"""Persistent storage and state management for Jira ticket attributions."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

from ai_telemetry_agent.attribution.detector import (
    detect_branch_ticket,
    extract_issue_keys,
    validate_ticket_key,
)
from ai_telemetry_agent.attribution.models import (
    JiraIssueNotFoundError,
    JiraUnconfiguredError,
    JiraUnreachableError,
    TicketAttribution,
    now_utc_iso,
)
from ai_telemetry_agent.config.settings import get_user_agent_home
from ai_telemetry_agent.core.repository import detect_repository_context
from ai_telemetry_agent.logging.structured import get_logger

logger = get_logger("telemetry.attribution.store")


def _normalize_path_key(path_val: Optional[Union[str, Path]]) -> str:
    """Normalize directory path for consistent dictionary key lookup across OS platforms."""
    if not path_val:
        return ""
    p = Path(path_val).resolve()
    # On Windows lowercase for case-insensitivity consistency
    posix_str = p.as_posix()
    if os.name == "nt":
        posix_str = posix_str.lower()
    return posix_str


class AttributionStore:
    """Thread-safe persistent store for ticket attributions and working contexts.
    
    Persists data to ~/.telemetry_agent/data/ticket_attributions.json
    using atomic file replacement and backward-compatible schemas.
    """

    def __init__(self, data_dir: Optional[Union[str, Path]] = None):
        if data_dir is None:
            user_home = get_user_agent_home()
            self.data_dir = (user_home / "data").resolve()
        else:
            self.data_dir = Path(data_dir).resolve()

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = self.data_dir / "ticket_attributions.json"
        self._lock = threading.Lock()
        
        # State schema:
        # {
        #   "version": 1,
        #   "contexts": { repo_key: { "active_attribution_id": str, "proposed_key": str, "unassigned": bool, ... } },
        #   "attributions": [ { ...TicketAttribution.to_dict()... } ]
        # }
        self._contexts: Dict[str, Dict[str, Any]] = {}
        self._attributions: Dict[str, TicketAttribution] = {}
        self._load()

    def _load(self) -> None:
        """Load stored attributions from disk with backward compatibility."""
        with self._lock:
            if not self.file_path.is_file():
                self._contexts = {}
                self._attributions = {}
                return

            try:
                content = self.file_path.read_text(encoding="utf-8")
                if not content.strip():
                    self._contexts = {}
                    self._attributions = {}
                    return
                data = json.loads(content)
            except Exception as e:
                logger.warning("attribution_store_load_failed", error=str(e))
                self._contexts = {}
                self._attributions = {}
                return

            if not isinstance(data, dict):
                self._contexts = {}
                self._attributions = {}
                return

            self._contexts = data.get("contexts", {})
            self._attributions = {}
            for item in data.get("attributions", []):
                try:
                    attr = TicketAttribution.from_dict(item)
                    self._attributions[attr.id] = attr
                except Exception:
                    continue

    def _save(self) -> None:
        """Atomically write in-memory state to disk."""
        data = {
            "version": 1,
            "contexts": self._contexts,
            "attributions": [attr.to_dict() for attr in self._attributions.values()],
        }
        tmp_file = self.file_path.parent / f"{self.file_path.name}.tmp"
        try:
            tmp_file.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp_file.replace(self.file_path)
        except Exception as e:
            logger.error("attribution_store_save_failed", error=str(e))
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)

    def _resolve_repo(self, repo_path: Optional[Union[str, Path]] = None) -> Tuple[str, str, Optional[str]]:
        """Resolve repository key, repository name, and current branch.
        
        Returns:
            (repo_key, repo_name, current_branch)
        """
        ctx = detect_repository_context(repo_path)
        if ctx.is_git_repo and ctx.repository_root:
            repo_key = _normalize_path_key(ctx.repository_root)
            repo_name = ctx.repository_name or Path(ctx.repository_root).name
            branch = ctx.branch
        else:
            target = Path(repo_path).resolve() if repo_path else Path.cwd().resolve()
            repo_key = _normalize_path_key(target)
            repo_name = target.name
            branch = None
        return (repo_key, repo_name, branch)

    def get_active_attribution(
        self, repo_path: Optional[Union[str, Path]] = None
    ) -> Optional[TicketAttribution]:
        """Retrieve currently active confirmed ticket attribution for workspace."""
        repo_key, repo_name, _ = self._resolve_repo(repo_path)
        with self._lock:
            # Check context by normalized root key, or fallback to repo name
            ctx_data = self._contexts.get(repo_key)
            if not ctx_data:
                for rk, c in self._contexts.items():
                    if c.get("repository_name") == repo_name:
                        ctx_data = c
                        break

            if not ctx_data or ctx_data.get("unassigned"):
                return None

            attr_id = ctx_data.get("active_attribution_id")
            if not attr_id or attr_id not in self._attributions:
                return None

            attr = self._attributions[attr_id]
            if attr.end_time is not None:
                return None
            return attr

    def get_proposed_switch(
        self, repo_path: Optional[Union[str, Path]] = None
    ) -> Optional[str]:
        """Return proposed ticket switch key if a conflicting branch was detected."""
        repo_key, repo_name, _ = self._resolve_repo(repo_path)
        with self._lock:
            ctx_data = self._contexts.get(repo_key)
            if not ctx_data:
                for rk, c in self._contexts.items():
                    if c.get("repository_name") == repo_name:
                        ctx_data = c
                        break
            if not ctx_data:
                return None
            return ctx_data.get("proposed_key")

    def select_ticket(
        self,
        issue_key: str,
        repo_path: Optional[Union[str, Path]] = None,
        source: str = "manual",
        jira_adapter: Optional[Any] = None,
        allow_unverified: bool = False,
        validation_status: Optional[str] = None,
        validation_message: Optional[str] = None,
        issue_title: Optional[str] = None,
        issue_status: Optional[str] = None,
        confirmation_state: str = "confirmed",
    ) -> TicketAttribution:
        """Select and activate a Jira ticket for the repository working context.
        
        Args:
            issue_key: Jira ticket key (e.g. 'PROJ-148').
            repo_path: Optional repository path. Defaults to current working directory.
            source: Association source ('manual', 'branch', 'remembered').
            jira_adapter: Optional configured Jira API adapter for existence validation.
            allow_unverified: If True, allows unverified selection when Jira is unreachable.
            validation_status: Explicit validation status ('verified', 'unverified').
            validation_message: Informational validation note.
            issue_title: Issue summary/title if known.
            issue_status: Issue state (e.g. 'In Progress') if known.
            confirmation_state: User confirmation state ('confirmed', 'proposed').
            
        Returns:
            The created or updated TicketAttribution record.
            
        Raises:
            ValueError: If issue_key syntax is invalid.
            JiraIssueNotFoundError: If Jira API confirms the issue does not exist (HTTP 404).
            JiraUnreachableError: If Jira is configured but unreachable and allow_unverified is False.
        """
        is_valid, normalized_or_err = validate_ticket_key(issue_key)
        if not is_valid:
            raise ValueError(normalized_or_err)

        norm_key = normalized_or_err

        resolved_val_status = validation_status or "unverified"
        resolved_val_message = validation_message
        resolved_title = issue_title
        resolved_issue_status = issue_status

        # If a Jira adapter is supplied, validate existence BEFORE making any state changes
        if jira_adapter is not None:
            if hasattr(jira_adapter, "is_configured") and jira_adapter.is_configured():
                diag = jira_adapter.test_issue(norm_key)
                status = diag.get("status")
                if status == "success":
                    resolved_val_status = "verified"
                    resolved_val_message = diag.get("message")
                    resolved_title = diag.get("title") or resolved_title
                    resolved_issue_status = diag.get("issue_status") or resolved_issue_status
                elif status == "not_found":
                    raise JiraIssueNotFoundError(
                        key=norm_key,
                        message=diag.get("message", f"Issue '{norm_key}' was not found in Jira (HTTP 404)."),
                    )
                else:
                    # auth_error, permission_denied, network_error, timeout, error
                    if not allow_unverified:
                        raise JiraUnreachableError(
                            key=norm_key,
                            reason=diag.get("message", f"Jira verification failed ({status})."),
                            status=status or "error",
                        )
                    resolved_val_status = "unverified"
                    resolved_val_message = diag.get("message")
            else:
                if not allow_unverified:
                    raise JiraUnconfiguredError(
                        key=norm_key,
                        message=f"Jira integration is unconfigured (JIRA_BASE_URL is not set). Ticket existence for '{norm_key}' cannot be verified.",
                    )
                resolved_val_status = "unverified"
                resolved_val_message = "Jira integration is unconfigured (offline policy)"

        repo_key, repo_name, branch = self._resolve_repo(repo_path)
        now = now_utc_iso()

        with self._lock:
            ctx_data = self._contexts.setdefault(repo_key, {
                "repository_name": repo_name,
                "repository_root": repo_key,
                "active_attribution_id": None,
                "proposed_key": None,
                "last_branch": branch,
                "unassigned": False,
                "updated_at": now,
            })

            existing_id = ctx_data.get("active_attribution_id")
            if existing_id and existing_id in self._attributions:
                existing_attr = self._attributions[existing_id]
                if existing_attr.end_time is None:
                    if existing_attr.issue_key == norm_key:
                        # Already active with same key: update metadata and branch
                        if branch and existing_attr.branch != branch:
                            existing_attr.branch = branch
                        existing_attr.validation_status = resolved_val_status
                        if resolved_val_message:
                            existing_attr.validation_message = resolved_val_message
                        if resolved_title:
                            existing_attr.issue_title = resolved_title
                        if resolved_issue_status:
                            existing_attr.issue_status = resolved_issue_status
                        existing_attr.updated_at = now
                        ctx_data["proposed_key"] = None
                        ctx_data["unassigned"] = False
                        ctx_data["updated_at"] = now
                        self._save()
                        return existing_attr
                    else:
                        # Close previous attribution at current timestamp
                        existing_attr.end_time = now
                        existing_attr.updated_at = now

            # Create new attribution record
            attr_id = str(uuid.uuid4())
            new_attr = TicketAttribution(
                id=attr_id,
                issue_key=norm_key,
                repository_name=repo_name,
                repository_root=repo_key,
                branch=branch,
                source=source,
                start_time=now,
                end_time=None,
                confirmation_state=confirmation_state,
                validation_status=resolved_val_status,
                validation_message=resolved_val_message,
                issue_title=resolved_title,
                issue_status=resolved_issue_status,
                created_at=now,
                updated_at=now,
            )

            self._attributions[attr_id] = new_attr
            ctx_data["active_attribution_id"] = attr_id
            ctx_data["proposed_key"] = None
            ctx_data["last_branch"] = branch
            ctx_data["unassigned"] = False
            ctx_data["updated_at"] = now

            self._save()
            return new_attr

    def clear_ticket(self, repo_path: Optional[Union[str, Path]] = None) -> None:
        """Clear active Jira ticket association and explicitly mark workspace work as unassigned."""
        repo_key, repo_name, branch = self._resolve_repo(repo_path)
        now = now_utc_iso()

        with self._lock:
            ctx_data = self._contexts.setdefault(repo_key, {
                "repository_name": repo_name,
                "repository_root": repo_key,
                "active_attribution_id": None,
                "proposed_key": None,
                "last_branch": branch,
                "unassigned": True,
                "updated_at": now,
            })

            existing_id = ctx_data.get("active_attribution_id")
            if existing_id and existing_id in self._attributions:
                existing_attr = self._attributions[existing_id]
                if existing_attr.end_time is None:
                    existing_attr.end_time = now
                    existing_attr.updated_at = now

            ctx_data["active_attribution_id"] = None
            ctx_data["proposed_key"] = None
            ctx_data["unassigned"] = True
            ctx_data["updated_at"] = now

            self._save()

    def confirm_switch(
        self,
        repo_path: Optional[Union[str, Path]] = None,
        jira_adapter: Optional[Any] = None,
        allow_unverified: bool = False,
    ) -> Optional[TicketAttribution]:
        """Confirm a proposed ticket switch detected from a branch switch."""
        repo_key, repo_name, branch = self._resolve_repo(repo_path)
        with self._lock:
            ctx_data = self._contexts.get(repo_key)
            if not ctx_data:
                return None

            proposed = ctx_data.get("proposed_key")
            if not proposed:
                return None

        # Call select_ticket with proposed key and jira_adapter for existence validation
        return self.select_ticket(
            issue_key=proposed,
            repo_path=repo_path,
            source="branch",
            jira_adapter=jira_adapter,
            allow_unverified=allow_unverified,
        )

    def sync_branch(
        self, repo_path: Optional[Union[str, Path]] = None
    ) -> Tuple[Optional[TicketAttribution], Optional[str]]:
        """Synchronize repository branch state with ticket attribution rules.
        
        Behavior:
        1. When branch contains exactly 1 valid key:
           - If active ticket agrees: keep association, update branch.
           - If active ticket is different: suggest switch, set proposed_key (requires confirmation).
           - If no active ticket exists: auto-associate with branch key.
        2. When branch contains no key (e.g. main/master):
           - Never erase an existing valid association.
        3. When branch contains conflicting keys:
           - Do not guess; leave proposed empty and require explicit selection.
           
        Returns:
            (active_attribution, proposed_key)
        """
        detection = detect_branch_ticket(repo_path)
        repo_key, repo_name, branch = self._resolve_repo(repo_path)
        active = self.get_active_attribution(repo_path)

        if detection.status == "single" and detection.suggested_key:
            branch_key = detection.suggested_key
            if active:
                if active.issue_key == branch_key:
                    # Agrees: keep active, clear any pending proposed switch
                    with self._lock:
                        ctx_data = self._contexts.get(repo_key)
                        if ctx_data:
                            ctx_data["proposed_key"] = None
                            if branch and active.branch != branch:
                                active.branch = branch
                                active.updated_at = now_utc_iso()
                            self._save()
                    return (active, None)
                else:
                    # Different key: propose switch, do NOT overwrite active
                    with self._lock:
                        ctx_data = self._contexts.setdefault(repo_key, {
                            "repository_name": repo_name,
                            "repository_root": repo_key,
                            "active_attribution_id": active.id,
                            "proposed_key": branch_key,
                            "last_branch": branch,
                            "unassigned": False,
                            "updated_at": now_utc_iso(),
                        })
                        ctx_data["proposed_key"] = branch_key
                        ctx_data["last_branch"] = branch
                        ctx_data["updated_at"] = now_utc_iso()
                        self._save()
                    return (active, branch_key)
            else:
                # No active association: auto-associate with detected branch key
                # Check if explicitly unassigned
                with self._lock:
                    ctx_data = self._contexts.get(repo_key)
                    is_unassigned = ctx_data.get("unassigned", False) if ctx_data else False

                if not is_unassigned:
                    new_attr = self.select_ticket(branch_key, repo_path=repo_path, source="branch")
                    return (new_attr, None)
                else:
                    # Explicitly unassigned: propose branch key without forcing
                    with self._lock:
                        if ctx_data:
                            ctx_data["proposed_key"] = branch_key
                            self._save()
                    return (None, branch_key)

        elif detection.status == "none":
            # Main / master or branch without key: preserve existing active association
            if active and branch and active.branch != branch:
                with self._lock:
                    active.branch = branch
                    active.updated_at = now_utc_iso()
                    self._save()
            return (active, None)

        elif detection.status == "conflict":
            # Multiple conflicting keys: do not guess
            return (active, None)

        return (active, None)

    def find_attribution_for_event(
        self,
        repo_name: Optional[str],
        timestamp: str,
        repo_root: Optional[str] = None,
    ) -> Optional[TicketAttribution]:
        """Find the confirmed ticket attribution interval active at a given event timestamp."""
        if not timestamp:
            return None

        # Clean timestamp for ISO comparison
        clean_ts = timestamp
        norm_root = _normalize_path_key(repo_root) if repo_root else None

        with self._lock:
            # Sort attributions newest start_time first so latest or active intervals take precedence
            candidates = sorted(
                self._attributions.values(),
                key=lambda a: a.start_time,
                reverse=True,
            )
            for attr in candidates:
                if attr.confirmation_state != "confirmed":
                    continue

                # Match repository
                matches_repo = False
                if norm_root and attr.repository_root and _normalize_path_key(attr.repository_root) == norm_root:
                    matches_repo = True
                elif repo_name and attr.repository_name and attr.repository_name.lower() == repo_name.lower():
                    matches_repo = True

                if not matches_repo:
                    continue

                # Check time boundary:
                # Active attribution (end_time is None): [start_time, inf)
                # Closed attribution: [start_time, end_time)
                if attr.start_time <= clean_ts:
                    if attr.end_time is None or clean_ts < attr.end_time:
                        return attr

        return None

    def get_recent_tickets(
        self,
        repo_path: Optional[Union[str, Path]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """List recently associated tickets with repository and timestamp details."""
        repo_key = _normalize_path_key(repo_path) if repo_path else None
        with self._lock:
            items: List[TicketAttribution] = list(self._attributions.values())

        if repo_key:
            items = [a for a in items if a.repository_root and _normalize_path_key(a.repository_root) == repo_key]

        # Group by issue key, take latest updated_at
        by_key: Dict[str, TicketAttribution] = {}
        for a in items:
            k = a.issue_key
            if k not in by_key or a.updated_at > by_key[k].updated_at:
                by_key[k] = a

        sorted_attrs = sorted(by_key.values(), key=lambda a: a.updated_at, reverse=True)[:limit]
        return [
            {
                "issue_key": a.issue_key,
                "repository": a.repository_name,
                "branch": a.branch,
                "source": a.source,
                "confirmation_state": a.confirmation_state,
                "last_active": a.updated_at,
                "is_active": a.end_time is None,
            }
            for a in sorted_attrs
        ]

    def get_all_attributions(self) -> List[TicketAttribution]:
        """Return a copy of all recorded ticket attributions."""
        with self._lock:
            return list(self._attributions.values())
