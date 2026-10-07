"""Event processing pipeline: validation, normalization, identity/context attachment, and privacy sanitization."""

import datetime
import uuid
from typing import Any, Dict, Optional, Tuple

from src.core.event import CollectorInfo, ContextInfo, IdentityInfo, RepositoryInfo, TelemetryEvent
from src.core.identity import IdentityProvider
from src.core.validation import ValidationError, is_valid_iso8601, validate_event
from src.logging.structured import get_logger, sanitize_data

logger = get_logger("telemetry.processor")


class EventProcessor:
    """Processes raw event inputs through a robust validation, normalization, and sanitization pipeline."""

    def __init__(
        self,
        identity_provider: IdentityProvider,
        collector_version: str = "0.1.0",
        default_context: Optional[ContextInfo] = None,
    ):
        self.identity_provider = identity_provider
        self.collector_version = collector_version
        self.default_context = default_context or ContextInfo()

    def process_raw(
        self,
        raw_event: Dict[str, Any],
        context_override: Optional[ContextInfo] = None,
    ) -> Optional[TelemetryEvent]:
        """Process, validate, normalize, and construct a standardized TelemetryEvent.

        Returns:
            TelemetryEvent if valid, or None if the raw event is rejected.
        """
        try:
            if not isinstance(raw_event, dict):
                logger.error(
                    "processor_rejected_event",
                    reason="raw_event_not_a_dict",
                    received_type=type(raw_event).__name__,
                )
                return None

            event_type = raw_event.get("event_type")
            if not event_type or not isinstance(event_type, str) or not event_type.strip():
                logger.error(
                    "processor_rejected_event",
                    reason="missing_or_invalid_event_type",
                    raw_event=str(raw_event)[:200],
                )
                return None
            event_type = event_type.strip()

            # 1. Event ID Generation (if not supplied or empty)
            event_id = raw_event.get("event_id")
            if not event_id or not isinstance(event_id, str) or not event_id.strip():
                event_id = str(uuid.uuid4())
            else:
                event_id = event_id.strip()

            # 2. Timestamp Normalization (default to current UTC in ISO-8601)
            raw_timestamp = raw_event.get("timestamp")
            if raw_timestamp and is_valid_iso8601(raw_timestamp):
                timestamp = raw_timestamp
                if timestamp.endswith("+00:00"):
                    timestamp = timestamp[:-6] + "Z"
            else:
                timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            # 3. Identity Attachment
            member_id = self.identity_provider.get_current_member()
            device_id = self.identity_provider.get_device_id()

            collector_info = CollectorInfo(
                version=self.collector_version,
                device_id=device_id,
            )
            identity_info = IdentityInfo(
                member_id=member_id,
            )

            # 4. Context Attachment & Repository Resolution
            ctx = context_override or self.default_context
            raw_ctx = raw_event.get("context", {})
            if isinstance(raw_ctx, dict):
                session_id = raw_ctx.get("session_id") or ctx.session_id
                project_id = raw_ctx.get("project_id") or ctx.project_id
                jira_task_id = raw_ctx.get("jira_task_id") or ctx.jira_task_id
                raw_repo = raw_ctx.get("repository")
            else:
                session_id = ctx.session_id
                project_id = ctx.project_id
                jira_task_id = ctx.jira_task_id
                raw_repo = None

            # Resolve RepositoryInfo
            repo_info = None
            if isinstance(raw_repo, dict):
                repo_info = RepositoryInfo(
                    name=raw_repo.get("name"),
                    branch=raw_repo.get("branch"),
                    root=raw_repo.get("root"),
                    remote_url=raw_repo.get("remote_url"),
                    is_git_repo=raw_repo.get("is_git_repo", True),
                )
            elif isinstance(raw_repo, RepositoryInfo):
                repo_info = raw_repo
            elif ctx.repository:
                repo_info = ctx.repository

            # If still None, check if payload has repository / branch (e.g. from GitSource)
            raw_payload = raw_event.get("payload", {})
            if not repo_info and isinstance(raw_payload, dict):
                p_repo = raw_payload.get("repository") or raw_payload.get("repository_name")
                p_branch = raw_payload.get("branch") or raw_payload.get("current_branch")
                p_root = raw_payload.get("repository_path") or raw_payload.get("repository_root")
                if p_repo:
                    repo_info = RepositoryInfo(
                        name=p_repo,
                        branch=p_branch,
                        root=p_root,
                        is_git_repo=True,
                    )

            context_info = ContextInfo(
                session_id=session_id,
                project_id=project_id,
                jira_task_id=jira_task_id,
                repository=repo_info,
            )

            # 5. Payload Normalization & Privacy Sanitization
            if not isinstance(raw_payload, dict):
                raw_payload = {"raw_value": str(raw_payload)}

            clean_payload = sanitize_data(raw_payload)

            event = TelemetryEvent(
                event_id=event_id,
                event_type=event_type,
                timestamp=timestamp,
                collector=collector_info,
                identity=identity_info,
                context=context_info,
                payload=clean_payload,
            )

            # 6. Final Schema Validation
            is_valid, errors = validate_event(event)
            if not is_valid:
                logger.error(
                    "processor_rejected_event",
                    event_id=event_id,
                    event_type=event_type,
                    errors="; ".join(errors),
                )
                return None

            return event

        except Exception as ex:
            logger.error(
                "processor_exception",
                error=str(ex),
                raw_event=str(raw_event)[:200],
            )
            return None
