"""Tests for GitSource detection, branch switching, and commit tracking."""

import os
from pathlib import Path
import subprocess
import pytest
from src.core.event import EventType
from src.sources.git import GitSource


@pytest.fixture
def test_git_repo(tmp_path):
    repo_dir = tmp_path / "test_repo"
    repo_dir.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Dev"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=str(repo_dir), check=True, capture_output=True)

    # Create initial commit
    dummy_file = repo_dir / "file.txt"
    dummy_file.write_text("hello world", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(repo_dir), check=True, capture_output=True)

    return repo_dir


def test_git_source_initial_detection(test_git_repo):
    source = GitSource(watch_paths=[str(test_git_repo)], poll_interval_seconds=0)
    source.start()

    events = source.collect()
    assert len(events) == 1
    assert events[0]["event_type"] == EventType.GIT_REPOSITORY_DETECTED
    assert events[0]["payload"]["repository"] == "test_repo"
    assert events[0]["payload"]["head_commit"] is not None


def test_git_source_branch_change_and_commit_detection(test_git_repo):
    source = GitSource(watch_paths=[str(test_git_repo)], poll_interval_seconds=0)
    source.start()

    # 1. Initial collection
    events1 = source.collect()
    assert len(events1) == 1

    # 2. Switch branch
    subprocess.run(["git", "checkout", "-b", "feature/telemetry"], cwd=str(test_git_repo), check=True, capture_output=True)
    events2 = source.collect()
    assert len(events2) == 1
    assert events2[0]["event_type"] == EventType.GIT_BRANCH_CHANGED
    assert events2[0]["payload"]["current_branch"] == "feature/telemetry"

    # 3. Create a new commit
    test_file = test_git_repo / "new_feature.txt"
    test_file.write_text("feature content", encoding="utf-8")
    subprocess.run(["git", "add", "new_feature.txt"], cwd=str(test_git_repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add feature telemetry"], cwd=str(test_git_repo), check=True, capture_output=True)

    events3 = source.collect()
    assert len(events3) == 1
    assert events3[0]["event_type"] == EventType.GIT_COMMIT_DETECTED
    assert events3[0]["payload"]["branch"] == "feature/telemetry"
    assert events3[0]["payload"]["message"] == "Add feature telemetry"
    assert events3[0]["payload"]["author"] == "Test Dev"
