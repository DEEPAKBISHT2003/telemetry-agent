"""Git repository context detection and metadata extraction."""

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Dict, Optional

from ai_telemetry_agent.logging.structured import get_logger

logger = get_logger("telemetry.repository")

# Regex to strip credentials from git remote URLs (e.g., https://token@github.com/... or https://user:pass@...)
CREDENTIAL_URL_PATTERN = re.compile(r"https?://([^/@:]+(:[^/@]+)?@)", re.IGNORECASE)


def sanitize_remote_url(raw_url: Optional[str]) -> Optional[str]:
    """Sanitize a git remote URL by removing embedded usernames, passwords, or tokens."""
    if not raw_url or not isinstance(raw_url, str):
        return None
    url = raw_url.strip()
    return CREDENTIAL_URL_PATTERN.sub("https://", url)


@dataclass
class RepositoryContext:
    """Represents the Git repository context of a developer workspace."""
    repository_name: Optional[str] = None
    repository_root: Optional[str] = None
    branch: Optional[str] = None
    remote_url: Optional[str] = None
    is_git_repo: bool = False

    def to_dict(self, include_local_root: bool = True) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name": self.repository_name,
            "branch": self.branch,
            "remote_url": self.remote_url,
            "is_git_repo": self.is_git_repo,
        }
        if include_local_root:
            d["root"] = self.repository_root
        return {k: v for k, v in d.items() if v is not None}


def _run_git_command(cwd: Path, args: list) -> Optional[str]:
    try:
        res = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3.0,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout.strip()
        return None
    except Exception:
        return None


def detect_repository_context(target_path: Optional[str | Path] = None) -> RepositoryContext:
    """Detect Git repository context for a target directory or file.

    Executes lightweight, read-only git commands directly in the specified target path.
    Does NOT scan the entire filesystem.

    Args:
        target_path: Directory or file path. Defaults to current working directory.

    Returns:
        RepositoryContext containing repository_name, repository_root, branch, and sanitized remote_url.
    """
    if target_path:
        path = Path(target_path).resolve()
        if path.is_file():
            path = path.parent
    else:
        path = Path.cwd().resolve()

    if not path.exists():
        return RepositoryContext(is_git_repo=False)

    # 1. Verify if inside a git work tree
    is_work_tree = _run_git_command(path, ["rev-parse", "--is-inside-work-tree"])
    if is_work_tree != "true":
        # Fallback: if not a git repo, return directory name as workspace name
        return RepositoryContext(
            repository_name=path.name,
            repository_root=str(path),
            branch=None,
            remote_url=None,
            is_git_repo=False,
        )

    # 2. Get repository root
    repo_root_str = _run_git_command(path, ["rev-parse", "--show-toplevel"])
    if not repo_root_str:
        return RepositoryContext(
            repository_name=path.name,
            repository_root=str(path),
            branch=None,
            remote_url=None,
            is_git_repo=False,
        )

    repo_root = Path(repo_root_str).resolve()
    repo_name = repo_root.name

    # 3. Get current branch
    branch = _run_git_command(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    if branch == "HEAD":
        # Detached HEAD, fallback to short commit SHA
        branch = _run_git_command(repo_root, ["rev-parse", "--short", "HEAD"]) or "DETACHED"

    # 4. Get remote origin URL (sanitized)
    raw_remote = _run_git_command(repo_root, ["config", "--get", "remote.origin.url"])
    sanitized_remote = sanitize_remote_url(raw_remote)

    return RepositoryContext(
        repository_name=repo_name,
        repository_root=str(repo_root),
        branch=branch,
        remote_url=sanitized_remote,
        is_git_repo=True,
    )
