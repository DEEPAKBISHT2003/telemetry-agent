"""Read-only Antigravity SQLite conversation database and Protobuf telemetry extractor.

Discovers local conversation SQLite databases in %USERPROFILE%/.gemini/antigravity-ide/conversations/,
opens them strictly read-only, decodes LLM generation protobuf metadata from `gen_metadata`, and
extracts exact, non-negative token counts (input, cached read, output, thinking, response, context).
"""

from dataclasses import asdict, dataclass
import datetime
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import uuid

from ai_telemetry_agent.core.event import AITelemetryPayload, EventType
from ai_telemetry_agent.logging.structured import get_logger, sanitize_data

logger = get_logger("telemetry.source.antigravity_sqlite")

# Max safe varint (63-bit signed integer limit to reject 2^64-1 sentinel overflows)
MAX_SAFE_VARINT = (1 << 63) - 1


def generate_generation_event_id(record: "GenerationRecord") -> str:
    """Derive deterministic UUIDv5 event ID for a GenerationRecord.

    Composite key combines conversation UUID, row index, generation timestamp,
    raw BLOB size, and model identifier. Re-reading identical generations produces
    the identical event ID, while index reuse with new content produces a distinct ID.
    """
    ts_key = record.timestamp or "no_ts"
    model_key = record.model_id or "no_model"
    composite = (
        f"antigravity://gen_metadata/{record.conversation_id}/{record.idx}/{ts_key}/{record.raw_size}/{model_key}"
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, composite))


def record_to_raw_event(
    record: "GenerationRecord",
    repository: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Convert a GenerationRecord into a standardized raw AI telemetry event dictionary."""
    meta: Dict[str, Any] = {
        "gen_idx": record.idx,
        "reconciled": record.is_reconciled,
    }
    if getattr(record, "source_variant", None):
        meta["source_variant"] = record.source_variant
    if record.thinking_tokens is not None:
        meta["thinking_tokens"] = record.thinking_tokens
    if record.response_tokens is not None:
        meta["response_tokens"] = record.response_tokens
    if record.context_tokens is not None:
        meta["context_tokens"] = record.context_tokens
    if record.context_limit is not None:
        meta["context_limit"] = record.context_limit
    if record.model_display_name:
        meta["model_display"] = record.model_display_name

    payload = AITelemetryPayload(
        provider="antigravity",
        model=record.model_id,
        session_id=record.conversation_id,
        input_tokens=record.non_cached_input,
        cache_read_tokens=record.cached_input,
        output_tokens=record.total_output,
        total_tokens=record.total_tokens,
        cost_usd=None,
        capture_method="sqlite_gen_metadata",
        metadata=sanitize_data(meta),
    )

    ctx: Dict[str, Any] = {"session_id": record.conversation_id}
    if repository:
        ctx["repository"] = repository

    raw_dict: Dict[str, Any] = {
        "event_id": generate_generation_event_id(record),
        "event_type": EventType.AI_RUN,
        "context": ctx,
        "payload": payload.to_dict(),
    }
    if record.timestamp:
        raw_dict["timestamp"] = record.timestamp
    return raw_dict



@dataclass
class GenerationRecord:
    """Authoritative, typed record of a single LLM generation extracted from gen_metadata."""

    idx: int
    conversation_id: str
    model_id: Optional[str] = None
    model_display_name: Optional[str] = None
    non_cached_input: Optional[int] = None
    cached_input: Optional[int] = None
    total_output: Optional[int] = None
    thinking_tokens: Optional[int] = None
    response_tokens: Optional[int] = None
    context_tokens: Optional[int] = None
    context_limit: Optional[int] = None
    timestamp: Optional[str] = None
    is_reconciled: bool = False
    raw_size: int = 0
    source_variant: str = "ide"

    @property
    def total_tokens(self) -> Optional[int]:
        """Calculate total tokens only when components are available."""
        if self.non_cached_input is not None and self.total_output is not None:
            cached = self.cached_input or 0
            return self.non_cached_input + cached + self.total_output
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Convert record to dictionary."""
        d = asdict(self)
        d["total_tokens"] = self.total_tokens
        return d


# ---------------------------------------------------------------------------
# Pure-Python Protobuf Wire Decoder
# ---------------------------------------------------------------------------


def read_varint(buf: bytes, offset: int) -> Optional[Tuple[int, int]]:
    """Decode a variable-length integer (Varint) from binary buffer.

    Returns:
        (value, next_offset) or None if buffer is truncated or malformed.
    """
    res = 0
    shift = 0
    cur = offset
    max_len = len(buf)

    while cur < max_len:
        b = buf[cur]
        cur += 1
        res |= (b & 0x7F) << shift
        shift += 7
        if (b & 0x80) == 0:
            if res > MAX_SAFE_VARINT:
                return None
            return res, cur
        if shift >= 70:
            # Exceeded maximum possible 64-bit varint length
            return None
    return None


def is_printable_string(buf: bytes) -> Optional[str]:
    """Return decoded string if bytes represent valid, printable UTF-8 text."""
    if not buf or len(buf) > 512:
        return None
    try:
        text = buf.decode("utf-8")
        # Ensure printable ASCII/Unicode without binary control characters (except \t, \n, \r)
        if all(c in "\t\n\r" or ord(c) >= 32 for c in text):
            return text
    except UnicodeDecodeError:
        pass
    return None


def parse_proto(buf: bytes) -> List[Dict[str, Any]]:
    """Defensive, pure-Python wire-level protobuf parser without .proto file dependency.

    Parses wire types 0 (Varint), 1 (64-bit), 2 (Length-delimited), and 5 (32-bit).
    Safely terminates on unknown wire types, truncated bytes, or zero-tags.
    """
    offset = 0
    result: List[Dict[str, Any]] = []
    buf_len = len(buf)

    while offset < buf_len:
        v_key = read_varint(buf, offset)
        if not v_key:
            break
        key, offset = v_key
        tag = key >> 3
        wire_type = key & 7

        # Protobuf tags must be > 0
        if tag == 0:
            break

        if wire_type == 0:  # Varint
            v_val = read_varint(buf, offset)
            if not v_val:
                break
            val, offset = v_val
            result.append({"tag": tag, "type": "varint", "val": val})

        elif wire_type == 1:  # 64-bit fixed
            if offset + 8 > buf_len:
                break
            val = int.from_bytes(buf[offset : offset + 8], byteorder="little", signed=False)
            offset += 8
            result.append({"tag": tag, "type": "fixed64", "val": val})

        elif wire_type == 2:  # Length-delimited (string, submessage, bytes)
            v_len = read_varint(buf, offset)
            if not v_len:
                break
            length, offset = v_len
            if offset + length > buf_len or length < 0:
                break
            sub_buf = buf[offset : offset + length]
            offset += length

            text_str = is_printable_string(sub_buf)
            if text_str is not None:
                result.append({"tag": tag, "type": "string", "val": text_str})
            else:
                try:
                    sub_parsed = parse_proto(sub_buf)
                    if sub_parsed:
                        result.append({"tag": tag, "type": "message", "val": sub_parsed})
                    else:
                        result.append({"tag": tag, "type": "bytes", "len": len(sub_buf)})
                except Exception:
                    result.append({"tag": tag, "type": "bytes", "len": len(sub_buf)})

        elif wire_type == 5:  # 32-bit fixed
            if offset + 4 > buf_len:
                break
            val = int.from_bytes(buf[offset : offset + 4], byteorder="little", signed=False)
            offset += 4
            result.append({"tag": tag, "type": "fixed32", "val": val})

        else:
            # Unsupported wire type (e.g. deprecated groups); stop decoding gracefully
            break

    return result


def extract_generation_record(
    buf: bytes,
    idx: int,
    conversation_id: str,
    raw_size: int = 0,
    source_variant: str = "ide",
) -> Optional[GenerationRecord]:
    """Decode and validate a GenerationRecord from a raw gen_metadata protobuf BLOB.

    Navigates audited protobuf hierarchy:
      Tag 1 (Message)
        Tag 4 (Message) -> Token Metrics
          Tag 2: non_cached_input
          Tag 3: total_output
          Tag 5: cached_input
          Tag 9: thinking_tokens
          Tag 10: response_tokens
        Tag 9 (Message) -> Telemetry Context
          Tag 4 (Message) -> Timestamp (Tag 1: sec, Tag 2: nano)
          Tag 10 (Message) -> Context Window (Tag 1: context_tokens, Tag 4: context_limit)
        Tag 19 (String) -> model_id
        Tag 21 (String) -> model_display_name
    """
    if not buf or not isinstance(buf, (bytes, bytearray, memoryview)):
        return None

    try:
        parsed = parse_proto(bytes(buf))
    except Exception as ex:
        logger.warning("protobuf_parse_failed", idx=idx, conversation_id=conversation_id, error=str(ex))
        return None

    t1 = next((f for f in parsed if f.get("tag") == 1 and f.get("type") == "message"), None)
    if not t1 or not isinstance(t1.get("val"), list):
        return None

    model_id: Optional[str] = None
    model_display_name: Optional[str] = None
    non_cached_input: Optional[int] = None
    cached_input: Optional[int] = None
    total_output: Optional[int] = None
    thinking_tokens: Optional[int] = None
    response_tokens: Optional[int] = None
    context_tokens: Optional[int] = None
    context_limit: Optional[int] = None
    timestamp_sec: Optional[int] = None
    timestamp_nano: Optional[int] = None

    for f in t1["val"]:
        ftag = f.get("tag")
        ftype = f.get("type")
        fval = f.get("val")

        if ftag == 19 and ftype == "string" and fval:
            model_id = str(fval).strip()

        elif ftag == 21 and ftype == "string" and fval:
            model_display_name = str(fval).strip()

        elif ftag == 4 and ftype == "message" and isinstance(fval, list):
            for sf in fval:
                stag = sf.get("tag")
                sval = sf.get("val")
                if isinstance(sval, int) and sval >= 0:
                    if stag == 2:
                        non_cached_input = sval
                    elif stag == 3:
                        total_output = sval
                    elif stag == 5:
                        cached_input = sval
                    elif stag == 9:
                        thinking_tokens = sval
                    elif stag == 10:
                        response_tokens = sval

        elif ftag == 9 and ftype == "message" and isinstance(fval, list):
            for sf in fval:
                stag = sf.get("tag")
                sval = sf.get("val")
                if stag == 4 and sf.get("type") == "message" and isinstance(sval, list):
                    for tsf in sval:
                        tstag = tsf.get("tag")
                        tsval = tsf.get("val")
                        if isinstance(tsval, int) and tsval >= 0:
                            if tstag == 1:
                                timestamp_sec = tsval
                            elif tstag == 2:
                                timestamp_nano = tsval
                elif stag == 10 and sf.get("type") == "message" and isinstance(sval, list):
                    for csf in sval:
                        cstag = csf.get("tag")
                        csval = csf.get("val")
                        if isinstance(csval, int) and csval >= 0:
                            if cstag == 1:
                                context_tokens = csval
                            elif cstag == 4:
                                context_limit = csval

    # Convert UTC timestamp
    iso_timestamp: Optional[str] = None
    if timestamp_sec is not None and timestamp_sec > 0:
        try:
            dt = datetime.datetime.fromtimestamp(timestamp_sec, tz=datetime.timezone.utc)
            if timestamp_nano is not None and 0 <= timestamp_nano < 1_000_000_000:
                microsec = timestamp_nano // 1000
                dt = dt.replace(microsecond=microsec)
                iso_timestamp = dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            else:
                iso_timestamp = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            iso_timestamp = None

    # Validate output reconciliation
    is_reconciled = False
    if total_output is not None and thinking_tokens is not None and response_tokens is not None:
        is_reconciled = (total_output == (thinking_tokens + response_tokens))

    return GenerationRecord(
        idx=idx,
        conversation_id=conversation_id,
        model_id=model_id,
        model_display_name=model_display_name,
        non_cached_input=non_cached_input,
        cached_input=cached_input,
        total_output=total_output,
        thinking_tokens=thinking_tokens,
        response_tokens=response_tokens,
        context_tokens=context_tokens,
        context_limit=context_limit,
        timestamp=iso_timestamp,
        is_reconciled=is_reconciled,
        raw_size=raw_size or len(buf),
        source_variant=source_variant,
    )


# ---------------------------------------------------------------------------
# Antigravity SQLite Reader & Source Adapter
# ---------------------------------------------------------------------------


class AntigravitySQLiteReader:
    """Read-only reader for local Antigravity SQLite conversation databases."""

    def __init__(
        self,
        conversations_dirs: Optional[List[Union[str, Path]]] = None,
        state_file: Optional[Union[str, Path]] = None,
        sync_historical: Optional[bool] = None,
    ):
        if conversations_dirs:
            self.conversations_dirs = [Path(d).resolve() for d in conversations_dirs]
        else:
            self.conversations_dirs = self._default_search_dirs()

        if state_file:
            self.state_file = Path(state_file).resolve()
        else:
            home = Path(os.environ.get("USERPROFILE", os.path.expanduser("~")))
            self.state_file = (home / ".telemetry_agent" / "state" / "antigravity_sqlite_state.json").resolve()

        if sync_historical is None:
            env_sync = os.environ.get("ANTIGRAVITY_SYNC_HISTORICAL", "false").lower()
            self.sync_historical = env_sync in ("1", "true", "yes")
        else:
            self.sync_historical = sync_historical

        self._state: Dict[str, Any] = self._load_state()
        self._pending_updates: Dict[str, Dict[str, Any]] = {}
        self._initialized = bool(self._state.get("conversations"))
        if not self._initialized and not self.sync_historical:
            self._seed_initial_cursors()

    def _seed_initial_cursors(self) -> None:
        """Seed cursors with current max(idx) for existing databases to avoid dumping historical data."""
        state_convs = self._state.setdefault("conversations", {})
        for db_path in self.discover_databases():
            conv_id = self.get_conversation_id(db_path)
            stats = self.get_db_structure(db_path)
            if stats:
                total_rows, max_idx = stats
                state_convs[conv_id] = {
                    "last_idx": max_idx,
                    "last_count": total_rows,
                    "last_modified": db_path.stat().st_mtime if db_path.exists() else 0,
                }
        self._initialized = True
        self._save_state()

    @staticmethod
    def _default_search_dirs() -> List[Path]:
        """Resolve standard Antigravity conversation database directories on Windows / macOS / Linux."""
        candidates = []
        env_override = os.environ.get("ANTIGRAVITY_CONVERSATIONS_DIR")
        if env_override:
            candidates.append(Path(env_override))

        home = Path(os.environ.get("USERPROFILE", os.path.expanduser("~")))
        candidates.extend([
            home / ".gemini" / "antigravity-cli" / "conversations",
            home / ".gemini" / "antigravity-ide" / "conversations",
            home / ".gemini" / "antigravity" / "conversations",
        ])
        return [c for c in candidates]

    def _load_state(self) -> Dict[str, Any]:
        """Load persistent cursor state from state file."""
        if not self.state_file.is_file():
            return {"conversations": {}}
        try:
            with open(self.state_file, "r", encoding="utf-8") as fp:
                data = json.load(fp)
                if isinstance(data, dict) and "conversations" in data:
                    return data
        except Exception as ex:
            logger.warning("sqlite_state_load_failed", error=str(ex))
        return {"conversations": {}}

    def _save_state(self) -> bool:
        """Persist cursor state atomically to JSON state file."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.state_file.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as fp:
                json.dump(self._state, fp, indent=2, ensure_ascii=False)
            temp_path.replace(self.state_file)
            return True
        except Exception as ex:
            logger.warning("sqlite_state_save_failed", error=str(ex))
            return False

    def discover_databases(self) -> List[Path]:
        """Find all conversation SQLite databases across configured directories."""
        discovered: List[Path] = []
        for cdir in self.conversations_dirs:
            if not cdir.is_dir():
                continue
            for f in cdir.glob("*.db"):
                if not f.is_file():
                    continue
                # Exclude summary and non-session databases
                fname_lower = f.name.lower()
                if fname_lower == "conversation_summaries.db" or fname_lower.endswith("_summaries.db") or "summary" in fname_lower:
                    continue
                discovered.append(f)
        discovered.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        return discovered

    @staticmethod
    def get_conversation_id(db_path: Path) -> str:
        """Derive conversation UUID from database filename."""
        return db_path.stem

    @staticmethod
    def open_readonly(db_path: Path) -> sqlite3.Connection:
        """Open a SQLite database in strict read-only query mode."""
        posix_path = db_path.as_posix()
        # Use SQLite URI mode mode=ro
        conn = sqlite3.connect(f"file:///{posix_path}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON;")
        return conn

    def get_db_structure(self, db_path: Path) -> Optional[Tuple[int, int]]:
        """Return (row_count, max_idx) from gen_metadata or None on error."""
        try:
            conn = self.open_readonly(db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT count(*), ifnull(max(idx), -1) FROM gen_metadata;")
                row = cursor.fetchone()
                if row:
                    return int(row[0]), int(row[1])
            finally:
                conn.close()
        except Exception as ex:
            logger.debug("db_structure_query_failed", db=db_path.name, error=str(ex))
        return None

    def read_new_records(self, db_path: Path, last_idx: int = -1) -> List[GenerationRecord]:
        """Extract generation records with index > last_idx in ascending index order."""
        conv_id = self.get_conversation_id(db_path)
        source_variant = "cli" if "antigravity-cli" in db_path.as_posix() else "ide"
        records: List[GenerationRecord] = []

        try:
            conn = self.open_readonly(db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT idx, data, size FROM gen_metadata WHERE idx > ? ORDER BY idx ASC;",
                    (last_idx,),
                )
                rows = cursor.fetchall()
                for r_idx, r_data, r_size in rows:
                    if r_data:
                        rec = extract_generation_record(
                            buf=r_data,
                            idx=int(r_idx),
                            conversation_id=conv_id,
                            raw_size=int(r_size or len(r_data)),
                            source_variant=source_variant,
                        )
                        if rec:
                            records.append(rec)
            finally:
                conn.close()
        except Exception as ex:
            logger.warning("sqlite_read_records_error", db=db_path.name, error=str(ex))

        return records

    def collect_all_new_records(self, auto_commit: bool = False) -> List[GenerationRecord]:
        """Scan all conversation databases incrementally, detect rewrites, and return new records.

        Args:
            auto_commit: If True, atomically saves cursor state to disk immediately.
                         If False, stages cursor state in memory until commit_state() is called.
        """
        databases = self.discover_databases()
        all_new: List[GenerationRecord] = []
        state_convs = self._state.setdefault("conversations", {})

        for db_path in databases:
            conv_id = self.get_conversation_id(db_path)
            stats = self.get_db_structure(db_path)
            if not stats:
                continue
            total_rows, max_idx = stats
            if total_rows == 0:
                continue

            if conv_id not in state_convs:
                if not self.sync_historical:
                    self._pending_updates[conv_id] = {
                        "last_idx": max_idx,
                        "last_count": total_rows,
                        "last_modified": db_path.stat().st_mtime if db_path.exists() else 0,
                    }
                    continue
                else:
                    last_idx = -1
                    last_count = 0
            else:
                conv_state = state_convs[conv_id]
                last_idx = conv_state.get("last_idx", -1)
                last_count = conv_state.get("last_count", 0)

                # Detect table replacement or compaction:
                # If total row count decreased or max_idx is less than last recorded index,
                # reset cursor to reread from scratch.
                if total_rows < last_count or max_idx < last_idx:
                    logger.info(
                        "db_compaction_detected",
                        conversation_id=conv_id,
                        prev_count=last_count,
                        current_count=total_rows,
                    )
                    last_idx = -1

            if max_idx > last_idx:
                new_records = self.read_new_records(db_path, last_idx=last_idx)
                if new_records:
                    all_new.extend(new_records)
                    newest_idx = max(r.idx for r in new_records)
                    self._pending_updates[conv_id] = {
                        "last_idx": newest_idx,
                        "last_count": total_rows,
                        "last_modified": db_path.stat().st_mtime if db_path.exists() else 0,
                    }

        if auto_commit and (self._pending_updates or not self._initialized):
            self.commit_state()

        return all_new

    def commit_state(self) -> bool:
        """Atomically persist in-memory staged cursor state to the state file."""
        state_convs = self._state.setdefault("conversations", {})
        if self._pending_updates:
            state_convs.update(self._pending_updates)
            self._pending_updates.clear()
        self._initialized = True
        return self._save_state()
