"""Aggregation and reporting logic attributing AI sessions to Jira tickets."""

from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from ai_telemetry_agent.attribution.jira_adapter import JiraAdapter, NullJiraAdapter
from ai_telemetry_agent.attribution.models import TicketSummary
from ai_telemetry_agent.attribution.store import AttributionStore
from ai_telemetry_agent.core.validation import is_valid_iso8601
from ai_telemetry_agent.storage.base import EventStore


class TicketAggregator:
    """Aggregates event-level telemetry and attributes usage to Jira tickets."""

    def __init__(
        self,
        event_store: EventStore,
        attribution_store: AttributionStore,
        jira_adapter: Optional[JiraAdapter] = None,
        hook_registry: Optional[Any] = None,
    ):
        self.event_store = event_store
        self.attribution_store = attribution_store
        self.jira_adapter = jira_adapter or NullJiraAdapter()
        self.hook_registry = hook_registry

    def get_ticket_summaries(
        self,
        repository_name: Optional[str] = None,
        ticket_key: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, TicketSummary]:
        """Aggregate event-level telemetry and group by Jira issue key.
        
        Guarantees:
        - Event-level granularity: Each event is attributed by its own timestamp
          to the half-open attribution interval [start_time, end_time).
        - Boundary precision: A timestamp exactly at a ticket switch belongs to the new ticket.
        - Strict credit conservation: Total attributed credits + unassigned credits
          equal total source event credits exactly (within floating point precision).
        - Run deduplication: Completed runs are counted using run_id / event identity
          without double-counting run-start and run-completion events.
        - Multi-ticket sessions: Multi-ticket sessions list all engaged tickets, while
          unique sessions are tracked accurately.
        - Missing/invalid timestamps: Falls back to session start_time; if still invalid -> UNASSIGNED.
        - Unknown token metrics remain None rather than 0.
        - has_credit_data is True only when at least one event has measured numeric credit.
        
        Returns:
            Dictionary mapping issue key (or 'UNASSIGNED') to TicketSummary.
        """
        # Retrieve events grouped by session
        session_events_map = self.event_store.get_events_by_session(
            repository_name=repository_name,
            date_from=date_from,
            date_to=date_to,
        )

        # Also get session metadata (for duration, branch, repo info, and start_time fallback)
        sessions_metadata = self.event_store.get_sessions(
            repository_name=repository_name,
            limit=10000,
        )
        sess_meta_by_id: Dict[str, Dict[str, Any]] = {
            s["session_id"]: s for s in sessions_metadata if s.get("session_id")
        }

        # Load all attributions to extract metadata (source, state)
        all_attributions = self.attribution_store.get_all_attributions()
        attr_by_key: Dict[str, Any] = {}
        for a in all_attributions:
            k = a.issue_key
            if k not in attr_by_key or a.updated_at > attr_by_key[k].updated_at:
                attr_by_key[k] = a

        summaries: Dict[str, TicketSummary] = {}
        seen_runs_by_group: Dict[str, set] = {}

        for sid, events in session_events_map.items():
            s_meta = sess_meta_by_id.get(sid, {})
            sess_started_at = s_meta.get("started_at")

            # Sort session events chronologically
            def _event_sort_key(ev):
                ts = ev.timestamp
                if ts and is_valid_iso8601(ts):
                    return ts
                if sess_started_at and is_valid_iso8601(sess_started_at):
                    return sess_started_at
                return ""

            events.sort(key=_event_sort_key)

            # Determine tickets engaged by this session to distribute duration
            session_ticket_groups: set = set()

            for ev in events:
                repo_info = ev.context.repository if ev.context else None
                repo_name = repo_info.name if repo_info else (s_meta.get("repository_name") or repository_name)
                repo_root = repo_info.root if repo_info else None
                branch = repo_info.branch if repo_info else s_meta.get("branch")

                # Resolve timestamp with fallbacks
                ev_ts = ev.timestamp
                if not ev_ts or not is_valid_iso8601(ev_ts):
                    if sess_started_at and is_valid_iso8601(sess_started_at):
                        ev_ts = sess_started_at
                    else:
                        ev_ts = None

                # Attribute event
                assigned_ticket: Optional[str] = None
                source = "none"
                conf_state = "confirmed"

                if ev_ts:
                    attr = self.attribution_store.find_attribution_for_event(
                        repo_name=repo_name,
                        timestamp=ev_ts,
                        repo_root=repo_root,
                    )
                    if attr:
                        assigned_ticket = attr.issue_key
                        source = attr.source
                        conf_state = attr.confirmation_state

                ticket_group = assigned_ticket or "UNASSIGNED"

                # Filter by ticket_key if specified
                if ticket_key and ticket_group.upper() != ticket_key.upper():
                    continue

                session_ticket_groups.add(ticket_group)

                if ticket_group not in summaries:
                    title = None
                    status = None
                    if ticket_group != "UNASSIGNED":
                        issue_info = self.jira_adapter.get_issue(ticket_group)
                        if issue_info:
                            title = issue_info.title
                            status = issue_info.status
                        elif ticket_group in attr_by_key:
                            source = attr_by_key[ticket_group].source
                            conf_state = attr_by_key[ticket_group].confirmation_state

                    summaries[ticket_group] = TicketSummary(
                        issue_key=ticket_group,
                        title=title,
                        status=status,
                        source=source if ticket_group != "UNASSIGNED" else "none",
                        confirmation_state=conf_state if ticket_group != "UNASSIGNED" else "none",
                    )
                    seen_runs_by_group[ticket_group] = set()

                summary = summaries[ticket_group]

                if repo_name and repo_name not in summary.repositories:
                    summary.repositories.append(repo_name)
                if branch and branch not in summary.branches:
                    summary.branches.append(branch)

                p = ev.payload or {}
                etype = ev.event_type
                cm = p.get("capture_method")
                run_id = p.get("run_id") or ev.event_id

                # AI run counting:
                # Count completions or SQLite generation records, deduplicated by run_id
                if etype in ("ai_run_completed", "ai_run") or cm == "sqlite_gen_metadata":
                    if run_id not in seen_runs_by_group[ticket_group]:
                        seen_runs_by_group[ticket_group].add(run_id)
                        summary.ai_runs_count += 1
                elif etype == "ai_run_started":
                    # Do not count ai_run_started as a completion run
                    pass

                is_hook = bool(cm == "kiro_hooks" or (p.get("metadata") and p["metadata"].get("hook_trigger")))
                if is_hook:
                    summary.hook_executions_count += 1

                # Credit aggregation: exactly aggregate numeric credits
                creds = p.get("credits")
                if creds is not None and isinstance(creds, (int, float)):
                    summary.has_credit_data = True
                    summary.total_credits = round(summary.total_credits + float(creds), 4)
                    if p.get("credit_unit"):
                        summary.credit_unit = p.get("credit_unit")

                # Token aggregation: accumulate only when token fields are present
                in_tok = p.get("input_tokens")
                out_tok = p.get("output_tokens")
                cache_tok = p.get("cache_read_tokens") if p.get("cache_read_tokens") is not None else p.get("cache_tokens")
                tot_tok = p.get("total_tokens")
                has_tokens = any(isinstance(v, (int, float)) for v in (in_tok, out_tok, cache_tok, tot_tok))

                if has_tokens:
                    summary.has_token_data = True
                    if isinstance(in_tok, (int, float)):
                        summary.input_tokens = (summary.input_tokens or 0) + int(in_tok)
                    if isinstance(out_tok, (int, float)):
                        summary.output_tokens = (summary.output_tokens or 0) + int(out_tok)
                    if isinstance(cache_tok, (int, float)):
                        summary.cache_read_tokens = (summary.cache_read_tokens or 0) + int(cache_tok)
                    if isinstance(tot_tok, (int, float)):
                        summary.total_tokens = (summary.total_tokens or 0) + int(tot_tok)
                    elif isinstance(in_tok, (int, float)) or isinstance(out_tok, (int, float)):
                        summary.total_tokens = (summary.total_tokens or 0) + int(in_tok or 0) + int(cache_tok or 0) + int(out_tok or 0)

            # Record session engagement for each ticket that saw events in this session.
            # Ticket engagement uses the session's wall-clock activity span
            # (first-to-last event), which reflects how long the developer was
            # engaged with the session -- NOT the validated AI operation
            # duration. Falls back to operation duration, then 0, for sessions
            # where the activity span is unavailable.
            sess_duration_raw = s_meta.get("session_activity_span_seconds")
            if sess_duration_raw is None:
                sess_duration_raw = s_meta.get("duration_seconds")
            sess_duration = float(sess_duration_raw) if sess_duration_raw is not None else 0.0
            # If session engaged multiple tickets, distribute duration equally across tickets
            dur_per_ticket = round(sess_duration / len(session_ticket_groups), 2) if session_ticket_groups else 0.0

            for tg in session_ticket_groups:
                if tg in summaries:
                    s_obj = summaries[tg]
                    if sid not in s_obj.session_ids:
                        s_obj.session_ids.append(sid)
                        s_obj.sessions_count += 1
                        s_obj.duration_seconds = round(s_obj.duration_seconds + dur_per_ticket, 2)

        # Merge hook execution records from hook_registry if available
        if self.hook_registry:
            h_execs = self.hook_registry.get_executions(limit=10000)
            seen_hook_op_ids: Set[str] = set()
            for tg, r_set in seen_runs_by_group.items():
                seen_hook_op_ids.update(r_set)

            for h in h_execs:
                t_key = h.jira_key or "UNASSIGNED"
                if ticket_key and t_key.upper() != ticket_key.upper():
                    continue

                if t_key not in summaries:
                    continue

                s_obj = summaries[t_key]
                if h.resulting_operation_id and h.resulting_operation_id in seen_hook_op_ids:
                    # Already tracked run-completion event for this hook
                    if h.estimated_credits is not None:
                        s_obj.has_estimated_credits = True
                        s_obj.estimated_credits = round(s_obj.estimated_credits + float(h.estimated_credits), 4)
                        s_obj.estimated_credits_exact = str(s_obj.estimated_credits)
                    continue

                # Shell or standalone hook execution
                s_obj.hook_executions_count += 1
                if h.estimated_credits is not None:
                    s_obj.has_estimated_credits = True
                    s_obj.estimated_credits = round(s_obj.estimated_credits + float(h.estimated_credits), 4)
                    s_obj.estimated_credits_exact = str(s_obj.estimated_credits)

        return summaries
