"""Persistent Jira Ticket Attribution Subsystem."""

from ai_telemetry_agent.attribution.aggregator import TicketAggregator
from ai_telemetry_agent.attribution.detector import (
    DEFAULT_JIRA_KEY_PATTERN,
    detect_branch_ticket,
    extract_issue_keys,
    validate_ticket_key,
)
from ai_telemetry_agent.attribution.jira_adapter import (
    GenericJiraRestAdapter,
    JiraAdapter,
    JiraIssueInfo,
    NullJiraAdapter,
    get_jira_adapter_from_env,
)
from ai_telemetry_agent.attribution.models import (
    BranchDetectionResult,
    TicketAttribution,
    TicketSummary,
)
from ai_telemetry_agent.attribution.store import AttributionStore

__all__ = [
    "AttributionStore",
    "BranchDetectionResult",
    "DEFAULT_JIRA_KEY_PATTERN",
    "GenericJiraRestAdapter",
    "JiraAdapter",
    "JiraIssueInfo",
    "NullJiraAdapter",
    "TicketAggregator",
    "TicketAttribution",
    "TicketSummary",
    "detect_branch_ticket",
    "extract_issue_keys",
    "get_jira_adapter_from_env",
    "validate_ticket_key",
]
