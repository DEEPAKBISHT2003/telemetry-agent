# Kiro IDE Telemetry Source Investigation

**Date:** 2026-10-13  
**Kiro version:** 1.0.437 (Electron, Amazon.com)  
**Investigation scope:** What telemetry can be reliably collected from Kiro IDE, and what cannot.

Every finding in this document is backed by a verified source: a file read from the local filesystem, a log line observed in an active session, or an official Kiro documentation page accessed via the introspect agent. Assumptions and unverified items are labelled explicitly.

---

## 1. Executive Summary

Kiro IDE can be integrated with `ai-telemetry-agent` to capture reliable session lifecycle and tool-usage telemetry through its native hook system. However, **model name, input token count, output token count, cached token count, and cost are not available through any documented or observed local interface**. This is a hard limit, not a gap to engineer around.

The investigation found two complementary data sources:

1. **Kiro hooks** (documented) — deliver `session_id`, `cwd`, `tool_name`, and `tool_input` through stdin on `SessionStart`, `PreToolUse`, `PostToolUse`, and `Stop` triggers.
2. **Kiro local session files** (observed, undocumented) — `~/.kiro/sessions/` contains `session.json` and `messages.jsonl` per session. These provide session creation time, workspace paths, agent mode, tool call history with `durationMs`, and a `contextUsage` percentage. They do not contain token counts or resolved model names.

`kiro.log` (`%APPDATA%\kiro\logs\<timestamp>\kiro.log`) logs `model.invoke.start` entries with routing strings (`modelId=auto`, `modelId=simple-task`), not resolved model names. No token counts appear in any observed log entry.

The enterprise OpenTelemetry export provides a `kiro.model.name` attribute in the `kiro.daily.model_messages` metric, but this is an admin-only, daily-aggregate pipeline — not a per-developer, per-session interface.

**Recommended integration approach:** Phase A only — implement hook-based telemetry capturing the verified fields. Represent model, token, and cost as `null`. Add an optional Phase B session-file reader for richer tool call and timing data. Do not attempt Phase C (enterprise OTel) without organisation admin access.

---

## 2. Existing Repository Integration Points

The following components can be reused for Kiro without modification:

| Component | File | Reusable as-is? | Notes |
|---|---|:---:|---|
| Event schema | `core/event.py` | ✅ | `AITelemetryPayload` has all fields as `Optional` — `None` works for unavailable data |
| Event processor | `core/processor.py` | ✅ | Accepts any dict with `event_type`; IDE-agnostic |
| Validation | `core/validation.py` | ✅ | Checks field presence and ISO-8601 timestamps only |
| Identity | `core/identity.py` | ✅ | Reads `~/.telemetry_agent/identity.json`; no IDE coupling |
| Repository detection | `core/repository.py` | ✅ | Runs `git` subprocess against a path; Kiro provides `cwd` in hook payloads |
| JSONL storage | `storage/jsonl_store.py` | ✅ | Append-only, thread-locked, deduplication; IDE-agnostic |
| Storage base | `storage/base.py` | ✅ | Abstract interface only |
| SystemSource | `sources/system.py` | ✅ | Emits lifecycle events; no IDE coupling |
| GitSource | `sources/git.py` | ✅ | Polls watched paths via `git`; IDE-agnostic |
| AIEventBuilder | `sources/ai_provider.py` | ✅ | Static factories; all token/model fields default to `None` |
| MCP probe server | `integrations/mcp_probe/mcp_server.py` | ✅ | Valid stdio MCP server; already documents `can_observe_tokens=False` |
| Antigravity hook handler | `integrations/hooks/hook_handler.py` | ✅ Keep unchanged | Antigravity-specific; must not be modified |
| Antigravity installer | `integrations/hooks/installer.py` | ✅ Keep unchanged | Writes `~/.gemini/config/hooks.json`; unrelated to Kiro |
| Antigravity SQLite reader | `sources/antigravity_sqlite.py` | ✅ Keep unchanged | No Kiro equivalent exists |
| AntigravitySource | `sources/antigravity.py` | ✅ Keep unchanged | Disable via `enabled=False` in Kiro mode |
| CLI query commands | `cli.py` (`events`, `sessions`, `session`, `summary`, `status`) | ✅ | All read from JSONL store; IDE-agnostic |

Components that need **new Kiro-specific counterparts** (not modifications to existing):

| New component | What it replaces for Kiro |
|---|---|
| `integrations/hooks/kiro_hook_handler.py` | Kiro stdin format; different field names and stdout behavior |
| `integrations/hooks/kiro_installer.py` | Writes `.kiro/hooks/telemetry.json` not `~/.gemini/config/hooks.json` |
| `sources/kiro.py` (KiroSource) | AntigravitySource without SQLite; also optionally wraps session file reader |
| `config/settings.py` — add `ide_mode` field | Allows `auto`/`kiro`/`antigravity` selection |
| `core/collector.py` — add `_resolve_ide_mode()` | Switches source list based on detected IDE |

---

## 3. Verified Kiro Hook Capabilities and Payload Fields

**Source:** Official Kiro documentation (kiro.dev/docs/hooks/types.md), verified via introspect agent.

### Hook file format and location

```json
// .kiro/hooks/telemetry.json  (workspace-scoped)
{
  "version": "v1",
  "hooks": [
    {
      "name": "Telemetry - Session Start",
      "trigger": "SessionStart",
      "action": { "type": "command", "command": "python /abs/path/kiro_hook_handler.py SessionStart" },
      "timeout": 10
    }
  ]
}
```

Global scope: `~/.kiro/hooks/<id>.json`. Both scopes are merged.

### Trigger-by-trigger payload inventory

| Trigger | Documented stdin fields | Confirmed absent | Can block? |
|---|---|---|---|
| `SessionStart` | `hook_event_name`, `cwd`, `session_id` | model_name, tokens | No |
| `UserPromptSubmit` | `prompt` (+ `USER_PROMPT` env var) | session_id, model_name, tokens | Yes (exit 2, IDE only) |
| `PreToolUse` | `hook_event_name`, `cwd`, `session_id`, `tool_name`, `tool_input` | model_name, tokens, timestamp | Yes (exit 2) |
| `PostToolUse` | Same as PreToolUse + `tool_response` | execution_time, error_status (unconfirmed) | No |
| `Stop` | Payload structure not fully documented | session_id presence unconfirmed | No |
| `PostFileSave` | `{{filePath}}` template in command string | Full JSON schema not published | No |
| `PostFileCreate` | Same as PostFileSave | Same | No |
| `PostFileDelete` | Same as PostFileSave | Same | No |
| `PreTaskExec` | Payload not documented beyond general pattern | All specific fields unconfirmed | Yes (exit 2) |
| `PostTaskExec` | Not documented | All specific fields unconfirmed | No |

### Exit code behavior (verified)

- `0` — success; stdout forwarded to agent context for `SessionStart` and `UserPromptSubmit`
- `2` — blocks the action for `PreToolUse`, `PreTaskExec`, `UserPromptSubmit` (IDE only)
- Other non-zero — shows warning; for `Stop` trigger causes another agent turn

**Critical difference from Antigravity:** Kiro's `PreToolUse` uses **exit code 2 + STDERR** for blocking. It does **not** use `{"decision": "allow"}` in stdout. Writing that string is harmless but not required. The existing Antigravity `hook_handler.py` must not be used for Kiro hooks — it writes `{"decision": "allow"}` which is the wrong protocol expectation.

### Hook execution limits

- Default timeout: **60 seconds** (IDE), **30 seconds** (CLI)
- Hook subprocesses are separate Python processes — they do **not** share memory with any running daemon
- A failed (non-zero exit) `SessionStart` hook does not block the session

---

## 4. Alternative Data Sources Investigated

### 4.1 Kiro session files (`~/.kiro/sessions/`) — OBSERVED ON THIS MACHINE

**Location (verified):**
```
~/.kiro/sessions/<workspace_hash>/<session_id>/
    session.json
    messages.jsonl
    sub-executions/<turn_id>.jsonl
    publish.cursor
~/.kiro/session-index/<workspace_hash>.jsonl
```

The `<workspace_hash>` is an 8-byte hex string derived from the workspace path (e.g., `d244b0fe051c3fb2`). Multiple sessions per workspace appear as separate `sess_<uuid>` subdirectories.

**`session.json` — verified schema (actual file):**
```json
{
  "schemaVersion": "1.0.0",
  "dataModelVersion": 1,
  "id": "sess_b3b99b3a-34fb-4514-b8e7-f6cc5149d314",
  "title": "Kiro telemetry investigation & report",
  "agentMode": "vibe",
  "workspacePaths": ["c:\\Users\\Dell\\Desktop\\telemetry-agent"],
  "rootPaths": ["c:\\Users\\Dell\\Desktop\\telemetry-agent"],
  "createdAt": "2026-10-13T06:28:24.227Z",
  "lastModifiedAt": "2026-10-13T07:26:54.422Z",
  "modelId": "auto",
  "autopilot": true,
  "semanticReviewEnabled": true,
  "_meta": {
    "kiro": { "policyPreset": ["edit-workspace"], "shellType": "powershell" }
  }
}
```

**`messages.jsonl` — verified message types (actual file):**

```jsonc
// tool_call entry
{"id":"tooluse_TEo-call","timestamp":"2026-10-13T07:22:14.673Z","payload":{
  "type":"tool_call","toolCallId":"tooluse_TEo","toolName":"web_fetch",
  "args":{"url":"https://...","mode":"full"},"status":"approved","kind":"fetch",
  "executionId":"f5b2e5d5-...","actionType":"web_fetch","title":"Fetch URL",
  "_meta":{"kiro":{"agentMode":"vibe","agentSubtaskId":"51c77d8e-...","toolOrigin":"default"}},
  "subExecutionId":"51c77d8e-..."}}

// tool_result entry
{"id":"tooluse_TEo-result","timestamp":"2026-10-13T07:22:14.673Z","payload":{
  "type":"tool_result","toolCallId":"tooluse_TEo","content":"...","success":true,
  "durationMs":0,"executionId":"f5b2e5d5-..."}}

// user message
{"id":"8204eadd-...","timestamp":"2026-10-13T06:30:40.547Z","payload":{
  "type":"user","content":"analyze the complete codebase...","images":[],"documents":[],
  "_meta":{"traceparent":"...","kiro":{"userMessageTag":"prompt_799c8996-..."}}}}

// session_metadata (only observed key: contextUsage)
{"id":"ad401ff8-...","timestamp":"2026-10-13T06:30:49.231Z","payload":{
  "type":"session_metadata","key":"contextUsage",
  "value":{"usagePercentage":2.501199960708618},"executionId":"15c936d7-..."}}

// assistant reasoning (content redacted to "...")
{"id":"a555d6df-...-say","timestamp":"...","payload":{
  "type":"assistant","content":"...","operationType":"Reasoning",
  "executionId":"...","reasoningModelId":"qdev::auto",
  "_meta":{"kiro":{"agentMode":"vibe"}}}}
```

**What session files provide:**
- Session ID (`sess_<uuid>`), title, agentMode, workspace paths, created/modified timestamps
- Full tool call log: tool name, args (including URL, file paths), status, durationMs
- User prompts (full text)
- `contextUsage.usagePercentage` — a float (0–100) representing context window utilization
- Sub-execution JSONL files for subagent turns with the same message structure

**What session files do NOT provide (verified absent):**
- Resolved model name (only routing string `"qdev::auto"` in `reasoningModelId`)
- Input token count
- Output token count
- Cached token count
- Total token count
- Cost data

**Stability warning:** This directory structure is not documented by Kiro. The schema (`dataModelVersion: 1`) may change without notice. Any reader built against it carries forward-compatibility risk.

### 4.2 Kiro application log (`%APPDATA%\kiro\logs\<timestamp>\kiro.log`) — OBSERVED ON THIS MACHINE

**Schema (verified from actual log file):**
```json
{
  "timestamp": "2026-10-13T07:22:46.865Z",
  "level": "info",
  "message": "[q-developer-converse] Sending GenerateAssistantResponse modelId=auto agentMode=vibe origin=AI_EDITOR streaming=true conversationId=e3e81c92-...",
  "conversationId": "e3e81c92-...",
  "rootConversationId": "sess_b3b99b3a-...",
  "turnId": "51c77d8e-...",
  "rootTurnId": "f5b2e5d5-..."
}
```

**What the log provides:**
- `rootConversationId` — the root session ID (matches `session.json`'s `id`)
- `conversationId` — sub-execution conversation ID
- `turnId`, `rootTurnId` — execution unit identifiers
- `model.invoke.start` events — one per model call, timestamped
- Request payload character count and history message count (NOT tokens)
  - e.g., `"Request payload: 197410 chars, 18 history messages"`
- `agentMode` per invocation (vibe, context-gatherer)
- `acp.policy-eval.complete` — per-tool permission decisions with `toolId` and `effect`

**modelId in log (critical finding):**
All observed model references are routing strings, not resolved model names:
- `modelId=auto` — Kiro's automatic model selection alias
- `modelId=simple-task` — another routing alias
- `reasoningModelId: "qdev::auto"` — Q Developer routing prefix

The actual model selected by the AWS CodeWhisperer/Q Developer backend (e.g., Claude 3.7 Sonnet, Gemini) is **not present in any observed local file**. Resolution happens server-side.

**What the log does NOT provide (verified absent after full search):**
- `model.invoke.complete` events — not observed at `info` level
- Token counts — no `inputToken`, `outputToken`, `cachedToken`, `totalToken` fields found
- Cost data
- The resolved model identifier

**Stability warning:** This log format (`%APPDATA%\kiro\logs\`) is not documented. Log directories are named by session start timestamp and are not rotated in a documented pattern. This is an internal diagnostic log, not a public interface.

### 4.3 `%APPDATA%\kiro\User\globalStorage\state.vscdb` — OBSERVED (not read)

This is a SQLite database (155,648 bytes observed). It is Kiro's VS Code-style global state store, analogous to VS Code's `state.vscdb`. The likely contents are IDE settings and extension state, not AI session telemetry. `sqlite3` was not available to inspect the schema. **This source remains unverified** — it may or may not contain relevant telemetry.

### 4.4 `%APPDATA%\kiro\User\globalStorage\kiro.kiroagent\` — OBSERVED

Contains three subdirectories with hash-based names and a `default` directory. No files were present inside them on inspection. This directory is likely a workspace-profile association store. It does not appear to contain session telemetry. **Unverified.**

### 4.5 Local HTTP server or IPC socket — NOT FOUND

No documented local HTTP API or IPC socket for querying session data exists. Kiro uses ACP (Agent Client Protocol) internally, but this is not exposed to external processes through a documented local endpoint. The ACP specification is external (agentclientprotocol.com) and Kiro's server is not documented for developer-side querying.

### 4.6 Developer-facing usage API (REST/GraphQL) — NOT FOUND

No developer-facing API for querying personal usage statistics (model, tokens, cost) is documented anywhere in Kiro's documentation. The admin dashboard exists but is restricted to organisation admins.

### 4.7 Environment variables injected by Kiro into hook subprocesses — NOT FOUND

The only documented injected environment variable is `USER_PROMPT` for `UserPromptSubmit`. No `KIRO_SESSION_ID`, `KIRO_MODEL`, `KIRO_API_KEY`, or equivalent variables are documented. The primary data channel is STDIN JSON.

---

## 5. Metric-by-Metric Availability Matrix

| # | Metric | Kiro hooks | Session files | kiro.log | Enterprise OTel | Verdict |
|---|---|:---:|:---:|:---:|:---:|---|
| 1 | Session identifier | ✅ `session_id` in `SessionStart`, `PreToolUse` | ✅ `id` in `session.json` | ✅ `rootConversationId` | ❌ | **Available** via hooks (primary) |
| 2 | Session start timestamp | ✅ hook fires on start | ✅ `createdAt` in `session.json` | ✅ first `model.invoke.start` | ❌ | **Available** |
| 3 | Session end timestamp | ⚠️ `Stop` hook fires (session_id unconfirmed) | ✅ `lastModifiedAt` updated on close | ❌ not logged | ❌ | **Partially available** |
| 4 | Working directory / workspace | ✅ `cwd` in all documented payloads | ✅ `workspacePaths` in `session.json` | ❌ | ❌ | **Available** |
| 5 | User prompt count | ✅ `UserPromptSubmit` fires per prompt | ✅ count `type:user` lines in `messages.jsonl` | ❌ | ❌ | **Available** (count) |
| 6 | Tool invocation count | ✅ `PreToolUse` fires per tool call | ✅ count `type:tool_call` in `messages.jsonl` | ✅ `acp.policy-eval.complete` | ❌ | **Available** |
| 7 | Tool names and inputs | ✅ `tool_name`+`tool_input` in `PreToolUse` | ✅ `toolName`+`args` in `messages.jsonl` | ✅ `toolId` in policy-eval entries | ❌ | **Available** |
| 8 | Tool outputs / errors | ✅ `tool_response` in `PostToolUse` | ✅ `content`+`success`+`durationMs` in `tool_result` | ❌ | ❌ | **Available** (via PostToolUse or session files) |
| 9 | Model name | ❌ not in any payload | ⚠️ only routing string `"auto"` | ⚠️ only routing string `"auto"` | ⚠️ admin-only, daily aggregate | **Not available** to individual developers |
| 10 | Input token count | ❌ | ❌ | ❌ | ❌ | **Not available** |
| 11 | Output token count | ❌ | ❌ | ❌ | ❌ | **Not available** |
| 12 | Cached token count | ❌ | ❌ | ❌ | ❌ | **Not available** |
| 13 | Total token count | ❌ | ❌ | ❌ | ❌ | **Not available** |
| 14 | Context window usage | ❌ | ✅ `contextUsage.usagePercentage` (%) | ❌ | ❌ | **Available as %, not token count** |
| 15 | Cost / billing | ❌ | ❌ | ❌ | ⚠️ credits only, admin-only | **Not available** to individual developers |
| 16 | Session duration | ✅ derived from `SessionStart`→`Stop` | ✅ `lastModifiedAt` - `createdAt` | ✅ derived from log timestamps | ❌ | **Derivable** |
| 17 | Per-session request count | ✅ count `PreToolUse` firings | ✅ count `model.invoke.start` pattern in log | ✅ via log | ❌ | **Available** (tool calls); LLM invocation count derivable from log |
| 18 | Agent mode (vibe/spec) | ✅ from `hook_event_name` / log | ✅ `agentMode` in `session.json` | ✅ per invocation | ❌ | **Available** |
| 19 | Repository name/branch | ✅ derived from `cwd` via git | ✅ derived from `workspacePaths` | ❌ | ❌ | **Available** (via existing `detect_repository_context()`) |
| 20 | Tool execution duration | ❌ not in hook payload | ✅ `durationMs` in `tool_result` | ❌ | ❌ | **Available** via session files only |

---

## 6. Findings on Model Name, Token Counts, Cached Tokens, and Cost

### Model name — NOT AVAILABLE to individual developers

**Evidence:**
- `session.json` `modelId` field contains `"auto"` — Kiro's routing alias, confirmed in two separate sessions (`sess_b3b99b3a`, `sess_e677d403`)
- `kiro.log` logs `"modelId=auto"` and `"modelId=simple-task"` — both are internal routing strings
- `messages.jsonl` assistant entries contain `"reasoningModelId":"qdev::auto"` — `qdev::` prefix is the Q Developer backend routing namespace, not a model name
- No log entry or file was found containing a resolved model identifier (e.g., `claude-3-7-sonnet-20250219` or `gemini-2.5-pro`)
- The Kiro documentation confirms no hook payload exposes model name
- The enterprise OTel export provides `kiro.model.name` in `kiro.daily.model_messages` but this is: (a) admin-only, (b) daily aggregate not per-session, (c) requires AWS IAM Identity Center configuration

**Conclusion:** Model name resolution happens server-side in the AWS CodeWhisperer/Q Developer infrastructure. The client never receives the resolved model identifier in a logged or accessible local form. There is no path to this data for individual developers at the current time.

### Token counts (input, output, cached, total) — NOT AVAILABLE

**Evidence:**
- Exhaustive search of `messages.jsonl` (682KB of actual session data): no token count fields found
- Exhaustive search of `kiro.log` (392KB of actual session log): only character counts (`"Request payload: 197410 chars"`) and history message counts — not token counts
- `session.json`: no token fields
- Sub-execution JSONL files: tool calls and results only
- `session_metadata` entries in `messages.jsonl`: only `contextUsage.usagePercentage` observed
- Official Kiro documentation: no hook payload contains token counts
- Enterprise OTel: exports message counts and credit counts, not token counts

**Conclusion:** Token counts are not stored locally by Kiro in any accessible form. The context window usage percentage (`contextUsage.usagePercentage`) is available in session files but is a ratio, not a token count, and it does not allow reverse-engineering of actual token values without knowing the exact context limit for the active model.

**Warning:** Do not use request payload character count from `kiro.log` as a proxy for input tokens. Character counts are not token counts — the ratio varies by model, language, and content. Presenting character-count-derived figures as token counts would be fabrication.

### Cached token count — NOT AVAILABLE

No field for cached or prefix-reused tokens was found anywhere. Kiro appears to handle prompt caching transparently at the infrastructure level. Even if caching occurs, it is not surfaced locally.

### Cost — NOT AVAILABLE to individual developers

**Evidence:**
- No cost or billing data found in any local file
- No developer-facing API documented
- Enterprise OTel provides credit counts (`kiro.daily.credits`, `kiro.daily.overage_credits`) but: (a) admin-only, (b) credits are not dollars — no published exchange rate exists for programmatic conversion, (c) daily aggregate only

**Conclusion:** Cost cannot be calculated or estimated without token counts and an authoritative pricing table. Neither is available.

---

## 7. Enterprise OpenTelemetry Findings

**Source:** kiro.dev/docs/enterprise/monitor-and-track/user-activity/opentelemetry.md, accessed via introspect agent.

### What it exports

Only OTLP Sum counters (monotonic, daily aggregates) in the `kiro.daily.*` namespace:

| Metric | Attributes | Notes |
|---|---|---|
| `kiro.daily.credits` | `kiro.user.id`, `kiro.user.name`, `kiro.subscription_tier` | Credit usage |
| `kiro.daily.overage_credits` | Same | Overage usage |
| `kiro.daily.messages` | Same | Total message count |
| `kiro.daily.conversations` | Same | Chat conversation count |
| `kiro.daily.model_messages` | Same + `kiro.model.name` | **Model name available here only** |

### Does it include token counts?

**No.** Token-level counts are not documented as OTel attributes. The export covers message counts and credit consumption only.

### Is model name available here?

Yes — `kiro.daily.model_messages` includes `kiro.model.name`. This is the only interface where a resolved model name is exposed. However:
- It is an aggregate count per model per day per user — not per-session
- It cannot be correlated with a specific session ID
- It is admin-only — requires AWS IAM Identity Center and AWS Secrets Manager configuration
- It cannot be integrated into a per-developer, local-first telemetry tool without org admin involvement

### Can it be correlated with hook events?

Not directly. The OTel export uses `kiro.user.id` and `kiro.user.name` attributes, not session IDs. No documented correlation key connects a `kiro.daily.model_messages` data point to a specific `sess_<uuid>`.

### Can it be practically integrated into this repository?

Only for enterprise customers with admin access. It would require:
1. Org admin setting up an OTLP collector endpoint
2. Configuring an AWS Secrets Manager secret with the endpoint URL and auth header
3. A separate process to receive OTLP metrics and correlate them with the local JSONL store by user ID and date

This is out of scope for a local-first developer tool.

---

## 8. Recommended Minimal Integration Design

### Guiding constraints

1. Hook subprocesses are **isolated processes** — they do not share memory with a running daemon. Events must be written directly to the JSONL store inside each hook subprocess call.
2. The existing `hook_handler.py` (Antigravity) must not be modified.
3. All unavailable fields must be stored as `null` — never as `0` or estimated values.
4. Hook installation must not overwrite existing `.kiro/hooks/` files.

### New files required

```
src/ai_telemetry_agent/
  integrations/hooks/
    kiro_hook_handler.py     NEW — Kiro stdin format; correct exit behavior
    kiro_installer.py        NEW — writes .kiro/hooks/telemetry.json

  sources/
    kiro.py                  NEW — KiroSource + optional KiroSessionFileReader

  sources/kiro_session_reader.py   NEW (Phase B only) — reads ~/.kiro/sessions/

.kiro/
  hooks/
    telemetry.json           CREATED by kiro_installer.py at setup time
  settings/
    mcp.json                 NEW — registers existing mcp_server.py
  steering/
    telemetry-agent.md       NEW — project conventions for the agent
```

### Minimal changes to existing files

**`config/settings.py`** — add one field:
```python
ide_mode: str = "auto"   # "auto" | "kiro" | "antigravity"
```
Parse from `TELEMETRY_IDE_MODE` environment variable.

**`core/collector.py`** — add `_resolve_ide_mode()`:
```python
def _resolve_ide_mode(self) -> str:
    mode = self.settings.ide_mode
    if mode in ("kiro", "antigravity"):
        return mode
    # Auto-detect: .kiro/ in workspace → kiro; ~/.gemini/ → antigravity
    if Path(".kiro").is_dir():
        return "kiro"
    if Path.home().joinpath(".gemini").is_dir():
        return "antigravity"
    return "antigravity"   # backward-compatible default
```
Use `_resolve_ide_mode()` in `__init__` to select the source list.

**`cli.py`** — add three commands: `install-kiro-hook`, `uninstall-kiro-hook`, `kiro-hook-status`. Fix the `source` label in `cmd_events()` to use `ev.payload.get("provider")` instead of hardcoding `"antigravity"`.

### `kiro_hook_handler.py` design

```
Kiro fires SessionStart
  → stdin: {"hook_event_name": "SessionStart", "cwd": "/workspace", "session_id": "sess_..."}
  → kiro_hook_handler.py SessionStart
      session_id = data["session_id"]          # available
      cwd = data.get("cwd") or os.getcwd()
      model = None                              # UNAVAILABLE — never fabricate
      repo_context = detect_repository_context(cwd)
      raw_event = AIEventBuilder.build_run_started(
          provider="kiro", model=None,
          session_id=session_id, capture_method="kiro_hooks",
          repository=repo_dict, metadata={"hook_type": "SessionStart"})
      collector.processor.process_raw(raw_event) → store.save_event(event)
      sys.exit(0)   # ALWAYS exit 0 — telemetry never blocks Kiro
      # DO NOT write {"decision":"allow"} — Kiro does not use this
```

For `PreToolUse`:
```
session_id = data.get("session_id")
tool_name = data.get("tool_name")
# tool_input is available but contains user data — store only tool_name
raw_event = AIEventBuilder.build_tool_call(
    tool_name=tool_name or "unknown_tool",
    session_id=session_id, capture_method="kiro_hooks")
sys.exit(0)
```

For `Stop`:
```
# session_id may or may not be present (Stop payload not fully documented)
raw_event = AIEventBuilder.build_run_completed(
    provider="kiro", model=None,
    session_id=session_id,           # may be None
    input_tokens=None, output_tokens=None, total_tokens=None,
    capture_method="kiro_hooks")
sys.exit(0)
```

### `kiro_installer.py` design

Creates `.kiro/hooks/telemetry.json` inside the current workspace. Does not modify any global file. Idempotent: checks if the file already matches the desired config before writing. Backs up any existing telemetry.json before overwriting.

### Hook configuration written by installer

```json
{
  "version": "v1",
  "hooks": [
    {
      "name": "Telemetry - Session Start",
      "trigger": "SessionStart",
      "action": { "type": "command", "command": "\"python\" \"/abs/path/kiro_hook_handler.py\" SessionStart" },
      "timeout": 10
    },
    {
      "name": "Telemetry - Session Stop",
      "trigger": "Stop",
      "action": { "type": "command", "command": "\"python\" \"/abs/path/kiro_hook_handler.py\" Stop" },
      "timeout": 10
    },
    {
      "name": "Telemetry - Tool Use",
      "trigger": "PreToolUse",
      "action": { "type": "command", "command": "\"python\" \"/abs/path/kiro_hook_handler.py\" PreToolUse" },
      "timeout": 10
    },
    {
      "name": "Telemetry - Post Tool Use",
      "trigger": "PostToolUse",
      "action": { "type": "command", "command": "\"python\" \"/abs/path/kiro_hook_handler.py\" PostToolUse" },
      "timeout": 10
    }
  ]
}
```

### IDE mode detection trade-offs

| Option | Pros | Cons |
|---|---|---|
| `auto` (detect from environment) | Zero config; works immediately after install | Could misdetect if both `.kiro/` and `~/.gemini/` exist |
| Explicit `TELEMETRY_IDE_MODE=kiro` | Deterministic | Requires user configuration |
| Both modes always active | Captures all data | SQLite reader may slow startup if Antigravity not installed |

**Recommendation:** Default to `auto`. Both `.kiro/` and `~/.gemini/` existing simultaneously is unlikely (different products). The detection order `.kiro/` → `~/.gemini/` → `antigravity` is safe and backward-compatible.

### Deduplication for Kiro events

Hook events use `uuid4()` (assigned in `EventProcessor` when no `event_id` is provided). Each hook invocation is inherently unique, so no UUIDv5 deterministic scheme is needed. The existing `_seen_ids` in-memory dedup and disk scan in `JSONLEventStore` are sufficient.

---

## 9. Security, Privacy, and Compatibility Considerations

### What is and is not captured

Kiro hook payloads include `tool_input` for `PreToolUse`. The `tool_input` field contains the raw arguments to the tool — for example, file paths, shell commands, and URL strings. **`tool_input` must not be stored.** The hook handler must extract only `tool_name` and discard all arguments.

The existing `sanitize_data()` function in `logging/structured.py` will catch obvious secret patterns (API keys, tokens, Bearer strings) but is not a complete substitute for intentionally discarding `tool_input` entirely.

The `UserPromptSubmit` trigger delivers the full user prompt text. Storing prompts would violate the project's stated "metadata only" privacy principle. The `UserPromptSubmit` hook should record only that a prompt was submitted (event type + session_id) and not store the prompt content.

### Concurrent writes

Both the daemon (if running) and hook subprocesses write to the same `telemetry-YYYY-MM-DD.jsonl` file. `threading.Lock` in `JSONLEventStore` is intra-process only. Two separate Python processes appending to the same file concurrently is generally safe on modern OS + filesystems for small writes (single `write()` call under ~4KB), but is not guaranteed. This risk exists for Antigravity too and is pre-existing.

### Session files are not a stable interface

The `~/.kiro/sessions/` schema is internal and undocumented. `dataModelVersion: 1` suggests versioning exists, implying future schema changes. Any Phase B reader must:
- Handle missing fields gracefully
- Not assume the directory structure is stable across Kiro versions
- Not fail if the session directory is absent (e.g., cloud sessions or CLI sessions)

### Hook timeout risk

Each hook subprocess must complete within 10 seconds (recommended value). The handler performs: `load_settings()` → `TelemetryCollector.__init__()` (which loads `JSONLEventStore` and reads the last 3 JSONL files) → `process_raw()` → `save_event()`. On a cold start with large JSONL files, this chain could approach the timeout. Mitigation: use a lightweight `EventProcessor` + `JSONLEventStore` directly rather than a full `TelemetryCollector` (which initializes all sources unnecessarily in hook mode).

### `.env` file contains a PyPI token

The committed `.env` file at the project root contains `TWINE_PASSWORD=pypi-AgEI...`. This token should be revoked and removed from the repository. This is unrelated to Kiro integration but is a live security risk.

---

## 10. Testing and Validation Plan

### Phase A tests (hook-based telemetry)

#### 10.1 Hook payload parsing

For each trigger, test with verified payload shapes:

```python
# Fixture: verified SessionStart payload
SESSIONSTART_PAYLOAD = {
    "hook_event_name": "SessionStart",
    "cwd": "/workspace/test-project",
    "session_id": "sess_abc123-def456-789"
}

# Fixture: verified PreToolUse payload
PRETOOLUSE_PAYLOAD = {
    "hook_event_name": "PreToolUse",
    "cwd": "/workspace/test-project",
    "session_id": "sess_abc123-def456-789",
    "tool_name": "web_fetch",
    "tool_input": {"url": "https://example.com", "mode": "full"}
}

# Fixture: PostToolUse payload (documented)
POSTTOOLUSE_PAYLOAD = {
    **PRETOOLUSE_PAYLOAD,
    "hook_event_name": "PostToolUse",
    "tool_response": "...result..."
}

# Fixture: Stop payload (fields not fully documented — test with minimal)
STOP_PAYLOAD_MINIMAL = {}
STOP_PAYLOAD_WITH_SESSION = {"session_id": "sess_abc123-def456-789"}
```

Test cases:
- `test_sessionstart_extracts_session_id_and_cwd` — verify session_id and cwd are read
- `test_sessionstart_detects_repository_from_cwd` — verify git detection runs against cwd
- `test_pretooluse_extracts_tool_name_only` — verify tool_input is NOT stored
- `test_posttooluse_records_tool_name_and_phase` — verify PostToolUse captured separately
- `test_stop_with_no_session_id_stores_null` — verify null session_id handled
- `test_model_is_always_null` — verify no model name appears in any stored event

#### 10.2 Missing and optional fields

- `test_missing_session_id_returns_null_not_error` — Stop payload missing session_id
- `test_missing_cwd_falls_back_to_getcwd` — payload without cwd
- `test_empty_stdin_handled_gracefully` — empty or malformed JSON input
- `test_non_json_stdin_handled_gracefully` — plain text on stdin

#### 10.3 Malformed payloads

- `test_malformed_json_stdin_exits_0` — handler must not crash; must exit 0
- `test_unknown_hook_type_exits_0` — e.g., "PostFileCreate" passed as hook_type arg

#### 10.4 Session correlation across subprocess calls

- `test_events_from_separate_processes_share_session_id` — two hook invocations with the same session_id produce events with the same context.session_id
- `test_session_id_propagated_to_stored_event` — verify session_id flows from stdin → raw_event → processor → stored event context

#### 10.5 Event persistence and concurrent writes

- `test_two_hook_processes_write_to_same_jsonl` — spawn two hook handler processes, verify both events are stored and no lines are corrupted
- `test_save_event_with_lock_under_concurrent_writes` — unit test for `JSONLEventStore` under threading (existing code; verify it covers the hook path)

#### 10.6 Duplicate event prevention

- `test_same_hook_invocation_idempotent` — running the hook handler twice with identical inputs (same session_id, same timestamp) produces only one stored event (uuid4 IDs will differ — this tests that no artificial deduplication is needed since each invocation is unique by design)
- `test_uuidv4_event_id_is_unique_per_invocation` — verify two identical hook calls produce different event IDs

#### 10.7 Hook installation and uninstallation

- `test_install_creates_telemetry_json` — verify `.kiro/hooks/telemetry.json` created with correct structure
- `test_install_idempotent` — running install twice does not duplicate hooks
- `test_install_does_not_overwrite_other_hook_files` — other `.kiro/hooks/*.json` files are untouched
- `test_uninstall_removes_only_telemetry_json` — other hook files preserved
- `test_hook_status_detects_installed_handler` — verify handler path validity check

#### 10.8 Antigravity compatibility

- `test_antigravity_handler_unchanged` — run existing hook_handler.py tests (once written) with Antigravity payloads
- `test_antigravity_source_unaffected_in_kiro_mode` — when `ide_mode=kiro`, AntigravitySource starts with `enabled=False`
- `test_kiro_mode_does_not_read_gemini_sqlite` — no SQLite database access in Kiro mode

#### 10.9 Unavailable usage data representation

- `test_model_stored_as_null` — `payload.model` is `None` in all Kiro events
- `test_input_tokens_stored_as_null` — `payload.input_tokens` is `None`
- `test_output_tokens_stored_as_null` — `payload.output_tokens` is `None`
- `test_total_tokens_stored_as_null` — `payload.total_tokens` is `None`
- `test_cost_stored_as_null` — `payload.cost_usd` is `None`
- `test_sessions_command_shows_no_token_data_for_kiro` — `has_token_data=False` for Kiro sessions
- `test_summary_command_handles_null_model` — no crash when `models` set is empty for Kiro sessions

### Phase B tests (session file reader, if implemented)

- `test_session_json_reader_extracts_id_and_timestamps` — parse verified schema
- `test_session_json_reader_handles_missing_modelid` — `modelId` absent or `"auto"`
- `test_messages_jsonl_tool_call_reader` — extract tool_name, durationMs
- `test_messages_jsonl_context_usage_reader` — extract usagePercentage as float
- `test_session_reader_handles_sub_executions` — traverses `sub-executions/` directory
- `test_session_reader_skips_unparseable_lines` — malformed JSONL lines silently skipped
- `test_session_reader_does_not_store_prompt_content` — user message content not stored
- `test_session_file_schema_version_check` — logs warning if `dataModelVersion` changes

---

## 11. Explicitly Unsupported or Unverified Capabilities

| Capability | Status | Reason |
|---|---|---|
| Resolved model name (e.g., Claude 3.7 Sonnet) | **Not available** | Only routing strings (`auto`, `simple-task`) in all local files; resolution happens server-side |
| Input token count | **Not available** | Not in any observed local file or documented hook payload |
| Output token count | **Not available** | Same |
| Cached/prefix token count | **Not available** | Same |
| Total token count | **Not available** | Same |
| Cost in USD | **Not available** | No token counts + no published exchange rate for Kiro credits |
| `session_id` in `Stop` hook payload | **Unverified** | Stop payload schema not fully documented; may or may not contain it |
| `tool_input` argument values (privacy) | **Intentionally excluded** | Contains user data; must not be stored |
| User prompt content (privacy) | **Intentionally excluded** | Violates "metadata only" principle |
| `state.vscdb` contents | **Unverified** | File exists (155KB SQLite); schema not inspected; may contain relevant state |
| `kiro.kiroagent/` globalStorage | **Unverified** | Directories observed but empty on inspection; unknown schema |
| Enterprise OTel `kiro.model.name` | **Technically possible** but out of scope | Admin-only; daily aggregate; not per-session; requires org setup |
| `kiro.log` as a stable data source | **Not recommended** | Internal diagnostic log; undocumented format; no stability guarantee |
| `~/.kiro/sessions/` as a stable data source | **Not recommended for production** | `dataModelVersion: 1` implies versioning; schema may change |

---

## 12. Prioritised Implementation Plan

### Phase A — Reliable hook-based telemetry (implement first)

These changes deliver verified, stable telemetry using Kiro's documented interfaces. No dependency on undocumented internals.

**A1. `src/ai_telemetry_agent/integrations/hooks/kiro_hook_handler.py`** (new)  
Implement Kiro hook stdin parsing. Map `SessionStart`→`ai_run_started`, `Stop`→`ai_run_completed`, `PreToolUse`/`PostToolUse`→`ai_tool_call`. Extract `session_id`, `cwd`, `tool_name` only. Store all token/model fields as `null`. Always exit 0. Do not write `{"decision":"allow"}` to stdout.

**A2. `src/ai_telemetry_agent/integrations/hooks/kiro_installer.py`** (new)  
Write `.kiro/hooks/telemetry.json` with v1 schema hooks for `SessionStart`, `Stop`, `PreToolUse`, `PostToolUse`. Idempotent. Backup before overwrite. Uninstall removes only this file.

**A3. `src/ai_telemetry_agent/sources/kiro.py`** (new)  
`KiroSource(EventSource)` with `KiroHookProvider` in-memory buffer. No `SQLiteTokenProvider`. No `commit_state()` needed. `KIRO_AI_TELEMETRY_STATUS = "HOOKS_ONLY_NO_TOKEN_DATA"`.

**A4. `config/settings.py`** (small addition)  
Add `ide_mode: str = "auto"` to `Settings`. Parse `TELEMETRY_IDE_MODE` env var in `load_settings()`.

**A5. `core/collector.py`** (small addition)  
Add `_resolve_ide_mode()`. Conditionally instantiate `KiroSource` vs `AntigravitySource` in `__init__` based on resolved mode. Log `ide_mode` on startup.

**A6. `cli.py`** (small additions)  
Add `install-kiro-hook`, `uninstall-kiro-hook`, `kiro-hook-status` commands. Fix provider label in `cmd_events()`. Optionally: add `--ide kiro|antigravity` flag to `install` command.

**A7. `.kiro/settings/mcp.json`** (new config file)  
Register existing `mcp_server.py` as a Kiro MCP server for workspace-scoped diagnostics.

**A8. `.kiro/steering/telemetry-agent.md`** (new)  
Document event schema conventions, provider field values, and the null-for-unavailable-data rule for the agent.

---

### Phase B — Session file reader (implement only after Phase A is stable)

These changes read from `~/.kiro/sessions/` — an undocumented internal format. They provide richer data (tool duration, context usage percentage, user prompt count, LLM invocation count) but carry forward-compatibility risk.

**B1. `src/ai_telemetry_agent/sources/kiro_session_reader.py`** (new)  
Implement `KiroSessionReader` that scans `~/.kiro/sessions/<workspace_hash>/` for new session files. Extract from `session.json`: `id`, `createdAt`, `lastModifiedAt`, `workspacePaths`, `agentMode`. Extract from `messages.jsonl`: tool_call count, tool names, durationMs per tool_result, contextUsage percentage, user message count. Do NOT store prompt content. Track read position via a cursor file (analogous to the Antigravity SQLite cursor pattern).

**B2. Add `KiroSessionReader` to `KiroSource`**  
Wire the reader into `KiroSource.collect()` as an additional sub-provider.

**B3. Capability probe**  
Add a `KiroCapabilityProbe` that checks for `~/.kiro/` existence and `schemaVersion`/`dataModelVersion` compatibility before enabling the reader.

---

### Phase C — Enterprise OpenTelemetry (implement only with org admin access)

This phase is only applicable to teams with Kiro enterprise subscriptions and AWS IAM Identity Center admin access.

**C1. `src/ai_telemetry_agent/sources/kiro_otel_receiver.py`** (new)  
Implement an OTLP metrics receiver that accepts `kiro.daily.model_messages` metric data points. Store as `EventType.AI_RUN` events with `model` populated from `kiro.model.name` attribute and token fields as `null` (credit counts are not tokens).

**C2. OTLP endpoint configuration**  
Add `KIRO_OTEL_ENDPOINT` and `KIRO_OTEL_TOKEN` to `Settings`. Document that these require enterprise admin provisioning.

**C3. Correlation strategy**  
Daily aggregate data can be correlated with hook events by `kiro.user.id` + date. Implement a best-effort join in `get_sessions()` to annotate sessions on a given date with the model name from OTel if available.

---

*End of investigation report.*
