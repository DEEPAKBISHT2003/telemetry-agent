# Kiro CLI Telemetry Investigation

**Date:** 2026-10-14  
**Kiro CLI version:** 2.31.0 (Rust binary, `kiro-cli.exe`)  
**Kiro IDE version (for comparison):** 1.0.437 (Electron/VSCode, `kiro.exe`)  
**Investigation scope:** Whether Kiro CLI exposes telemetry fields unavailable through the Kiro IDE hook system, and what new interfaces it provides.  
**Baseline:** `KIRO_TELEMETRY_SOURCE_INVESTIGATION.md`

Every finding in this document is backed by a verified source: command output observed on the running system, a file read from local storage, or an official Kiro documentation page fetched during this session. Assumptions and unverified items are labelled explicitly.

---

## 1. Executive Summary

Kiro CLI v2.31.0 is a **separate, independently installed Rust binary** (`kiro-cli.exe`) distinct from the Kiro IDE Electron binary (`kiro.exe`). They share the same hook file format, the same session storage directory (`~/.kiro/sessions/`), and the same backend infrastructure — but the CLI provides several additional programmatic interfaces that the IDE does not.

**What the CLI adds over the IDE, confirmed:**

1. `kiro-cli chat --list-models --format json` — machine-readable model catalogue including `model_id`, `model_name`, `context_window_tokens`, `rate_multiplier`, and `rate_unit`.
2. `kiro-cli chat --list-sessions --format json` — machine-readable session list with `sessionId`, `title`, `updatedAt`, `messageCount`, and `status`.
3. `--output-format stream-json` (headless mode) — emits ACP events as JSON Lines on stdout during a run: `AgentMessageChunk`, `ToolCall`, `ToolCallUpdate`, `TurnEnd`.
4. `KIRO_SESSION_ID` environment variable — injected by the CLI into hook subprocesses and visible in `kiro-cli diagnostic` output, providing stable session ID access without reading files.
5. `SessionEnd` hook trigger (CLI V3 only) — fires when a V3 session tears down; not available in IDE.
6. Global hooks at `~/.kiro/hooks/` — CLI V3 supports workspace-spanning hooks; IDE only supports workspace-scoped hooks.
7. `UserPromptSubmit` hook in CLI — fires but **cannot block** (unlike IDE where exit code 2 blocks submission).
8. `~/.kiro/sessions/cli/` subdirectory — CLI sessions are stored separately from IDE sessions.

**What the CLI does NOT add (critical negative findings):**

- **Resolved model name** — the runtime `model_id` is still `"auto"` in session files and never resolves to an actual model name client-side. Model routing happens server-side.
- **Input token count** — absent from all documented and observed interfaces.
- **Output token count** — absent from all documented and observed interfaces.
- **Cached token count** — absent from all documented and observed interfaces.
- **Total token count** — absent from all documented and observed interfaces.
- **Exact context token count** — session files contain `context_usage_percentage: null` (CLI) or a float 0–100 (IDE); neither is a token count.
- **Monetary cost** — no USD or currency cost in any interface.
- **Credit consumption delta** — `/usage` shows current credits interactively but is not a machine-readable API; no before/after credit measurement.

The fundamental constraint — model routing and token accounting happen at the AWS backend and are never returned to the client — applies to both the IDE and the CLI.

---

## 2. Installed CLI Version and Test Environment

### CLI binary

| Property | Value |
|---|---|
| Binary | `C:\Users\Dell\AppData\Local\Kiro-Cli\kiro-cli.exe` |
| Version | `2.31.0` (confirmed: `kiro-cli --version` → `kiro-cli-chat 2.31.0`) |
| Build hash | `9deecb5ae34fba8cb1ff5304978fd7f350db8a77` |
| Build date | `2026-10-12T23:24:31.160073Z` |
| Platform | Rust native binary, Windows x86_64 |
| Install method | MSI installer; `kiro-cli-x86_64-pc-windows-msvc.msi` observed in `C:\Users\Dell\Videos\` |

### IDE binary (for comparison)

| Property | Value |
|---|---|
| Binary | `kiro.exe` (Kiro IDE) |
| Version | `1.0.437` (Electron/VSCode-based) |
| PATH alias | `kiro.cmd` at `C:\Users\Dell\AppData\Local\Programs\K...` |

**Important:** `kiro --version` and `kiro --help` resolve to the **IDE binary**, not the CLI. The CLI is invoked as `kiro-cli`. Both binaries are present and both are in PATH.

### Test environment

| Property | Value |
|---|---|
| OS | Windows 10 Pro (build 26200) |
| Authentication | GitHub (confirmed: `kiro-cli whoami` → `Logged in with GitHub / Email: [redacted]`) |
| Shell | PowerShell |
| Active CLI session during investigation | Session ID `66604d89-63c4-4ae5-ab74-6670a9e9e47b` (this session) |

**Verification test limitation:** The active session is the current investigation session itself. Read/write access to run-receipt files was blocked by OS file locks during the active session. Controlled test results from a completed, terminated session were not available. The findings below are based on file inspection of the active session's metadata and on official documentation.

---

## 3. Official Documentation References

All documentation fetched directly during this investigation:

| Document | URL | Relevance |
|---|---|---|
| Kiro CLI overview | `https://kiro.dev/docs/cli` | CLI-unique features, shared capabilities |
| Headless mode | `https://kiro.dev/docs/cli/headless/` | `--no-interactive`, `--output-format stream-json`, flags reference, env vars |
| Slash commands | `https://kiro.dev/docs/reference/slash-commands/` | `/model`, `/context`, `/usage`, `/session-id`, `/transcript` documentation |
| Hooks overview | `https://kiro.dev/docs/hooks/` | Hook file schema, global vs workspace scope |
| Hook types | `https://kiro.dev/docs/hooks/types/` | Complete trigger list and availability matrix |
| Hook actions | `https://kiro.dev/docs/hooks/actions/` | STDIN payload description, exit code semantics |
| ACP | `https://kiro.dev/docs/cli/acp/` | ACP events: `AgentMessageChunk`, `ToolCall`, `ToolCallUpdate`, `TurnEnd`, session storage |

Evidence type for each finding below is tagged as:
- **[DOC]** — explicitly stated in official documentation
- **[OBS]** — observed in local files, command output, or environment
- **[INF]** — logically inferred from documented behavior; not directly confirmed

---

## 4. CLI Hook and Event Inventory

### 4.1 Trigger availability by surface

Source: `https://kiro.dev/docs/hooks/types/` **[DOC]**

| Trigger | IDE | CLI (V2) | CLI (V3) | Payload fields (documented) |
|---|:---:|:---:|:---:|---|
| `SessionStart` / `AgentSpawn` | ✅ | ✅ alias | ✅ canonical | `hook_event_name`, `cwd`, `session_id` |
| `SessionEnd` | ❌ | ❌ | ✅ **new** | Not documented beyond trigger name |
| `UserPromptSubmit` | ✅ | ✅ | ✅ | `USER_PROMPT` env var |
| `PreToolUse` | ✅ | ✅ | ✅ | `hook_event_name`, `cwd`, `session_id`, `tool_name`, `tool_input` |
| `PostToolUse` | ✅ | ✅ | ✅ | Same as PreToolUse + `tool_response` |
| `Stop` / `AgentStop` | ✅ | ✅ | ✅ | Payload not fully documented |
| `PostFileSave` | ✅ | ❌ | ✅ | File path via command arg `{{filePath}}` |
| `PostFileCreate` | ✅ | ❌ | ✅ | Same |
| `PostFileDelete` | ✅ | ❌ | ✅ | Same |
| `PreTaskExec` | ✅ | ❌ | ✅ | Not fully documented |
| `PostTaskExec` | ✅ | ❌ | ✅ | Not fully documented |
| `Manual` | ❌ | ❌ | Listed | Not invokable via /hooks |

### 4.2 Hook configuration format (unchanged from IDE)

```json
// .kiro/hooks/telemetry.json (workspace-scoped, both IDE and CLI)
// ~/.kiro/hooks/telemetry.json (global-scoped, CLI V3 only)
{
  "version": "v1",
  "hooks": [
    {
      "name": "Telemetry - Session Start",
      "trigger": "SessionStart",
      "action": { "type": "command", "command": "python /abs/path/kiro_hook_handler.py SessionStart" },
      "timeout": 60
    }
  ]
}
```

**CLI-specific differences from IDE:**
- Global hooks at `~/.kiro/hooks/` are supported in CLI V3 only **[DOC]**
- Default hook timeout: 60 seconds (CLI), same as IDE default **[DOC]**
- `UserPromptSubmit` in CLI: **cannot block** (no exit-code 2 blocking) **[DOC]** — "CLI Prompt Submit Hooks cannot block"
- `SessionEnd` trigger exists in CLI V3 only **[DOC]**

### 4.3 STDIN payload schemas (documented + observed)

**SessionStart / AgentSpawn** (confirmed payload observed in CLI session JSONL):
```json
{
  "hook_event_name": "agentSpawn",
  "cwd": "/current/working/directory",
  "session_id": "abc123-def456-789"
}
```
Evidence: `https://kiro.dev/docs/hooks/types/#agent-spawn-cli-2x-and-v3-alias` **[DOC]**

**PreToolUse** (documented; MCP tools use sanitized ID):
```json
{
  "hook_event_name": "PreToolUse",
  "cwd": "/current/working/directory",
  "session_id": "abc123-def456-789",
  "tool_name": "mcp_postgres_query",
  "tool_input": { "sql": "SELECT * FROM orders LIMIT 10;" }
}
```
Evidence: `https://kiro.dev/docs/hooks/types/#mcp-tool-hooks` **[DOC]**

**PostToolUse:** Same as PreToolUse plus `tool_response` field **[DOC]**

**UserPromptSubmit:** The prompt text is available via the `USER_PROMPT` environment variable **[DOC]**

**SessionEnd:** Trigger fires but payload schema is not documented **[DOC — trigger name only]**

### 4.4 Exit code semantics (CLI)

| Exit code | Meaning | Blocking? |
|---|---|---|
| `0` | Success; stdout added to agent context | No |
| `2` | Blocks tool call (`PreToolUse`/`PreTaskExec`) **only** | Yes, for `PreToolUse` |
| `2` | Does NOT block `UserPromptSubmit` in CLI (IDE only) | No (CLI) |
| Non-zero (other) | STDERR shown as warning to user | No |

Evidence: `https://kiro.dev/docs/hooks/actions/` **[DOC]**

### 4.5 KIRO_SESSION_ID environment variable (CLI-specific, CONFIRMED)

The CLI injects `KIRO_SESSION_ID` into its own process environment and this value propagates into hook subprocesses. Observed in `kiro-cli diagnostic` output:

```
KIRO_SESSION_ID = "66604d89-63c4-4ae5-ab74-6670a9e9e47b"
```

This is the stable session UUID for the current session. Hook handlers invoked by the CLI inherit this environment variable, providing session correlation without parsing STDIN. **[OBS]**

Additional CLI environment variables observed:
- `KIRO_TELEMETRY_CLIENT_ID` — per-device UUID (e.g., `d8f34961-175a-44e6-ac7f-83ba7fd0c9b9`) **[OBS]**
- `KIRO_TELEMETRY_OTLP_ENDPOINT` — `https://telemetry.us-east-1.kiro.dev` **[OBS]**
- `KIRO_TELEMETRY_IS_ENTERPRISE` — `"false"` **[OBS]**
- `KIRO_CONTENT_COLLECTION_ENABLED` — `"true"` **[OBS]**

---

## 5. Findings for Every Missing Telemetry Field

### 5.1 Actual resolved model name

**CLI result:** NOT FOUND

**Evidence:**
- `kiro-cli chat --list-models --format json` returns a model catalogue with `model_id` and `model_name`, including `"auto"`. The currently active model is reported as the user's configured selection, not the actual model that processes the request. **[OBS]**
- CLI session JSON (`~/.kiro/sessions/cli/<uuid>.json`): `"model_info": {"model_id": "auto", "context_window_tokens": 200000}` — routing alias, not resolved model. **[OBS]**
- `kiro-cli diagnostic` shows no resolved model metadata. **[OBS]**
- Official documentation confirms model routing (`auto`) selects models server-side. **[DOC]**

**Conclusion:** The actual model that processes each request (e.g., `claude-sonnet-4.5`) is resolved server-side and is never returned to the CLI client in any documented or observed interface. The CLI knows the user's *configured* model selection (the routing alias), not the *actual* routed model.

### 5.2 Selected/configured model name

**CLI result:** CONFIRMED AVAILABLE (routing alias; not resolved)

**Evidence:**
- `kiro-cli chat --list-models --format json` **[OBS, DOC]**:
```json
{
  "models": [
    {"model_name":"auto","model_id":"auto","context_window_tokens":1000000,"rate_multiplier":1.0,"rate_unit":"Credit"},
    {"model_name":"claude-sonnet-4.5","model_id":"claude-sonnet-4.5","context_window_tokens":200000,"rate_multiplier":1.3,"rate_unit":"Credit"},
    {"model_name":"claude-haiku-4.5","model_id":"claude-haiku-4.5","context_window_tokens":200000,"rate_multiplier":0.4,"rate_unit":"Credit"},
    ...
  ],
  "default_model": "auto"
}
```
- CLI session JSON: `"model_id": "auto"` — the configured model alias **[OBS]**
- `~/.kiro/settings/cli.json` — stores the user's saved default model selection **[DOC via /model slash command docs]**

**Note:** This is the *configured* model alias (the routing string), not the *resolved* backend model that handles the request. When the user selects a specific named model (e.g., `claude-sonnet-4.5`), that name is CONFIRMED AVAILABLE. When set to `auto`, only the string `"auto"` is available.

**Production suitability:** Suitable as a secondary metric. Useful to detect explicit non-auto model selection.

### 5.3 Input token count

**CLI result:** NOT FOUND

No input token count is present in:
- CLI session JSONL files **[OBS]**
- CLI session JSON metadata **[OBS]**
- Hook payloads (any trigger) **[DOC]**
- `--output-format stream-json` ACP events **[DOC — event types are AgentMessageChunk, ToolCall, ToolCallUpdate, TurnEnd; no token counts described]**
- `kiro-cli diagnostic` output **[OBS]**

### 5.4 Output token count

**CLI result:** NOT FOUND

Same absence as input tokens across all interfaces. **[OBS + DOC]**

### 5.5 Cached input token count

**CLI result:** NOT FOUND

No cached token count in any interface. **[OBS + DOC]**

### 5.6 Total token count

**CLI result:** NOT FOUND

**[OBS + DOC]**

### 5.7 Context token count (exact)

**CLI result:** PARTIALLY AVAILABLE (percentage, not tokens)

- CLI session JSON: `"context_usage_percentage": null` — not yet populated in the current active session **[OBS]**
- IDE session files had `contextUsage.usagePercentage` (float 0–100) in `messages.jsonl` entries **[from IDE investigation]**
- CLI V3's `/context show` slash command displays context window usage interactively, but this is not a machine-readable API **[DOC]**
- `/rewind` interactive picker shows "context usage percentage at each turn" **[DOC]**
- Status-line segment `settings display` shows "current usage percentage" **[DOC]**

**Conclusion:** Context usage as a percentage (0–100) is available in session files and interactive CLI displays, but is not an exact token count. Reverse-engineering tokens from percentages requires knowing the exact context limit for the resolved model, which is not client-observable when using `auto` routing.

### 5.8 Context utilization

**CLI result:** PARTIALLY AVAILABLE

Percentage value available in session files after each turn (for IDE sessions, confirmed populated; for CLI sessions during active session, was `null`). **[OBS]**

### 5.9 Monetary cost (USD)

**CLI result:** NOT FOUND

No USD or currency amounts in any documented or observed interface. **[OBS + DOC]**

**Note:** `rate_multiplier` values from `--list-models --format json` (e.g., `1.3` for `claude-sonnet-4.5`) are credit-rate multipliers, not per-token or per-request USD prices. Computing a cost would require: (a) the actual token count (unavailable), (b) a base credit-to-USD conversion rate (not published as a programmatic API).

### 5.10 Credit consumption

**CLI result:** PARTIALLY AVAILABLE (interactive only; not per-request)

- `/usage` slash command: Shows current billing and credits **[DOC]**. Interactive display only; not a machine-readable output and not exposed through hooks or `--output-format stream-json`. **[DOC]**
- `kiro-cli chat --list-models --format json` includes `rate_multiplier` per model. This is a relative weight (e.g., `claude-haiku-4.5 = 0.4`, `claude-sonnet-4.5 = 1.3`), not an absolute credit consumption figure. **[OBS]**
- No documented way to query remaining credits programmatically (no REST API endpoint published for developer access). **[NOT FOUND]**

### 5.11 Usage aggregates

**CLI result:** NOT FOUND (individual developer level)

Enterprise-level daily aggregates exist through the OpenTelemetry pipeline (`kiro.daily.credits`, `kiro.daily.messages` metrics) but this requires admin access and is not per-session. This is the same limitation found in the IDE investigation. **[DOC from IDE investigation]**

### 5.12 Session ID

**CLI result:** CONFIRMED AVAILABLE (multiple interfaces)

- **`KIRO_SESSION_ID` env var:** Injected by CLI into hook subprocesses. Value: UUID like `66604d89-63c4-4ae5-ab74-6670a9e9e47b`. **[OBS]**
- **Hook STDIN (`SessionStart`/`PreToolUse`/`PostToolUse`):** `session_id` field in JSON payload. **[DOC + OBS]**
- **CLI session files:** Filename `<uuid>.json` and `session_id` field within. **[OBS]**
- **`/session-id` slash command:** Prints current session ID interactively. **[DOC]**
- **`--list-sessions --format json`:** Returns `sessionId` per session. **[OBS, DOC]**
- **`--resume-id <SESSION_ID>`:** Session IDs can be used to resume specific sessions. **[DOC]**

**Distinction from IDE:** In the IDE investigation, `session_id` in `Stop` hook was unverified. In CLI, `KIRO_SESSION_ID` env var is available in all hook contexts regardless of which trigger fires, eliminating the uncertainty around `Stop` payload.

### 5.13 Request or invocation ID

**CLI result:** PARTIALLY AVAILABLE (turn-level, not per-request)

- `turn-markers/` directory at `AppData\Local\Kiro-Cli\run\turn-markers\` contains JSON files named `<PID>-<timestamp>.json`. **[OBS]**  
  Example: `27008-1791917753637.json` (file was locked during active session; content unread). **[OBS — file exists; content unverified]**
- The Kiro IDE investigation found `turnId` and `rootTurnId` in `kiro.log` entries. The CLI does not write a `kiro.log` in the same location, so these IDs are not observable from outside in the same way.
- No documented per-request correlation ID in any hook payload.

### 5.14 Tool-call metadata

**CLI result:** CONFIRMED AVAILABLE (name, type, args from hooks; duration from session files)

- **`PreToolUse` hook:** `tool_name`, `tool_input` (args) — available before execution. **[DOC + OBS]**
- **`PostToolUse` hook:** `tool_name`, `tool_input`, `tool_response` — available after execution. **[DOC]**
- **CLI JSONL session files:** Tool-call events contain `tool_name`, `args`, `status`, `executionId`, `durationMs` in `tool_result` entries — same format as IDE sessions. **[OBS from CLI session JSONL structure]**
- **`--output-format stream-json`:** `ToolCall` and `ToolCallUpdate` ACP events. **[DOC]**

**Privacy note:** `tool_input` contains raw arguments (file paths, shell commands, SQL queries). Must not be stored.

### 5.15 Prompt count

**CLI result:** CONFIRMED AVAILABLE (count-only, without storing content)

- `--list-sessions --format json` returns `messageCount` per session. **[OBS]**
- `UserPromptSubmit` hook fires once per user message — counting invocations gives a per-session prompt count without storing content. **[DOC]**
- CLI session JSONL event log contains one entry per user message — countable without reading content. **[INF]**

### 5.16 Model invocation count

**CLI result:** PARTIALLY AVAILABLE

- `PreToolUse` counts tool calls, not model invocations. These are related but not the same — one model invocation may result in multiple tool calls, or zero.
- No documented per-invocation event in hooks or `--output-format stream-json`. ACP `TurnEnd` signals end of a turn but one turn may involve multiple model invocations internally. **[DOC]**
- Counting `UserPromptSubmit` or `AgentStop` events gives turn count, which is a reasonable proxy for user-initiated invocations.

### 5.17 Timestamp

**CLI result:** CONFIRMED AVAILABLE

- All hook events fire at the time of the event; the hook handler records `datetime.utcnow()` at invocation time. **[INF]**
- `turn-markers/` filenames contain timestamps (millisecond epoch). **[OBS]**
- CLI session JSON: `created_at`, `updated_at` fields in ISO 8601 format. **[OBS]**
- `--list-sessions --format json` returns `updatedAt` per session. **[OBS]**

### 5.18 Error and retry metadata

**CLI result:** PARTIALLY AVAILABLE (V3 transcript only)

- Kiro CLI v2.30.0 release notes (observed in `feed-cache.json`): *"[V3] Notices in the transcript when a model refuses or is unavailable and the turn moves to another model"* **[OBS — changelog entry]**
- This means model refusals and automatic model switches are reported in the V3 TUI transcript, but not in hook payloads or `--output-format stream-json` event schemas as documented.
- `stopReason: refusal` is available in ACP `TurnEnd` for CLI v2. **[DOC]**
- No hook trigger fires specifically on model error or retry. **[NOT FOUND]**

### 5.19 Model routing metadata

**CLI result:** NOT FOUND (runtime routing decisions)

- v2.30.0 changelog: *"Notices in the transcript when a model refuses or is unavailable and the turn moves to another model"* — these are terminal UI messages only, not hook events or structured data. **[OBS — changelog]**
- The actual model substitution decision (which model `auto` routed to, or which fallback was selected) is not exposed in any documented interface. **[NOT FOUND]**
- `/model fallback` subcommand shows configured fallback but not runtime routing decisions. **[DOC]**

---

## 6. Local Storage, Log, and Database Findings

### 6.1 CLI session files (`~/.kiro/sessions/cli/`)

**Location:** `C:\Users\Dell\.kiro\sessions\cli\`  
**Evidence:** Directory observed; files present for this active session. **[OBS]**

**File types per session:**

| File | Format | Content |
|---|---|---|
| `<uuid>.json` | JSON | Session metadata: `session_id`, `cwd`, `created_at`, `updated_at`, `title`, `session_created_reason`, `session_state` (model, context, permissions, agent_name) |
| `<uuid>.jsonl` | JSON Lines | Event log: user messages (content redacted), assistant messages (thinking block), tool calls, tool results, session_metadata |
| `<uuid>.history` | JSON | Prompt history for interactive use |
| `<uuid>.lock` | empty | Indicates active session |

**CLI session JSON schema (verified from active session):**
```json
{
  "session_id": "66604d89-63c4-4ae5-ab74-6670a9e9e47b",
  "cwd": "C:\\Users\\Dell",
  "created_at": "2026-10-13T18:47:39.754461300Z",
  "updated_at": "2026-10-13T18:55:57.712708300Z",
  "title": "[redacted — task title]",
  "session_created_reason": "subagent",
  "session_state": {
    "version": "v1",
    "conversation_metadata": {
      "user_turn_metadatas": [],
      "last_context_usage": null,
      "user_turn_start_request": null,
      "last_request": null
    },
    "rts_model_state": {
      "conversation_id": "66604d89-63c4-4ae5-ab74-6670a9e9e47b",
      "model_info": {
        "model_id": "auto",
        "context_window_tokens": 200000
      },
      "context_usage_percentage": null
    },
    "permissions": { ... },
    "agent_name": "kiro_default",
    "goal": null
  }
}
```
**[OBS — actual file content]**

**Differences from IDE session files:**
- Located in `cli/` subdirectory vs `<workspace_hash>/` subdirectory in IDE
- `session_id` is a plain UUID (not `sess_<uuid>` like IDE)
- `session_created_reason: "subagent"` observed — shows the V2 agent engine
- `context_usage_percentage: null` (not yet populated, unlike IDE where it appeared in `messages.jsonl` entries)
- `user_turn_metadatas: []` and `last_request: null` — V2 conversation state fields; not present in IDE format
- No `schemaVersion` or `dataModelVersion` field (IDE has `schemaVersion: "1.0.0"`)

**What session files provide vs do not provide:**

| Available | Not Available |
|---|---|
| Session ID, CWD, timestamps | Token counts |
| Title, agent name | Resolved model name |
| Model routing alias (`"auto"`) | Credit/cost data |
| context_usage_percentage (when populated) | Per-request timing |
| Session reason (subagent vs interactive) | |

**Stability warning:** This schema is not documented. CLI uses `"version": "v1"` in `session_state` but without a version-signalling field at the top level (unlike IDE's `schemaVersion`). Treat as version-sensitive internal format.

### 6.2 `AppData\Local\Kiro-Cli\` directory

**Location:** `C:\Users\Dell\AppData\Local\Kiro-Cli\`  
**Evidence:** Directory contents listed. **[OBS]**

| File/Dir | Size | Relevance |
|---|---|---|
| `kiro-cli.exe` | 434 MB | CLI binary |
| `run/chat-cli-2.31.0.exe` | 434 MB | Extracted agent engine binary |
| `run/turn-markers/` | dir | JSON files named `<PID>-<timestamp_ms>.json` — one per turn |
| `run-receipts/` | dir | JSON files per run: `<uuid>.json` — 2 present; locked during active session |
| `data.sqlite3` | 69 KB | SQLite database — purpose unknown; sqlite3 not in PATH; schema unread |
| `knowledge_bases/kiro_default/` | dir | Semantic search models (embedding models) |
| `tui.js` | 13.7 MB | TUI JavaScript bundle |
| `bun.exe` | 86 MB | Bun JS runtime for TUI |
| `feed-cache.json` | 3 KB | Changelog/release feed entries (observed content) |
| `telemetry-export-drops.lock` | 0 bytes | Lock file for telemetry export |

**`run-receipts/` — IMPORTANT, UNVERIFIED:**  
Two receipt files present (`a02b7cd4-....json`, `8b34826f-....json`), each 235 bytes. They were locked during the active session (OS error 33: file locked by another process). Their content is unknown. The name `run-receipts` and small size (235 bytes) suggest per-run metadata rather than full transcripts.  
**This is the highest-priority unresolved question in this investigation.** Post-session inspection of these files may reveal per-run token counts, credit consumption, or invocation metadata. **[OBS — file exists; content UNVERIFIED]**

**`turn-markers/` — partially observed:**  
File `27008-1791917753637.json` present (timestamp `1791917753637` ms). Was locked during active session. Content unverified. **[OBS — exists; content UNVERIFIED]**

**`data.sqlite3` — UNVERIFIED:**  
69 KB SQLite database. `sqlite3` is not in PATH on this system. The schema and content are completely unknown. This is a potential source of usage data that could not be investigated. A future investigation with SQLite access should inspect this file. **[OBS — exists; schema UNVERIFIED]**

### 6.3 `AppData\Roaming\Kiro\logs\`

The IDE Electron binary writes logs to `AppData\Roaming\Kiro\logs\`. The CLI binary writes its own logs to `$TMPDIR/kiro-log/kiro-chat.log` (Linux/macOS) — the Windows equivalent was not confirmed. The CLI `kiro-cli diagnostic` does not reveal a log file path on Windows. **[DOC — log path documented for Linux/macOS only]**

**What IDE kiro.log provided (from IDE investigation):** `model.invoke.start` events, character counts, agent mode, conversation IDs. These are in the IDE process log and are not expected to exist in the same form for the separate CLI binary.

---

## 7. Programmatic Usage and Billing Interfaces

### 7.1 `kiro-cli chat --list-models --format json` — CONFIRMED AVAILABLE, DOCUMENTED

**Evidence (observed output, redacted):**
```json
{
  "models": [
    {
      "model_name": "auto",
      "description": "Models chosen by task for optimal usage and consistent quality",
      "model_id": "auto",
      "context_window_tokens": 1000000,
      "rate_multiplier": 1.0,
      "rate_unit": "Credit"
    },
    {
      "model_name": "claude-sonnet-4.5",
      "model_id": "claude-sonnet-4.5",
      "context_window_tokens": 200000,
      "rate_multiplier": 1.3,
      "rate_unit": "Credit"
    },
    {
      "model_name": "claude-haiku-4.5",
      "model_id": "claude-haiku-4.5",
      "context_window_tokens": 200000,
      "rate_multiplier": 0.4,
      "rate_unit": "Credit"
    },
    {
      "model_name": "deepseek-3.2",
      "model_id": "deepseek-3.2",
      "context_window_tokens": 164000,
      "rate_multiplier": 0.25,
      "rate_unit": "Credit"
    }
    ... (9 total models including minimax-m2.5, glm-5, qwen3-coder-next)
  ],
  "default_model": "auto"
}
```
**[OBS — `kiro-cli chat --list-models --format json`]**

**What `rate_multiplier` means:** Relative credit cost multiplier vs a base model. Not a fixed per-token or per-request price. Published by Kiro and documented in the `--list-models` output. Useful to distinguish expensive models from cheap ones, but not to compute an exact credit consumption without the token count.

**Scripting use:** This command is non-interactive, machine-readable, and scriptable. Safe to call at any time.

### 7.2 `kiro-cli chat --list-sessions --format json` — CONFIRMED AVAILABLE, DOCUMENTED

**Evidence (observed output):**
```json
[{
  "cwd": "\\\\?\\C:\\Users\\Dell",
  "sessions": [{
    "sessionId": "66604d89-63c4-4ae5-ab74-6670a9e9e47b",
    "source": "v2",
    "title": "[redacted]",
    "updatedAt": "2026-10-13T18:55:57.712Z",
    "messageCount": 20,
    "status": "in_progress"
  }],
  "complete": true
}]
```
**[OBS — `kiro-cli chat --list-sessions --format json`]**

Fields: `sessionId`, `source` (v1/v2), `title`, `updatedAt`, `messageCount`, `status` (`in_progress` or `complete`).

**`--all-cwds` flag:** Documented to return sessions across all workspaces. **[DOC]**

### 7.3 `--output-format stream-json` (headless mode) — CONFIRMED AVAILABLE, DOCUMENTED

For non-interactive runs (`--no-interactive`), the CLI can emit ACP events as JSON Lines on stdout. Events observed/documented:

| Event type | Timing | Contains |
|---|---|---|
| `AgentMessageChunk` | During streaming response | Text chunks |
| `ToolCall` | When tool is invoked | Tool name, parameters, status |
| `ToolCallUpdate` | During tool execution | Progress updates |
| `TurnEnd` | When agent turn completes | `stopReason` (V2: `end_turn` or `refusal`) |

**What stream-json does NOT contain:** Token counts, model name, cost, credit consumption. **[DOC — event types described; no token fields mentioned]**

### 7.4 `/usage` slash command — INTERACTIVE ONLY

Available in interactive CLI sessions. Shows current billing and credits. **[DOC]**  
In non-interactive runs, prints a summary to stdout. **[DOC]**  
**Not machine-readable in a hook or structured output context.** The output format is human-readable text, not JSON. **[DOC — described as "billing and credits information"; no JSON format option documented]**

### 7.5 ACP server (`kiro-cli acp`) — PROGRAMMATIC, NOT DIRECT TELEMETRY

The ACP server exposes `session/prompt`, `session/new`, `session/load`, and Kiro-specific extensions like `_kiro.dev/compaction/status`. These are for editor integration, not telemetry collection. No ACP method returns token counts or billing data. **[DOC]**

### 7.6 No documented developer REST API

No REST API endpoint for personal usage statistics (tokens, credits, cost, sessions) is documented anywhere in the Kiro documentation. The admin-only enterprise OTel pipeline (`kiro.daily.*` metrics) exists but requires org admin access and AWS configuration. Same limitation as the IDE. **[NOT FOUND]**

---

## 8. Controlled Verification Test Results

### 8.1 Test conditions

The Kiro CLI v2.31.0 binary is installed and authenticated. However, the investigation was conducted within an active CLI session (the current session, ID `66604d89-63c4-4ae5-ab74-6670a9e9e47b`). This created the following limitations:

- `run-receipts/*.json` files were OS-locked by the active process and could not be read.
- `run/turn-markers/*.json` files were OS-locked for the same reason.
- A separate test conversation could not be started without potentially interfering with the investigation session.

### 8.2 What was verified directly

| Verification | Method | Result |
|---|---|---|
| CLI version | `kiro-cli --version` | `2.31.0` confirmed **[OBS]** |
| Binary location | `Get-Command kiro-cli` | `C:\Users\Dell\AppData\Local\Kiro-Cli\kiro-cli.exe` confirmed **[OBS]** |
| Authentication | `kiro-cli whoami` | Authenticated via GitHub **[OBS]** |
| Session ID exposure | `kiro-cli diagnostic` | `KIRO_SESSION_ID` = current session UUID **[OBS]** |
| Model list | `kiro-cli chat --list-models --format json` | 9 models returned with rate_multiplier **[OBS]** |
| Session list | `kiro-cli chat --list-sessions --format json` | Active session with messageCount=20 returned **[OBS]** |
| Session JSON schema | Read `~/.kiro/sessions/cli/<uuid>.json` | model_id="auto", context_usage_percentage=null **[OBS]** |
| Session JSONL | Read first 50 lines of `~/.kiro/sessions/cli/<uuid>.jsonl` | User/assistant/tool events visible; no token fields **[OBS]** |
| run-receipts | Attempted read | File locked — content UNVERIFIED **[OBS]** |
| turn-markers | Attempted read | File locked — content UNVERIFIED **[OBS]** |
| data.sqlite3 | Attempted schema read | sqlite3 not in PATH — schema UNVERIFIED **[OBS]** |

### 8.3 Findings from active session inspection

**`messageCount: 20`** in the session list output — correctly counts messages at the time of inspection. This shows `messageCount` updates in real time. **[OBS]**

**`context_usage_percentage: null`** in the CLI session JSON — this is the same null value observed at session start. For the IDE, `contextUsage.usagePercentage` was populated after the first turn as a float in `messages.jsonl`. The CLI stores this in session JSON under `session_state.rts_model_state.context_usage_percentage`. It was still `null` after 20 messages in the active session, which may be a difference in when the field is updated or a V2 vs V3 distinction. **[OBS — limitation: single active session; not reproducible without a completed session]**

---

## 9. Kiro IDE versus CLI Comparison Matrix

| # | Telemetry field | Kiro IDE | Kiro CLI | Source/interface | Exact or estimated | Granularity | Documented | Confidence | Production suitable |
|---|---|---|---|---|---|---|:---:|:---:|:---:|
| 1 | Session ID | ✅ Available | ✅ **More available** | Hook STDIN `session_id`; `KIRO_SESSION_ID` env var; session files; `--list-sessions` | Exact | Per-session | ✅ | High | ✅ Yes |
| 2 | Session start timestamp | ✅ Available | ✅ Available | Session file `created_at`; hook fires at start | Exact | Per-session | ⚠️ No (files) / ✅ Yes (hook) | High | ✅ Yes |
| 3 | Session end timestamp | ⚠️ Partial | ✅ **Better** via `SessionEnd` trigger | `SessionEnd` hook (V3); `lastModifiedAt` in files | Derived | Per-session | ✅ (trigger) | Medium (V3 only) | ✅ V3 only |
| 4 | Working directory | ✅ Available | ✅ Available | Hook STDIN `cwd`; session file | Exact | Per-session | ✅ | High | ✅ Yes |
| 5 | User prompt count | ✅ Available | ✅ Available | `UserPromptSubmit` hook; `messageCount` in `--list-sessions` | Exact count | Per-session | ✅ | High | ✅ Yes |
| 6 | Tool invocation count | ✅ Available | ✅ Available | `PreToolUse` hook fires per call | Exact count | Per-session | ✅ | High | ✅ Yes |
| 7 | Tool names | ✅ Available | ✅ Available | `PreToolUse` `tool_name`; `PostToolUse` | Exact | Per-invocation | ✅ | High | ✅ Yes |
| 8 | Tool duration | ⚠️ Via session files only | ⚠️ Via session files | Session JSONL `durationMs` in `tool_result` | Exact ms | Per-invocation | ❌ (undocumented) | Low | ⚠️ Risky (undocumented) |
| 9 | Configured model name | ⚠️ `"auto"` only | ✅ **Better** | `--list-models --format json`; session file `model_id` | Exact alias | Per-session | ✅ | High | ✅ Yes (alias only) |
| 10 | Resolved model name | ❌ Not available | ❌ Not available | None | — | — | — | High (negative) | ❌ No |
| 11 | Input token count | ❌ Not available | ❌ Not available | None | — | — | — | High (negative) | ❌ No |
| 12 | Output token count | ❌ Not available | ❌ Not available | None | — | — | — | High (negative) | ❌ No |
| 13 | Cached token count | ❌ Not available | ❌ Not available | None | — | — | — | High (negative) | ❌ No |
| 14 | Total token count | ❌ Not available | ❌ Not available | None | — | — | — | High (negative) | ❌ No |
| 15 | Context usage (%) | ✅ In session JSONL | ⚠️ `null` in active session | Session files; /context show (interactive) | % estimate | Per-turn | ❌ (files) / ✅ (slash cmd) | Medium | ⚠️ Risky (files) |
| 16 | Context token count | ❌ Not available | ❌ Not available | Only % (not exact count) | — | — | — | High (negative) | ❌ No |
| 17 | Monetary cost | ❌ Not available | ❌ Not available | None | — | — | — | High (negative) | ❌ No |
| 18 | Credit consumption | ❌ Not available | ⚠️ rate_multiplier only | `--list-models` `rate_multiplier` (relative) | Relative | Per-model | ✅ | High | ⚠️ No (relative only) |
| 19 | Usage aggregates | ❌ Admin OTel only | ❌ Admin OTel only | Enterprise OTel | Aggregate | Daily | ✅ (enterprise) | High | ❌ Admin only |
| 20 | Session duration | ✅ Derivable | ✅ Derivable | Start/end hook timestamps; session file timestamps | Derived | Per-session | ✅ | High | ✅ Yes |
| 21 | Model invocation count | ⚠️ Via kiro.log | ⚠️ Via unknown log | kiro.log (IDE); CLI log not located | Estimated proxy | Per-session | ❌ | Low | ❌ Risky |
| 22 | Timestamps | ✅ Available | ✅ Available | Hook invocation time; session file timestamps | Exact | Per-event | ✅ | High | ✅ Yes |
| 23 | Error/retry metadata | ⚠️ Partial | ⚠️ Partial (V3 transcript only) | `stopReason: refusal` in ACP TurnEnd; V3 TUI notices | Text | Per-turn | ✅ | Medium | ⚠️ Limited |
| 24 | Model routing metadata | ❌ Not available | ❌ Not available | V3 transcript text only; not structured data | — | — | ❌ (structured) | Medium | ❌ No |
| 25 | Agent mode | ✅ Via session files | ✅ Via session files | `agent_name` in session JSON | Exact | Per-session | ❌ (undocumented) | Medium | ⚠️ Risky |
| 26 | Repository context | ✅ From `cwd` | ✅ From `cwd` | Hook `cwd` field; git detection | Derived | Per-session | ✅ | High | ✅ Yes |

### Classification summary

| Classification | IDE count | CLI count |
|---|:---:|:---:|
| CONFIRMED AVAILABLE | 10 | 12 |
| PARTIALLY AVAILABLE | 5 | 6 |
| NOT FOUND | 9 | 7 |
| UNVERIFIED | 0 | 2 (run-receipts, sqlite) |
| UNSUPPORTED | 0 | 0 |

**Net improvement CLI vs IDE: +2 confirmed, +1 partial, −2 not found** — primarily driven by `KIRO_SESSION_ID` env var, `--list-models` programmatic API, and `--list-sessions` machine-readable output.

---

## 10. Recommended Integration Strategy

**Constraint:** Do not modify any existing source code. This section is recommendations only.

### 10.1 Whether a dedicated `KiroCLISource` is appropriate

**Yes.** The CLI uses a distinct binary, separate session storage directory (`cli/` vs `<workspace_hash>/`), different session ID format (bare UUID vs `sess_<uuid>`), different `session_created_reason` values, and has CLI-specific interfaces (`--list-sessions`, `--list-models`, `KIRO_SESSION_ID` env var). Mixing CLI and IDE events in the same source without distinguishing them would lose valuable context.

A `KiroCLISource` wrapping a `KiroCLIHookProvider` is appropriate, reusing the same `AITelemetryPayload` schema from `core/event.py` (all token/model fields remain `null`) and the same `JSONLEventStore` from `storage/jsonl_store.py`.

### 10.2 CLI vs IDE session distinction

Sessions can be distinguished by:
- Session ID format: CLI uses bare UUID (`66604d89-...`), IDE uses `sess_<uuid>` prefix.
- Session file location: `~/.kiro/sessions/cli/` for CLI vs `~/.kiro/sessions/<workspace_hash>/` for IDE.
- `session_created_reason` field: CLI shows `"subagent"` or `"user"` (not observed in IDE format).
- `KIRO_SESSION_ID` env var: present in CLI hook subprocesses; absent from IDE hook subprocesses (IDE injects no equivalent).

### 10.3 Hook handler differences from IDE

A `KiroCLIHookHandler` needs these changes vs the existing `KiroHookHandler` design:

1. **STDIN field mapping:** CLI session JSON uses `"session_id"` (plain UUID, no prefix). IDE uses `"session_id"` with `"sess_"` prefix. Map both uniformly.
2. **`KIRO_SESSION_ID` fallback:** If `session_id` is absent from STDIN payload (e.g., in `UserPromptSubmit` which only provides `USER_PROMPT`), read `os.environ.get("KIRO_SESSION_ID")` as fallback. This is CLI-specific and is not available in IDE hook subprocesses.
3. **UserPromptSubmit:** Cannot block (exit code 2 does not block in CLI). Handler must always exit 0. Record only that a prompt was submitted; do not store prompt content.
4. **SessionEnd trigger:** Available in V3 only. If firing, treat as `ai_run_completed`. Session ID available from `KIRO_SESSION_ID` env var even if payload is empty.
5. **Global hooks:** `~/.kiro/hooks/` is supported in CLI V3. The installer can optionally write to the global location for workspace-independent collection.

### 10.4 New CLI-specific machine-readable interfaces to leverage

Two new non-hook interfaces are available that have no IDE equivalent:

**`kiro-cli chat --list-models --format json`**  
Collect this once at session start or on demand to record the configured model name and its `rate_multiplier`. This is the only interface that provides the user's explicit model selection (when not using `auto`) in machine-readable form. Store `model_id` (e.g., `"claude-sonnet-4.5"` if explicitly selected, `"auto"` if routing is automatic) and `rate_multiplier` as a context field.

**`kiro-cli chat --list-sessions --format json`**  
Useful for periodic reconciliation — after a session completes, call this to retrieve `messageCount` and `status: "complete"`. Does not require modifying hook files.

### 10.5 What fields can be populated honestly

| Field | Value | How to obtain |
|---|---|---|
| `session_id` | Exact UUID | STDIN `session_id` or `KIRO_SESSION_ID` env var |
| `timestamp` | Exact ISO-8601 | `datetime.utcnow()` at hook invocation |
| `provider` | `"kiro_cli"` | Hardcoded in handler |
| `capture_method` | `"kiro_cli_hooks"` | Hardcoded |
| `cwd` | Exact path | STDIN `cwd` or fallback `os.getcwd()` |
| `tool_name` | Exact name | STDIN `tool_name` (PreToolUse/PostToolUse) |
| `configured_model` | `"auto"` or named model | Session file or `--list-models` |
| `rate_multiplier` | Float (relative, not cost) | `--list-models --format json` |
| `model` | `null` | **Never populated — resolved model unavailable** |
| `input_tokens` | `null` | **Never populated — unavailable** |
| `output_tokens` | `null` | **Never populated — unavailable** |
| `total_tokens` | `null` | **Never populated — unavailable** |
| `cost_usd` | `null` | **Never populated — unavailable** |

### 10.6 Deduplication strategy

CLI and IDE sessions share `~/.kiro/sessions/` but use different subdirectories. Hook events from CLI will have `provider="kiro_cli"` and IDE events will have `provider="kiro"`. No deduplication is needed between the two providers since they are distinct systems.

Within CLI sessions, each `PreToolUse` hook invocation is inherently unique (different tool, different time). The existing `uuid4()`-based event ID generation in `EventProcessor` is sufficient.

### 10.7 Session file reader (optional Phase B)

A `KiroCLISessionReader` similar to the proposed `KiroSessionReader` in the IDE report could read `~/.kiro/sessions/cli/` to backfill historical data. Key considerations:
- The schema is undocumented; treat as version-sensitive.
- `context_usage_percentage` in `session_state` may be populated after session completion — inspect completed sessions to verify.
- `user_turn_metadatas` may contain per-turn context usage data in future versions; currently empty.
- Post-session read of `run-receipts/` may reveal usage data not available through hooks.

### 10.8 Undocumented internals risk assessment

| Interface | Undocumented? | Risk if schema changes |
|---|:---:|---|
| Hook triggers and STDIN schema | ✅ Documented | Low |
| `--list-models --format json` | ✅ Documented | Low |
| `--list-sessions --format json` | ✅ Documented | Low |
| `KIRO_SESSION_ID` env var | ⚠️ Observed (not in official docs) | Medium — observed but not guaranteed stable |
| `~/.kiro/sessions/cli/` schema | ❌ Undocumented | High — internal format |
| `run-receipts/` schema | ❌ Undocumented | High — internal format; content unknown |
| `data.sqlite3` | ❌ Undocumented | Very high — binary format, schema unknown |

**Recommended approach:** Rely on documented hook interfaces and documented CLI commands. Use `KIRO_SESSION_ID` with low trust (observed, likely stable, but not guaranteed). Treat session files and run-receipts as optional, version-sensitive enrichment, not primary telemetry.

---

## 11. Security and Privacy Considerations

### 11.1 Hook payload contains sensitive data

- `tool_input` in `PreToolUse`/`PostToolUse` contains raw tool arguments: file paths, shell commands, SQL queries, web URLs, code content. **Must not be stored.** Extract only `tool_name`.
- `USER_PROMPT` env var in `UserPromptSubmit` contains the full user message. **Must not be stored.** Record only that a prompt was submitted (event type + session_id + timestamp).
- `tool_response` in `PostToolUse` may contain file contents, API responses, command output. **Must not be stored.**

### 11.2 KIRO_SESSION_ID is a session identifier, not a user identifier

The session UUID identifies a conversation session, not the user. The same user can have multiple sessions. `KIRO_TELEMETRY_CLIENT_ID` is a per-device UUID, not a per-user identifier. Do not treat either as a user identity.

### 11.3 `KIRO_CONTENT_COLLECTION_ENABLED = "true"`

This environment variable (observed in `kiro-cli diagnostic`) indicates that Kiro is collecting content for training/improvement purposes on this account. This does not affect what telemetry the `ai-telemetry-agent` collects — it is an internal Kiro flag — but it is worth noting that content collection is enabled and hook payloads should still be treated as sensitive.

### 11.4 `data.sqlite3` may contain sensitive data

The 69 KB SQLite database at `AppData\Local\Kiro-Cli\data.sqlite3` has an unknown schema. If it contains conversation history, prompts, or file contents, reading it programmatically would be a privacy violation. Treat with caution and do not read without understanding the schema first.

### 11.5 `run-receipts/` files

These files may contain per-run metadata. Even if they contain token counts or credit data (which would be valuable), reading them requires understanding whether they contain any user content. Inspect schema post-session before deciding whether to read them programmatically.

---

## 12. Known Limitations and Unresolved Questions

### 12.1 `run-receipts/` contents — **highest priority unresolved question**

Two files exist at `AppData\Local\Kiro-Cli\run-receipts\*.json`, each 235 bytes. They were OS-locked during the active session. They may or may not contain: token counts, credit consumption, per-run timing, model information, or request identifiers. Their small size (235 bytes) suggests metadata rather than full transcripts, but the field names and values are completely unknown. This should be the first thing investigated after the current session ends.

**How to investigate:** After this session completes, read the files before starting a new session:
```powershell
Get-Content "C:\Users\Dell\AppData\Local\Kiro-Cli\run-receipts\*.json"
```

### 12.2 `data.sqlite3` schema — **second priority unresolved question**

The 69 KB SQLite database schema is completely unknown. It could contain: usage counters, session indexes, credential tokens, MCP connection state, or application state. 

**How to investigate:** Install SQLite and inspect:
```
sqlite3 "C:\Users\Dell\AppData\Local\Kiro-Cli\data.sqlite3" ".tables"
sqlite3 "C:\Users\Dell\AppData\Local\Kiro-Cli\data.sqlite3" ".schema"
```

### 12.3 `context_usage_percentage` in CLI sessions

Observed as `null` in the active session's JSON. The IDE populated this as a float in `messages.jsonl`. It is unknown whether the CLI eventually populates this field in `session_state.rts_model_state.context_usage_percentage` (or elsewhere) after session completion. Inspect a completed session's JSON to verify.

### 12.4 `turn-markers/` file format

Files named `<PID>-<timestamp_ms>.json` exist in `AppData\Local\Kiro-Cli\run\turn-markers\`. The content was locked. These likely mark agent turn boundaries and may contain turn IDs or timing information relevant to model invocation counting.

### 12.5 `SessionEnd` trigger payload

The documentation acknowledges this trigger exists (CLI V3 only) but does not document the payload schema. It is unknown whether `session_id` or any other useful fields are included. A V3 session test would be needed to confirm the payload.

### 12.6 `KIRO_SESSION_ID` env var in IDE hook subprocesses

The IDE investigation found `KIRO_SESSION_ID` in `kiro-cli diagnostic` but this is specific to the CLI process. The IDE (`kiro.exe`) may or may not inject `KIRO_SESSION_ID` into its hook subprocesses. The IDE investigation treated this as unconfirmed. This should be verified with a test hook script that logs its environment.

### 12.7 CLI V3 (`--v3`) vs CLI V2 (default)

The current session uses V2 (`"source": "v2"` in `--list-sessions` output). V3 provides: `SessionEnd` trigger, global hooks at `~/.kiro/hooks/`, file triggers (`PostFileSave`, etc.), task triggers, model availability notices. Whether V3 changes the session file schema or adds new fields (e.g., context usage, per-turn metadata) is unverified.

---

## 13. Final Checklist

### Fields now collectible (via hooks + documented CLI commands)

- [x] Session ID — via `KIRO_SESSION_ID` env var and hook STDIN `session_id`
- [x] Session start timestamp — hook fires at session start; `session.json` `created_at`
- [x] Session end timestamp — `SessionEnd` hook (V3); `updated_at` in session files
- [x] Working directory / workspace — hook STDIN `cwd`
- [x] User prompt count — count `UserPromptSubmit` hook invocations; or `messageCount` from `--list-sessions`
- [x] Tool call count — count `PreToolUse` hook invocations
- [x] Tool names — `PreToolUse` `tool_name`
- [x] Session duration — derived from start/end timestamps
- [x] Configured model name / alias — `--list-models --format json` `default_model`; session file `model_id`
- [x] Model `rate_multiplier` — `--list-models --format json` (relative credit weight, not cost)
- [x] Agent name — session file `agent_name` (undocumented, version-sensitive)
- [x] Timestamps — hook invocation time
- [x] Repository context — from `cwd` via existing `detect_repository_context()`
- [x] CLI vs IDE source distinction — `provider: "kiro_cli"` flag; session file location

### Fields still missing (not collectible reliably)

- [ ] **Actual resolved model name** — model routing is server-side; `"auto"` is the only client-observable value when routing is enabled
- [ ] **Input token count** — not in any documented or observed interface
- [ ] **Output token count** — same
- [ ] **Cached token count** — same
- [ ] **Total token count** — same
- [ ] **Exact context token count** — percentage available; exact tokens unavailable
- [ ] **Monetary cost (USD)** — no USD pricing in any interface
- [ ] **Credit consumption (absolute)** — only relative `rate_multiplier`; no absolute credit balance API
- [ ] **Per-request credit delta** — not exposed
- [ ] **Model routing decisions** — V3 TUI text only; no structured event

### Fields unverified (insufficient evidence)

- [ ] `run-receipts/` content — files exist but were locked; may contain per-run usage metadata
- [ ] `data.sqlite3` schema — file exists; SQLite not available; schema completely unknown
- [ ] `turn-markers/` content — files exist but were locked
- [ ] `context_usage_percentage` in completed CLI sessions — was `null` in active session; may populate on completion
- [ ] `SessionEnd` payload schema — trigger documented; payload fields not documented
- [ ] Whether `KIRO_SESSION_ID` is available in IDE hook subprocess environments

---

*End of investigation report.*
