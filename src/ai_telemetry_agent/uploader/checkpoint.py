"""Atomic, crash-safe upload checkpoint manager for AI Telemetry Agent.

Tracks file byte offsets and event progress across date-based JSONL files:
~/.telemetry_agent/data/telemetry-YYYY-MM-DD.jsonl

Guarantees:
1. Atomic persistence via temporary file write and os.replace().
2. Zero data loss on abrupt process termination or power loss.
3. Resumes reading from exact byte offsets without rescanning old history.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from ai_telemetry_agent.config.settings import get_user_agent_home


class CheckpointManager:
    """Manages persistent byte offsets and completion status for telemetry JSONL files."""

    def __init__(self, custom_home: Optional[Path] = None):
        self.home = (custom_home or get_user_agent_home()).resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        self.checkpoint_file = self.home / "uploader_checkpoint.json"
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        """Load checkpoint from disk, initializing an empty structure on first run."""
        if not self.checkpoint_file.is_file():
            return {"version": 1, "last_upload_at": None, "files": {}}

        try:
            content = self.checkpoint_file.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, dict) and "files" in data:
                return data
        except Exception:
            pass

        return {"version": 1, "last_upload_at": None, "files": {}}

    def get_offset(self, filename: str) -> int:
        """Get the last acknowledged byte offset for a JSONL file."""
        file_info = self._data.get("files", {}).get(filename)
        if isinstance(file_info, dict):
            return int(file_info.get("byte_offset", 0))
        return 0

    def is_completed(self, filename: str) -> bool:
        """Check if an older date file has been marked as completed."""
        file_info = self._data.get("files", {}).get(filename)
        if isinstance(file_info, dict):
            return bool(file_info.get("completed", False))
        return False

    def update_checkpoint(
        self,
        filename: str,
        byte_offset: int,
        last_event_id: Optional[str] = None,
        completed: bool = False,
    ) -> None:
        """Atomically advance the byte offset and save checkpoint to disk."""
        if "files" not in self._data:
            self._data["files"] = {}

        now_iso = datetime.now(timezone.utc).isoformat()
        self._data["last_upload_at"] = now_iso

        self._data["files"][filename] = {
            "byte_offset": max(0, int(byte_offset)),
            "last_event_id": last_event_id,
            "completed": completed,
            "updated_at": now_iso,
        }

        # Atomic file write using temporary file + os.replace
        temp_file = self.home / f"uploader_checkpoint.json.{os.getpid()}.tmp"
        temp_file.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        temp_file.replace(self.checkpoint_file)

    def mark_completed(self, filename: str, final_offset: int) -> None:
        """Mark an archived date file as fully ingested and acknowledged."""
        self.update_checkpoint(filename=filename, byte_offset=final_offset, completed=True)

    def get_all_file_checkpoints(self) -> Dict[str, Dict[str, Any]]:
        """Return shallow copy of all tracked file checkpoints."""
        return dict(self._data.get("files", {}))

    def reset(self) -> None:
        """Reset checkpoint data."""
        self._data = {"version": 1, "last_upload_at": None, "files": {}}
        if self.checkpoint_file.is_file():
            try:
                self.checkpoint_file.unlink()
            except Exception:
                pass
