"""Git event source for monitoring repository state, branch changes, and commits."""

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Dict, List, Optional

from ai_telemetry_agent.core.event import EventType
from ai_telemetry_agent.logging.structured import get_logger, sanitize_data
from ai_telemetry_agent.sources.base import EventSource

logger = get_logger("telemetry.source.git")


@dataclass
class GitRepoState:
    repo_path: str
    repo_name: str
    last_branch: Optional[str] = None
    last_commit_hash: Optional[str] = None
    initial_detection_done: bool = False


class GitSource(EventSource):
    """Monitors local git workspaces for branch switches and new commits using read-only operations."""

    def __init__(
        self,
        watch_paths: Optional[List[str]] = None,
        poll_interval_seconds: int = 10,
    ):
        self.watch_paths = [Path(p).resolve() for p in (watch_paths or ["."])]
        self.poll_interval_seconds = poll_interval_seconds
        self._last_poll_time = 0.0
        self._repo_states: Dict[str, GitRepoState] = {}
        self._started = False

    @property
    def name(self) -> str:
        return "git"

    def start(self) -> None:
        self._started = True
        self._last_poll_time = 0.0

    def stop(self) -> None:
        self._started = False

    def _run_git_cmd(self, repo_dir: Path, args: List[str]) -> Optional[str]:
        """Execute a read-only git command safely."""
        try:
            res = subprocess.run(
                ["git"] + args,
                cwd=str(repo_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5.0,
                check=False,
            )
            if res.returncode == 0:
                return res.stdout.strip()
            return None
        except Exception:
            return None

    def _inspect_repo(self, repo_dir: Path) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []

        # Check if inside git work tree
        is_git = self._run_git_cmd(repo_dir, ["rev-parse", "--is-inside-work-tree"])
        if is_git != "true":
            return events

        # Get repository root and name
        root_path_str = self._run_git_cmd(repo_dir, ["rev-parse", "--show-toplevel"])
        if not root_path_str:
            return events

        root_path = Path(root_path_str).resolve()
        repo_key = str(root_path)
        repo_name = root_path.name

        if repo_key not in self._repo_states:
            self._repo_states[repo_key] = GitRepoState(
                repo_path=repo_key,
                repo_name=repo_name,
            )

        state = self._repo_states[repo_key]

        # Get current branch
        branch = self._run_git_cmd(root_path, ["rev-parse", "--abbrev-ref", "HEAD"])
        if branch == "HEAD":
            # Detached HEAD, fallback to short SHA
            branch = self._run_git_cmd(root_path, ["rev-parse", "--short", "HEAD"]) or "DETACHED"

        # Get latest commit info (SHA, Author Name, Email, Timestamp, Subject)
        commit_raw = self._run_git_cmd(
            root_path,
            ["log", "-1", "--format=%H%x00%an%x00%ae%x00%ct%x00%s"]
        )

        commit_hash = None
        commit_author_name = None
        commit_timestamp = None
        commit_subject = None

        if commit_raw:
            parts = commit_raw.split("\x00")
            if len(parts) >= 5:
                commit_hash = parts[0]
                commit_author_name = parts[1]
                commit_timestamp = parts[3]
                commit_subject = parts[4]

        # 1. Initial repository detection event
        if not state.initial_detection_done:
            state.initial_detection_done = True
            state.last_branch = branch
            state.last_commit_hash = commit_hash

            events.append({
                "event_type": EventType.GIT_REPOSITORY_DETECTED,
                "payload": {
                    "repository": repo_name,
                    "repository_path": repo_key,
                    "branch": branch,
                    "head_commit": commit_hash,
                }
            })
            return events

        # 2. Check for branch change
        if branch and branch != state.last_branch:
            previous_branch = state.last_branch
            state.last_branch = branch
            events.append({
                "event_type": EventType.GIT_BRANCH_CHANGED,
                "payload": {
                    "repository": repo_name,
                    "repository_path": repo_key,
                    "previous_branch": previous_branch,
                    "current_branch": branch,
                }
            })

        # 3. Check for new commit
        if commit_hash and commit_hash != state.last_commit_hash:
            state.last_commit_hash = commit_hash
            events.append({
                "event_type": EventType.GIT_COMMIT_DETECTED,
                "payload": {
                    "repository": repo_name,
                    "branch": branch,
                    "commit_sha": commit_hash,
                    "author": commit_author_name,
                    "commit_time_epoch": commit_timestamp,
                    "message": sanitize_data(commit_subject),
                }
            })

        return events

    def collect(self) -> List[Dict[str, Any]]:
        if not self._started:
            return []

        now = time.time()
        if (now - self._last_poll_time) < self.poll_interval_seconds:
            return []

        self._last_poll_time = now
        events: List[Dict[str, Any]] = []

        for p in self.watch_paths:
            if p.exists():
                try:
                    repo_events = self._inspect_repo(p)
                    events.extend(repo_events)
                except Exception as ex:
                    logger.warning("git_poll_error", path=str(p), error=str(ex))

        return events
