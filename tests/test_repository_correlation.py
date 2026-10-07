"""Tests for Phase 2.5: Git repository detection, session correlation, and aggregations."""

import json
from pathlib import Path
import subprocess
import pytest

from src.core.event import EventType, RepositoryInfo
from src.core.repository import RepositoryContext, detect_repository_context, sanitize_remote_url
from src.integrations.hooks.hook_handler import process_hook
from src.storage.sqlite_store import SQLiteEventStore


def create_git_repo(path: Path, name: str, branch: str = "main", remote_url: str = None) -> Path:
    repo_dir = path / name
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", branch], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Dev"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=str(repo_dir), check=True, capture_output=True)

    test_file = repo_dir / "README.md"
    test_file.write_text(f"# {name}", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(repo_dir), check=True, capture_output=True)

    if remote_url:
        subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=str(repo_dir), check=True, capture_output=True)

    return repo_dir


def test_sanitize_remote_url():
    assert sanitize_remote_url("https://github.com/org/repo.git") == "https://github.com/org/repo.git"
    assert sanitize_remote_url("https://user:password123@github.com/org/repo.git") == "https://github.com/org/repo.git"
    assert sanitize_remote_url("https://ghp_secretToken12345@gitlab.com/group/project.git") == "https://gitlab.com/group/project.git"
    assert sanitize_remote_url(None) is None


def test_detect_repository_context(tmp_path):
    repo_dir = create_git_repo(
        tmp_path,
        name="bid-intelligence",
        branch="feature/P1-101",
        remote_url="https://oauth2:secret_token@github.com/company/bid-intelligence.git",
    )

    ctx = detect_repository_context(repo_dir)
    assert ctx.is_git_repo is True
    assert ctx.repository_name == "bid-intelligence"
    assert ctx.branch == "feature/P1-101"
    assert ctx.repository_root == str(repo_dir.resolve())
    assert ctx.remote_url == "https://github.com/company/bid-intelligence.git"


def test_detect_non_git_directory(tmp_path):
    non_git = tmp_path / "plain_folder"
    non_git.mkdir()

    ctx = detect_repository_context(non_git)
    assert ctx.is_git_repo is False
    assert ctx.repository_name == "plain_folder"
    assert ctx.branch is None


def test_hook_correlates_session_with_repository(tmp_path, monkeypatch):
    db_path = tmp_path / "test_corr.db"
    monkeypatch.setenv("LOCAL_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("MEMBER_ID", "M001")
    monkeypatch.setenv("DEVICE_ID", "DEV-001")

    repo_a = create_git_repo(tmp_path, name="chatbot-service", branch="feature/audio")

    # Simulate Antigravity hook with workspacePaths pointing to repo_a
    hook_input = {
        "conversationId": "session-chat-01",
        "workspacePaths": [str(repo_a)],
        "modelName": "gemini-3.7-flash",
        "stepIdx": 1,
    }

    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_input)))
    monkeypatch.setattr("sys.stdout", io.StringIO())

    process_hook("PreInvocation")

    store = SQLiteEventStore(str(db_path))
    events = store.get_events(limit=5)
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == EventType.AI_RUN_STARTED
    assert ev.context.session_id == "session-chat-01"
    assert ev.context.repository is not None
    assert ev.context.repository.name == "chatbot-service"
    assert ev.context.repository.branch == "feature/audio"


def test_multiple_repositories_isolation(tmp_path, monkeypatch):
    db_path = tmp_path / "test_multi_repo.db"
    monkeypatch.setenv("LOCAL_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("MEMBER_ID", "M001")
    monkeypatch.setenv("DEVICE_ID", "DEV-001")

    repo_1 = create_git_repo(tmp_path, name="repo-alpha", branch="main")
    repo_2 = create_git_repo(tmp_path, name="repo-beta", branch="feature/beta-v2")

    import io

    # Session 1 on Repo 1
    input_1 = {
        "conversationId": "sess-alpha-01",
        "workspacePaths": [str(repo_1)],
        "modelName": "gemini-3.7-flash",
        "stepIdx": 1,
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(input_1)))
    monkeypatch.setattr("sys.stdout", io.StringIO())
    process_hook("PreInvocation")

    # Session 2 on Repo 2
    input_2 = {
        "conversationId": "sess-beta-01",
        "workspacePaths": [str(repo_2)],
        "modelName": "gemini-3.7-flash",
        "stepIdx": 1,
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(input_2)))
    monkeypatch.setattr("sys.stdout", io.StringIO())
    process_hook("PreInvocation")

    # Session 3 on Repo 1
    input_3 = {
        "conversationId": "sess-alpha-02",
        "workspacePaths": [str(repo_1)],
        "modelName": "gemini-3.7-flash",
        "stepIdx": 1,
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(input_3)))
    monkeypatch.setattr("sys.stdout", io.StringIO())
    process_hook("PreInvocation")

    store = SQLiteEventStore(str(db_path))
    sessions = store.get_sessions()
    assert len(sessions) == 3

    # Check repository summary
    summary = store.get_repository_summary()
    assert summary["repo-alpha"]["total_sessions"] == 2
    assert summary["repo-beta"]["total_sessions"] == 1
    assert summary["repo-beta"]["branches"] == ["feature/beta-v2"]


def test_branch_switch_during_session(tmp_path, monkeypatch):
    db_path = tmp_path / "test_branch_switch.db"
    monkeypatch.setenv("LOCAL_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("MEMBER_ID", "M001")
    monkeypatch.setenv("DEVICE_ID", "DEV-001")

    repo = create_git_repo(tmp_path, name="dynamic-repo", branch="feature/branch-1")
    import io

    # Turn 1 on branch-1
    input_1 = {
        "conversationId": "sess-dyn-01",
        "workspacePaths": [str(repo)],
        "modelName": "gemini-3.7-flash",
        "stepIdx": 1,
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(input_1)))
    monkeypatch.setattr("sys.stdout", io.StringIO())
    process_hook("PreInvocation")

    # Switch branch
    subprocess.run(["git", "checkout", "-b", "feature/branch-2"], cwd=str(repo), check=True, capture_output=True)

    # Turn 2 on branch-2 (same session)
    input_2 = {
        "conversationId": "sess-dyn-01",
        "workspacePaths": [str(repo)],
        "modelName": "gemini-3.7-flash",
        "stepIdx": 2,
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(input_2)))
    monkeypatch.setattr("sys.stdout", io.StringIO())
    process_hook("PreInvocation")

    store = SQLiteEventStore(str(db_path))
    events = store.get_events(session_id="sess-dyn-01", descending=False)
    assert len(events) == 2
    assert events[0].context.repository.branch == "feature/branch-1"
    assert events[1].context.repository.branch == "feature/branch-2"

    # Verify session summary has updated latest branch
    sessions = store.get_sessions()
    assert sessions[0]["branch"] == "feature/branch-2"
