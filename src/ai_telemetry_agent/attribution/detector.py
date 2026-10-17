"""Automatic Git branch inspection and Jira issue key extraction."""

from pathlib import Path
import re
import subprocess
from typing import List, Optional, Tuple, Union

from ai_telemetry_agent.attribution.models import BranchDetectionResult
from ai_telemetry_agent.core.repository import detect_repository_context
from ai_telemetry_agent.logging.structured import get_logger

logger = get_logger("telemetry.attribution.detector")

# Standard Jira issue key pattern: Project key (starts with letter, 2+ alphanumerics) followed by hyphen and number
DEFAULT_JIRA_KEY_PATTERN = r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]+-[0-9]+)(?![A-Za-z0-9])"
STRICT_TICKET_KEY_REGEX = re.compile(r"^[A-Za-z][A-Za-z0-9]+-[0-9]+$")


def validate_ticket_key(key: str, pattern: Optional[str] = None) -> Tuple[bool, str]:
    """Validate Jira issue key syntax and return (is_valid, normalized_key_or_error_msg).
    
    Args:
        key: The user-supplied or detected issue key string.
        pattern: Optional custom regex pattern.
        
    Returns:
        Tuple of (True, normalized_uppercase_key) or (False, error_description).
    """
    if not key or not isinstance(key, str):
        return (False, "Ticket key cannot be empty.")
    
    stripped = key.strip()
    if not stripped:
        return (False, "Ticket key cannot be empty.")

    validator = re.compile(pattern) if pattern else STRICT_TICKET_KEY_REGEX
    if not validator.match(stripped):
        return (
            False,
            f"Invalid Jira ticket key '{stripped}'. "
            f"Expected format like 'PROJ-148' (letters/alphanumeric prefix, hyphen, and positive integer).",
        )

    return (True, stripped.upper())


def extract_issue_keys(text: Optional[str], pattern: Optional[str] = None) -> List[str]:
    """Extract all valid, normalized Jira issue keys from arbitrary text or branch name.
    
    Preserves unique order and normalizes keys to uppercase.
    """
    if not text or not isinstance(text, str):
        return []

    regex_str = pattern or DEFAULT_JIRA_KEY_PATTERN
    try:
        compiled = re.compile(regex_str)
    except re.error as e:
        logger.warning("invalid_jira_key_pattern", pattern=regex_str, error=str(e))
        compiled = re.compile(DEFAULT_JIRA_KEY_PATTERN)

    matches = compiled.findall(text)
    # Deduplicate while preserving first appearance order
    seen = set()
    result: List[str] = []
    for m in matches:
        k = m.upper()
        if k not in seen:
            seen.add(k)
            result.append(k)
    return result


def detect_branch_ticket(
    target_path: Optional[Union[str, Path]] = None,
    pattern: Optional[str] = None,
) -> BranchDetectionResult:
    """Detect Jira ticket from current branch in a local git repository workspace.
    
    Gracefully handles environments without git, non-git directories, or git command failures.
    
    Args:
        target_path: Path to workspace directory or file. Defaults to current working directory.
        pattern: Optional custom regex pattern for issue keys.
        
    Returns:
        BranchDetectionResult detailing detected keys, suggestion, and detection status.
    """
    try:
        repo_ctx = detect_repository_context(target_path)
    except Exception as e:
        logger.debug("repo_detection_exception", error=str(e))
        return BranchDetectionResult(
            keys=[],
            suggested_key=None,
            status="error",
            branch=None,
            error=str(e),
        )

    if not repo_ctx.is_git_repo:
        return BranchDetectionResult(
            keys=[],
            suggested_key=None,
            status="none",
            branch=None,
            error="Not a git repository checkout.",
        )

    branch = repo_ctx.branch
    if not branch or branch in ("HEAD", "DETACHED"):
        return BranchDetectionResult(
            keys=[],
            suggested_key=None,
            status="none",
            branch=branch,
        )

    keys = extract_issue_keys(branch, pattern=pattern)

    if len(keys) == 1:
        return BranchDetectionResult(
            keys=keys,
            suggested_key=keys[0],
            status="single",
            branch=branch,
        )
    elif len(keys) > 1:
        return BranchDetectionResult(
            keys=keys,
            suggested_key=None,
            status="conflict",
            branch=branch,
            error=f"Multiple conflicting Jira issue keys detected in branch '{branch}': {', '.join(keys)}",
        )
    else:
        return BranchDetectionResult(
            keys=[],
            suggested_key=None,
            status="none",
            branch=branch,
        )
