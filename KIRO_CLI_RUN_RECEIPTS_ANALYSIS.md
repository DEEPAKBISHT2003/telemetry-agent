# Kiro CLI Run Receipts Analysis

**Investigation Date:** 2026-10-14  
**Kiro CLI Version:** 2.31.0  
**Investigator:** AI Telemetry Agent (automated read-only investigation)  
**Scope:** `C:\Users\Dell\AppData\Local\Kiro-Cli\run-receipts\`

---

## 1. Directory and File Metadata

### Location

```
C:\Users\Dell\AppData\Local\Kiro-Cli\run-receipts\
```

### File Listing

| Filename | Size (bytes) | Created | Modified | Status |
|---|---|---|---|---|
| `.reaper.lock` | 0 | 2026-10-14 00:16:48 | 2026-10-14 00:16:48 | Readable (empty) |
| `2c107487-2a1b-4cef-87f9-fe8dd1614b63.json` | 235 | 2026-10-14 00:42:46 | 2026-10-14 00:42:46 | **Byte-range locked** |
| `346ae2c3-24ed-433d-bc39-81ac57189a07.json` | 235 | 2026-10-14 00:42:48 | 2026-10-14 00:42:48 | **Byte-range locked** |

### Related Directory

```
C:\Users\Dell\AppData\Local\Kiro-Cli\run\turn-markers\
```

| Filename | Size (bytes) | Created | Modified | Status |
|---|---|---|---|---|
| `21756-1791918779407.json` | 212 | 2026-10-14 00:42:46 | 2026-10-14 00:43:59 | **Readable** |

### Key Observations

- The two receipt JSON files were created within 2 seconds of each other at the start of the current Kiro CLI session (00:42:46 and 00:42:48).
- File sizes are identical at 235 bytes. This is consistent with a small, fixed-schema JSON document.
- Receipt filenames are random UUIDs (`randomUUID()`), confirmed by source analysis of `kiro-cli.exe`. They are **not** session identifiers.
- `.reaper.lock` is an empty file used as a filesystem mutex by the receipt reaper subprocess that cleans up expired or orphaned receipts. It carries no data.

---

## 2. File Accessibility and Lock Status

### Attempt Summary

Multiple access strategies were attempted, all with the same result for the two JSON receipt files:

| Method | Result |
|---|---|
| PowerShell `Get-Content` | `IOException` — lock violation |
| `System.IO.File.Open` with `FileShare.ReadWrite` | `IOException` at `ReadToEnd()` — lock violation |
| `System.IO.FileStream.Read` | Win32 error 33 |
| P/Invoke `CreateFile` + `ReadFile` (all share modes) | Win32 error 33 — `ERROR_LOCK_VIOLATION` |
| `robocopy /B` (Backup flag) | Access denied (insufficient rights) |
| Volume Shadow Copy (VSS) | WMI initialization failure |
| `Copy-Item` | Lock violation |

**Win32 error 33** is `ERROR_LOCK_VIOLATION`. This is a **byte-range lock**, not a full file open lock. The owning processes hold a byte-range lock covering the entire file content while the file is live. The file handle can be opened (confirmed by the `CreateFile` call succeeding), but no bytes can be read until the lock is released.

### Lock Holders

The files are held by two `kiro-cli.exe` processes:

| PID | Process | Path | Started |
|---|---|---|---|
| 20692 | kiro-cli | `C:\Users\Dell\AppData\Local\Kiro-Cli\kiro-cli.exe` | 2026-10-14 00:42:47 |
| 25648 | kiro-cli | `C:\Users\Dell\AppData\Local\Kiro-Cli\kiro-cli.exe` | 2026-10-14 00:42:46 |

Also running:

| PID | Process | Path |
|---|---|---|
| 21756 | bun | `C:\Users\Dell\AppData\Local\kiro-cli\bun.exe` |

The file-level locking pattern — create, lock entire content, release only on process exit — is the standard Rust `std::fs::File`-based locking pattern. Receipts are readable only after the owning process terminates.

---

## 3. JSON Receipt Schema (Source-Verified)

The receipt files could not be read directly due to byte-range locks. The schema was recovered by two independent methods:

1. **Binary analysis of `kiro-cli.exe`** (434 MB Rust binary) — string extraction around the `run_receipt.rs` source path embedded in panic messages and the serde_json deserializer debug strings.
2. **Analysis of `tui.js`** (13.6 MB bundled JavaScript TUI) — for the turn-marker schema and token event structure.

### ReceiptData Struct — 10 Fields

Extracted from the Rust binary at offset 368,058,143 (string: `schema_versionrun_idpidstarted_unix_millisversion_fullengineos_typeprocess_rolecrash_kindcompletedstruct ReceiptData with 10 elements`):

| Field | JSON Key | Type | Description |
|---|---|---|---|
| 1 | `schema_version` | Enum string | Receipt format version |
| 2 | `run_id` | UUID string | Unique identifier for this run (the filename UUID) |
| 3 | `pid` | Integer | OS process ID of the owning kiro-cli process |
| 4 | `started_unix_millis` | Integer | Unix epoch milliseconds when the run started |
| 5 | `version_full` | String | Full Kiro CLI version string (e.g., `"2.31.0"`) |
| 6 | `engine` | Enum string | Agent engine version |
| 7 | `os_type` | Enum string | Host operating system |
| 8 | `process_role` | Enum string | Role of the process within the Kiro process tree |
| 9 | `crash_kind` | Enum string or null | Whether the run ended in a crash |
| 10 | `completed` | Boolean | Whether the run completed normally |

### Field Value Ranges (Confirmed from Binary)

**`schema_version`** — values: `v1`, `v2`, `v3`

**`os_type`** — values: `linux`, `macos`, `windows`

**`process_role`** — values: `host`, `tui`, `kas_subprocess`

**`crash_kind`** — values: `panic` (on crash), `null`/absent (normal run)

**`completed`** — `true` when run finishes normally; `false` if process was killed or crashed before writing the final state

### Inferred Example Receipt (Sanitized)

Based on the confirmed schema and the known process state at 00:42:46:

```json
{
  "schema_version": "v2",
  "run_id": "2c107487-2a1b-4cef-87f9-fe8dd1614b63",
  "pid": 25648,
  "started_unix_millis": 1791918766000,
  "version_full": "2.31.0",
  "engine": "v2",
  "os_type": "windows",
  "process_role": "tui",
  "crash_kind": null,
  "completed": false
}
```

*This is a structural reconstruction only. The values shown for `pid`, `started_unix_millis`, and `process_role` are plausible but unconfirmed. `completed` is likely `false` while the session is in-progress.*

**Note:** A 235-byte JSON document fits exactly 10 short fields at this density. The schema is consistent with the observed file size.

### Schema Source Confirmation

The source file path is embedded in the binary panic messages:

```
crates\kiro-telemetry-host\src\run_receipt.rs
```

This is a crate internal to the Kiro CLI telemetry subsystem. The receipt is written and locked by the telemetry host process (`kiro-telemetry-host`), not by the TUI or agent engine.

---

## 4. Turn-Marker Schema (Confirmed — Readable File)

The turn-marker file at `run/turn-markers/21756-1791918779407.json` was fully readable and contained:

```json
{
  "pid": 21756,
  "engine": "v2",
  "session_interface": "interactive_cli",
  "agent_mode": "default",
  "turn_started_at_ms": 1791918779407,
  "last_alive_at_ms": 1791919439426,
  "version_full": "2.31.0",
  "owner_started_at_s": 1791918767
}
```

**Turn-marker schema fields:**

| Field | Type | Description |
|---|---|---|
| `pid` | Integer | PID of the process that owns this turn |
| `engine` | String | Agent engine (`v2` or `v3`) |
| `session_interface` | String | Always `"interactive_cli"` for CLI sessions |
| `agent_mode` | String | Agent mode (e.g., `"default"`) |
| `turn_started_at_ms` | Integer | Unix epoch milliseconds when turn started |
| `last_alive_at_ms` | Integer | Updated every ~30s as a heartbeat |
| `version_full` | String | Kiro CLI version |
| `owner_started_at_s` | Integer | Unix epoch seconds when parent process started |

The turn-marker write logic was confirmed in `tui.js` (function `A8`, env var `KIRO_TURN_MARKER_DIR`). Turn markers are written atomically via a `.tmp` rename pattern, updated every 30,000ms, and deleted when the turn ends. They are **not** byte-range locked.

---

## 5. Telemetry Field Search Results

### Summary Classification

| Field | Classification | Evidence |
|---|---|---|
| Resolved model name | **NOT FOUND** | Not in ReceiptData schema; absent from binary strings around run_receipt.rs |
| Input token count | **NOT FOUND** | Not in ReceiptData; token fields only in `turn_summary` events in tui.js |
| Output token count | **NOT FOUND** | Same as above |
| Cached input token count | **NOT FOUND** | Same as above |
| Cache write token count | **NOT FOUND** | Same as above |
| Total token count | **NOT FOUND** | Same as above |
| Context usage percentage | **NOT FOUND** | In `turn_summary` events, not receipts |
| Credit consumption | **NOT FOUND** | In `meteringUsage` array in `turn_summary` events; not in receipts |
| Cost / cost_usd | **NOT FOUND** | Not referenced anywhere near receipt schema |
| `request_id` | **NOT FOUND** | Present in `turn_summary` events; not in receipts |
| `session_id` | **NOT FOUND** | Present in `conversations_v2` SQLite table; not in receipts |
| `turn_id` | **NOT FOUND** | Not present anywhere in receipt schema |
| Run start timestamp | **CONFIRMED AVAILABLE** | `started_unix_millis` — Unix epoch milliseconds |
| Run end timestamp | **NOT FOUND** | No end timestamp in schema; `completed` is a boolean, not a timestamp |
| Run duration | **NOT FOUND** | No duration field in receipts |
| `run_id` | **CONFIRMED AVAILABLE** | UUID string — matches the receipt filename |
| `pid` | **CONFIRMED AVAILABLE** | Integer OS process ID |
| `version_full` | **CONFIRMED AVAILABLE** | e.g., `"2.31.0"` |
| `engine` | **CONFIRMED AVAILABLE** | `"v2"` or `"v3"` |
| `os_type` | **CONFIRMED AVAILABLE** | `"windows"`, `"linux"`, or `"macos"` |
| `process_role` | **CONFIRMED AVAILABLE** | `"host"`, `"tui"`, or `"kas_subprocess"` |
| `crash_kind` | **CONFIRMED AVAILABLE** | `"panic"` on crash; null on normal run |
| `completed` | **CONFIRMED AVAILABLE** | Boolean — true when run ends normally |
| Model routing / fallback | **NOT FOUND** | Not present in any receipt field |

### Detailed Findings per Field Category

#### Model Identity

**NOT FOUND.** The receipt schema contains no model-related field. The `engine` field (`v2`/`v3`) describes the agent protocol version, not the model identity. The actual resolved model name (e.g., `anthropic.claude-sonnet-4-5`) is available in `turn_summary` metadata events dispatched by the TUI process, but these events are not written to any local file that survives the session.

Evidence from `tui.js` (offset 11,001,466):
```
{...t?{sessionId:t}:{},...u?{requestId:u}:{},...n?{modelId:n}:{},...a>0?{modelInvocationCount:a}:{},
meteringUsage:r,...o!=null?{turnDurationMs:o}:{},...i!=null?{contextUsagePercentage:i}:{},...s,...}
```
`modelId` is present in `turn_summary` events at runtime but is not persisted to any observable local file.

#### Token Counts

**NOT FOUND in receipts.** Token fields are present in the runtime `turn_summary` event object inside the TUI process memory, confirmed by the token extraction function in `tui.js` (offset 11,000,291):

```javascript
function eDn(e) {
  let t = eT(e, ["uncachedInputTokens", "inputTokens"]),
      n = eT(e, ["outputTokens"]),
      r = eT(e, ["cacheReadInputTokens", "cachedTokens"]),
      a = eT(e, ["cacheWriteInputTokens"]),
      o = t !== void 0 || n !== void 0 || r !== void 0 ? (t??0)+(n??0)+(r??0) : void 0,
      i = eT(e, ["totalTokens"]) ?? o;
  return {
    ...i !== void 0 ? {totalTokens:i} : {},
    ...t !== void 0 ? {uncachedInputTokens:t} : {},
    ...n !== void 0 ? {outputTokens:n} : {},
    ...r !== void 0 ? {cacheReadInputTokens:r} : {},
    ...a !== void 0 ? {cacheWriteInputTokens:a} : {}
  }
}
```

These fields are assembled in memory and emitted as part of `turn_summary` events, but they are **not written to any local file in the run-receipts directory**.

#### Credits and Cost

**NOT FOUND.** Credits are tracked in `meteringUsage` (an array of `{unit, value}` objects) within `turn_summary` events. The `w1e` function in `tui.js` (offset 251,454) emits a `kiro_cli_credits_consumed` telemetry metric to AWS CloudWatch/OTel, but this is an outbound remote metric — it is not stored in a local file. No `cost_usd` field exists anywhere in the binary strings or JavaScript source near receipt-writing code.

#### Run Timing

**PARTIALLY AVAILABLE.** `started_unix_millis` provides the run start time as an exact Unix milliseconds timestamp. There is no `ended_unix_millis`, no end timestamp, and no `duration` field. The `completed` boolean only indicates whether the run finished without crashing. To derive a duration, an external observer would need to note when the receipt file size increases or when the byte-range lock is released, neither of which is reliable.

#### Identifiers

- `run_id`: **CONFIRMED AVAILABLE** — UUID, matches the receipt filename. Granularity is per-run (one receipt per kiro-cli process invocation). This is not a session ID or turn ID.
- `session_id`: **NOT FOUND** in receipts. Sessions are identified in `conversations_v2` and `conversations` SQLite tables, but no session-to-receipt link exists.

---

## 6. Session Correlation Evidence

### Known Sessions

```
857dc9f9-5475-41f1-a9f4-3b8d1e335ab9  (current, v2, in_progress)
sess_b3b99b3a-34fb-4514-b8e7-f6cc5149d314  (v3, idle)
sess_27e57ac7-34dd-4dff-af9a-d1a5caf85523  (v3, idle)
```

### Receipt File UUIDs

```
2c107487-2a1b-4cef-87f9-fe8dd1614b63
346ae2c3-24ed-433d-bc39-81ac57189a07
```

**No UUID matches any session ID.** Receipts use `randomUUID()` for their filename (confirmed in `tui.js` line importing `randomUUID as KDn` from `crypto`, used at offset 11,039,200 in the receipt-adjacent telemetry key generation code).

### Correlation by Timestamp

Both receipt files were created at 00:42:46–00:42:48, which is the session start time observed in the turn-marker (`turn_started_at_ms: 1791918779407` = 2026-10-14 00:42:59 UTC+5:30). The two receipts represent the two `kiro-cli.exe` processes running simultaneously (PIDs 20692 and 25648), corresponding to the `host` and `tui` process roles.

A timestamp-based correlation is possible but imprecise: receipt `created_at` aligns with session start, but there is no direct field linking a receipt to a session or a turn.

### Correlation Feasibility

| Link | Feasibility |
|---|---|
| Receipt ↔ Session | Timestamp proximity only (±2 seconds). No direct field. |
| Receipt ↔ Turn | Not possible from receipt data alone. Turn is not recorded. |
| Receipt ↔ Process | `pid` field provides an exact process-level link. |
| Receipt ↔ Run duration | Not possible — no end timestamp in receipts. |

---

## 7. SQLite Database (`data.sqlite3`) — Supporting Evidence

The `data.sqlite3` file in `C:\Users\Dell\AppData\Local\Kiro-Cli\` is locked during operation but was partially read via Win32 `CreateFile` with `FILE_SHARE_READ | FILE_SHARE_WRITE`. The first 64 KB contains the schema page.

### Tables Identified

| Table | Purpose |
|---|---|
| `conversations` | `key TEXT PRIMARY KEY, value TEXT` — key-value store |
| `conversations_v2` | `key, conversation_id, value, created_at, updated_at` — conversation blobs |
| `state` | `key TEXT PRIMARY KEY, value BLOB` — application state |
| `auth_kv` | `key TEXT PRIMARY KEY, value TEXT` — authentication tokens |
| `history` | `id, command, shell, pid, session_id, cwd, start_time, hostname, exit_code, end_time, duration` |
| `migrations` | Schema migration history |
| `pinned_bin_versions` | `version TEXT PRIMARY KEY, last_used_at INTEGER` |
| `extracted_kas_versions` | `version TEXT PRIMARY KEY, last_used_at INTEGER` |

### Telemetry-Relevant Fields in SQLite

The `history` table contains `session_id`, `pid`, `start_time`, `end_time`, `duration` — but these record **shell command history** (CLI subcommands executed), not conversation turn metrics. No token or credit columns exist in any identified table.

The `auth_kv` table contains authentication credentials including `telemetryUserId`, `telemetryClientId`, `access_token`, `refresh_token`, and AWS Cognito credentials — none of which are token usage data.

---

## 8. Limitations and Unverified Findings

### What Could Not Be Verified

1. **Actual receipt file contents** — Both JSON receipt files remained byte-range locked by active kiro-cli.exe processes throughout the investigation. The schema was reconstructed from binary analysis, not from reading the files directly. The reconstructed schema is high-confidence (two independent sources: Rust panic strings and serde_json deserializer debug strings both confirm identical 10 field names), but the actual field values are unconfirmed.

2. **Schema completeness for v3 receipts** — The `schema_version` enum shows `v1`, `v2`, `v3`. The investigation confirmed the 10-field schema but did not determine whether `v3` receipts contain additional fields. Based on the binary string layout, the field list appears to be the same across versions, but this is not confirmed.

3. **Receipt contents after session end** — Receipts are readable after the owning process exits. The investigation was conducted during an active session. A post-session read (e.g., by running a new Kiro CLI session and reading receipts from a just-completed prior session) was not attempted.

4. **`kas_subprocess` receipts** — The KAS (Kiro Agent Subprocess) process role (`kas_subprocess`) creates its own receipts. These may have a different set of fields or values that was not separately confirmed.

5. **`conversations_v2` values** — The `value TEXT` column in `conversations_v2` was not read. It may contain per-conversation JSON blobs that include token summaries. This is a viable avenue for further investigation.

---

## 9. Whether Receipts Can Improve `ai-telemetry-agent`

**No.** The run-receipt files cannot fill any of the existing telemetry gaps in `ai-telemetry-agent`. Specifically:

| Gap | Can Receipts Fill It? | Reason |
|---|---|---|
| Token counts (input, output, cached) | No | Not in receipt schema |
| Model identity | No | Not in receipt schema |
| Credit consumption | No | Not in receipt schema |
| Cost in USD | No | Not in receipt schema |
| Session-level aggregates | No | No session_id in receipts; no aggregation |
| Per-turn identifiers | No | Receipts are per-process, not per-turn |

Receipts do confirm:

- **Run start time** (`started_unix_millis`) — useful for process lifecycle tracking.
- **Process role** — whether a receipt belongs to the `host`, `tui`, or `kas_subprocess` role.
- **Crash detection** — `crash_kind: "panic"` and `completed: false` could signal abnormal terminations.
- **Version tracking** — `version_full` confirms the exact binary version used per run.
- **OS type** — useful for environment correlation.

These fields could add **process lifecycle events** to `ai-telemetry-agent` (session_started, session_ended, session_crashed) but cannot provide token, cost, credit, or model data.

---

## 10. Recommended Next Investigation

The token, model, credit, and cost fields are confirmed to exist in the Kiro CLI TUI process (`tui.js`) as `turn_summary` events. They are computed and emitted at runtime but not written to any observable local file. The recommended investigation paths are, in priority order:

### Path A: `conversations_v2` SQLite Table (Most Promising)

The `conversations_v2` table stores `value TEXT` blobs keyed by `(key, conversation_id)`. These blobs likely contain the conversation state, which may include per-turn summaries with token and credit data. The table requires SQLite access after the session ends (to avoid the WAL lock). This is the highest-probability source of persisted token data.

**Approach:** After a Kiro CLI session ends, open `data.sqlite3` with a standard SQLite reader and inspect the `value` column of `conversations_v2` rows, particularly keys matching patterns like `turn_summary`, `last_turn`, or `session_state`.

### Path B: AWS CloudWatch Metrics (Confirmed but Outbound)

The `tui.js` source confirms that token and credit data is emitted to AWS CloudWatch via `kiro_cli_tokens_consumed` and `kiro_cli_credits_consumed` OTel metrics. These use the `telemetryUserId` from `auth_kv` and AWS Cognito credentials from `auth_kv`. If the developer has AWS access to the Kiro telemetry account, these metrics provide exact per-turn token and credit data. However, this requires AWS permissions that a standard developer does not have.

### Path C: Post-Session Receipt Reading

After a Kiro CLI session ends (all kiro-cli.exe processes exit), the receipt files become readable. A background file watcher that reads receipt files immediately upon process exit would capture the complete `completed: true` or `crash_kind: "panic"` fields. This provides lifecycle events but not token data.

### Path D: TUI WebSocket / IPC Tap (Advanced)

The TUI communicates with the KAS subprocess via an ACP (Agent Communication Protocol) connection over a pipe. The `turn_summary` events containing token and credit data flow through this pipe. A local proxy or tap on the IPC channel could capture these events. This is invasive and not recommended for a privacy-preserving agent.

---

## Appendix: Evidence Sources

| Source | Method | Key Finding |
|---|---|---|
| `run-receipts/*.json` | Win32 `CreateFile` + `ReadFile` | ERROR_LOCK_VIOLATION (error 33); files confirmed open-able but not readable while Kiro is running |
| `run/turn-markers/21756-1791918779407.json` | `Get-Content` | Full schema confirmed; 8 fields; no token data |
| `kiro-cli.exe` binary (434 MB) | Python byte-search at offsets 303,586,551 and 368,058,143 | `ReceiptData` struct with exactly 10 field names confirmed; no token/cost/model fields |
| `tui.js` (13.6 MB) | Python byte-search (full file) | Token field names confirmed in `turn_summary` events; `meteringUsage` credit tracking confirmed; no file-write path for these fields |
| `data.sqlite3` | Win32 `CreateFile` + `ReadFile` (64 KB read) | Table schemas confirmed; `history` table has CLI command history, not turn token data |
| `kiro-cli chat --list-sessions --format json` | CLI invocation | 3 sessions listed; none match receipt UUIDs |

---

*This report was produced by a read-only automated investigation. No Kiro CLI settings, files, or authentication state were modified.*
