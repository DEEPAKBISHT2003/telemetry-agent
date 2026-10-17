# Kiro CLI SQLite and Session Telemetry Analysis

**Investigation Date:** 2026-10-14  
**Kiro CLI Version:** 2.31.0  
**Investigator:** AI Telemetry Agent (automated read-only investigation)  
**Scope:** `C:\Users\Dell\AppData\Local\Kiro-Cli\data.sqlite3` and `C:\Users\Dell\.kiro\sessions\`  
**Prior Report:** `KIRO_CLI_RUN_RECEIPTS_ANALYSIS.md` (same session)

---

## 1. Investigation Method and Environment

### Tools Used

- **Python 3.x** built-in `sqlite3` module (read-only mode via `file:path?mode=ro` URI)
- File system enumeration via PowerShell `Get-ChildItem`
- JSON parsing via Python `json` module
- JavaScript bundle text search via Python `re` module on `tui.js`

### Safety Precautions

- All database access used read-only URI mode (`?mode=ro`)
- Database was copied to `data_copy.sqlite3` before inspection; the original was not touched
- No INSERT, UPDATE, DELETE, or schema changes were performed
- Sensitive credential fields (tokens, keys) were redacted and not reproduced here
- AI-generated prompt text and response content were not read or reproduced

### Database Copy

| File | Path | Size | Copy Time |
|---|---|---|---|
| Original | `C:\Users\Dell\AppData\Local\Kiro-Cli\data.sqlite3` | 69,632 bytes | live (monitored) |
| Copy 1 | `C:\Users\Dell\Desktop\telemetry-agent\data_copy.sqlite3` | 69,632 bytes | 2026-10-14T01:07 UTC+5:30 |
| Copy 2 | `C:\Users\Dell\Desktop\telemetry-agent\data_copy2.sqlite3` | 69,632 bytes | 2026-10-14T01:22 UTC+5:30 (refresh) |

Both copies produced identical row counts, confirming the database state was stable across the inspection window.

---

## 2. SQLite Database Schema

### Environment

| Property | Value |
|---|---|
| SQLite version | 3.49.1 |
| Integrity check | `ok` |
| Journal mode | `delete` (not WAL) |
| Page size | 4,096 bytes |
| Page count | 17 |
| Total size | 69,632 bytes (68 KB) |
| WAL file | Not present |
| SHM file | Not present |

### Tables

| Table | Primary Key | Row Count | Purpose |
|---|---|---|---|
| `conversations` | `key TEXT` | **0** | Key-value store for conversations (legacy/empty) |
| `conversations_v2` | `(key TEXT, conversation_id TEXT)` | **0** | Conversation blobs with timestamps |
| `state` | `key TEXT` | **6** | Application runtime state (auth, telemetry identity) |
| `auth_kv` | `key TEXT` | **1** | Authentication tokens |
| `history` | `id INTEGER` | **0** | Shell command history (CLI subcommands) |
| `migrations` | `id INTEGER` | **11** | Schema version tracking |
| `pinned_bin_versions` | `version TEXT` | **1** | Pinned binary version record |
| `extracted_kas_versions` | `version TEXT` | **1** | Extracted KAS binary version record |

### Table DDL

#### `conversations_v2`
```sql
CREATE TABLE conversations_v2 (
    key TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at INTEGER NOT NULL,  -- Unix timestamp in milliseconds
    updated_at INTEGER NOT NULL,  -- Unix timestamp in milliseconds
    PRIMARY KEY (key, conversation_id)
)
```

#### `state`
```sql
CREATE TABLE state (
    key TEXT PRIMARY KEY,
    value BLOB
)
```

#### `history`
```sql
CREATE TABLE history (
    id INTEGER PRIMARY KEY,
    command TEXT,
    shell TEXT,
    pid INTEGER,
    session_id TEXT,
    cwd TEXT,
    start_time INTEGER,
    hostname TEXT,
    exit_code INTEGER,
    end_time INTEGER,
    duration INTEGER
)
```

### Indexes

| Index Name | Definition | Purpose |
|---|---|---|
| `idx_conversations_v2_key_updated` | `(key, updated_at DESC)` | Efficient lookup by key, newest first |
| `idx_conversations_v2_updated_at` | `(updated_at DESC)` | Global newest-first ordering |
| `sqlite_autoindex_auth_kv_1` | Primary key on `auth_kv` | Auto-index |
| `sqlite_autoindex_conversations_v2_1` | Primary key `(key, conversation_id)` | Auto-index |
| `sqlite_autoindex_state_1` | Primary key on `state` | Auto-index |

### Migration History

All 11 migrations (versions 0–10) ran at `2026-10-13T18:46:47Z` — the moment the database was first initialized. No subsequent schema changes were recorded.

---

## 3. Table Contents: Sanitized Record Survey

### `state` table (6 rows)

The `state` table is a general-purpose key-value store for runtime state.

| Key | Type | Content Summary |
|---|---|---|
| `api.codewhisperer.profile` | JSON object | `{arn: str(65), profile_name: str(22), device_code_disabled: bool}` |
| `telemetry-cognito-credentials` | JSON object | AWS Cognito temp credentials (access_key_id, secret_key, session_token, expiration) — **REDACTED** |
| `telemetry-cognito-identity-id` | Text string | AWS Cognito identity pool ID (`us-east-1:...`) — **REDACTED** |
| `telemetry.lastHeartbeatDate.2.31.0` | Text string | Date string `"2026-10-13"` — last heartbeat date |
| `telemetryClientId` | Text string | Telemetry client UUID — **REDACTED** |
| `telemetryUserId` | Text string | Telemetry user identifier — **REDACTED** |

**Telemetry target search result:** No token, model, cost, or credit fields found in any `state` row.

### `auth_kv` table (1 row)

| Key | Value Structure |
|---|---|
| `kirocli:social:token` | JSON: `{access_token, expires_at, refresh_token, telemetry_generation, provider, profile_arn}` |

**Telemetry target search result:** Authentication data only. No usage metrics.

### `conversations` and `conversations_v2` (0 rows)

Both tables are empty in both database copies taken during this investigation. The `conversations_v2` table schema includes fields for `created_at` and `updated_at` timestamps, and a `value TEXT NOT NULL` column which — based on its indexes and schema — is intended to hold JSON conversation blobs keyed by `(key, conversation_id)`.

**Why it is empty:** The `conversations_v2` table appears to be written by the Kiro CLI on conversation save or checkpoint operations. In this investigation, both copies of the database were taken while a Kiro CLI session was active but no explicit save/checkpoint operation was observed. The design suggests rows would be written by the Rust `kiro-telemetry-host` binary, not by the TUI JavaScript process — meaning the TUI has no direct write path. Rows may only appear after session completion or an explicit persistence event.

**tui.js confirmation:** A full text search of `tui.js` (13.6 MB) found **zero occurrences** of `"conversations_v2"`, `"conversations"` as a table name, or any `INSERT INTO conversations` SQL statement. The TUI does not write to `conversations_v2`. The Rust host binary (`kiro-cli.exe`) is the expected writer.

### `history` table (0 rows)

Designed to store shell command history (CLI subcommands executed), not conversation turn data. Empty in this session.

---

## 4. Token Counts

**Classification: NOT FOUND in SQLite database**

No token count fields were found in any row of the `data.sqlite3` database. Specifically:

| Token Field | SQLite Location | Result |
|---|---|---|
| `input_token_count` | Any table | NOT FOUND |
| `output_token_count` | Any table | NOT FOUND |
| `cache_read_input_token_count` | Any table | NOT FOUND |
| `cache_write_input_token_count` | Any table | NOT FOUND |
| `uncachedInputTokens` | Any table | NOT FOUND |
| `totalTokens` | Any table | NOT FOUND |
| `cachedInputTokens` | Any table | NOT FOUND |

**However — CONFIRMED AVAILABLE in session JSON files:**

The investigation discovered that per-turn token counts are persisted in a different location than SQLite:

**Path:** `C:\Users\Dell\.kiro\sessions\cli\{session_id}.json`

Within each session file, the `session_state.conversation_metadata.user_turn_metadatas` array contains one entry per completed turn. Each entry includes:

```json
{
  "input_token_count": 0,
  "output_token_count": 0,
  "cache_read_input_token_count": 0,
  "cache_write_input_token_count": 0,
  ...
}
```

**Important limitation:** In both confirmed session files (sessions `66604d89` and `857dc9f9`), all four token count fields were `0`. This is consistent with the `"model": "auto"` value — when Kiro uses automatic model selection, token counts may not be reported by the backend. Whether specific models (e.g., `anthropic.claude-sonnet-4-5`) populate these fields is **UNVERIFIED** by this investigation, as no such session was available.

**Confidence:** The fields exist in the schema with integer types. Values of 0 indicate the fields are schema-present but not populated for `auto`-model sessions.

---

## 5. Model Identity

**Classification: PARTIALLY AVAILABLE in session JSON files**

Model identity is persisted in session files but at a low resolution.

| Field | Location | Value Found | Notes |
|---|---|---|---|
| `session_state.rts_model_state.model_info.model_id` | session JSON | `"auto"` | Model routing mode, not resolved model |
| `session_state.conversation_metadata.last_context_usage.model_id` | session JSON | `"auto"` | Same |
| `session_state.conversation_metadata.user_turn_metadatas[N].model` | session JSON | `"auto"` | Per-turn model field |
| `modelId` on v3 session files | `sessions/d244b0fe.../sess_*.json` | `"auto"` | Top-level session preference |

**What is persisted:** The `model` field records the model selection mode used for the session, not the resolved model identity. The value `"auto"` means Kiro's server-side routing selected the model dynamically. The actual resolved model identifier (e.g., `anthropic.claude-sonnet-4-5`) is not stored locally in any confirmed field.

**Confidence:** `model: "auto"` is confirmed from two independent sessions. This is the persistent value. The resolved model may appear in different fields under specific conditions but was not observed.

### `rts_model_state` structure

```json
{
  "conversation_id": "66604d89-63c4-4ae5-ab74-6670a9e9e47b",
  "model_info": {
    "model_id": "auto",
    "context_window_tokens": 200000
  },
  "context_usage_percentage": 26.288101
}
```

**Note:** `context_window_tokens: 200000` is the model's context window size — not a usage count.

---

## 6. Context Utilization

**Classification: CONFIRMED AVAILABLE in session JSON files**

Context utilization percentage is persisted in multiple locations within session JSON files:

| Field | Location | Value Found | Precision |
|---|---|---|---|
| `session_state.rts_model_state.context_usage_percentage` | session JSON | `26.288101` / `16.2404` | Float, 4–6 decimal places |
| `session_state.conversation_metadata.last_context_usage.percentage` | session JSON | `26.288101` / `12.4312` | Float |
| `session_state.conversation_metadata.user_turn_metadatas[N].context_usage_percentage` | session JSON | `26.288101` / `12.4312` | Per-turn float |
| `session_state.conversation_metadata.user_turn_metadatas[N].final_context_usage_percentage` | session JSON | `26.288101` / `12.4312` | Final snapshot at turn end |

**Observations:**
- `context_usage_percentage` at the turn level records the percentage at turn start (or mid-turn checkpoint)
- `final_context_usage_percentage` records the value when the turn completed
- `rts_model_state.context_usage_percentage` holds the most recent value (may differ from completed turn values if the session continued)
- The two discrepant values (`12.4312` vs `16.2404` in session `857dc9f9`) confirm that turn-level vs session-level percentages can differ when context grew during a turn

**Evidence:** Two session files confirmed with real floating-point values.

---

## 7. Credit Consumption

**Classification: CONFIRMED AVAILABLE in session JSON files**

Credit consumption is persisted per API request within each turn, in the `metering_usage` array.

### Schema

Each turn's `user_turn_metadatas[N].metering_usage` is an array of objects, one per API call made during the turn:

```json
[
  {"value": 0.06368901774461028, "unit": "credit", "unitPlural": "credits"},
  {"value": 0.1953171578772803,  "unit": "credit", "unitPlural": "credits"},
  ...
]
```

### Confirmed Values

**Session `66604d89` (15 API requests, turn duration 463 seconds):**

| Request # | Credits | Running Total |
|---|---|---|
| 1 | 0.063689 | 0.063689 |
| 2 | 0.195317 | 0.259006 |
| 3 | 0.239443 | 0.498449 |
| 4 | 0.271230 | 0.769679 |
| 5 | 0.549874 | 1.319553 |
| ... | ... | ... |
| 15 | 0.920726 | **≈ 8.358 credits** |

**Session `857dc9f9` (53 API requests, turn duration 850 seconds):**

| metric | value |
|---|---|
| API requests | 53 |
| Total credits | **≈ 12.81 credits** (sum of 53 values) |
| Min per request | 0.0499 credits |
| Max per request | 1.1279 credits |

**Properties of `metering_usage`:**
- One entry per API request (matches `total_request_count` field)
- `value` is a positive float representing credits consumed
- `unit` is always `"credit"` in both observed sessions
- `unitPlural` is always `"credits"`
- Array length equals `total_request_count` in both confirmed sessions
- Does **not** include cost in USD or any monetary currency

**Monetary cost (`cost_usd`):** NOT FOUND anywhere in the local data. Credits are not financial units — the exchange rate between credits and USD is not stored locally.

---

## 8. Correlation to Individual Turns and Sessions

**Classification: CONFIRMED AVAILABLE**

The session JSON files provide stable, unambiguous identifiers for both session and turn granularity.

### Session-Level Identifier

| Field | Type | Example | Stability |
|---|---|---|---|
| Top-level `session_id` | UUID string | `"857dc9f9-5475-41f1-a9f4-3b8d1e335ab9"` | Stable (filename = ID) |
| File name | `{session_id}.json` | `857dc9f9-5475-41f1-a9f4-3b8d1e335ab9.json` | Permanent |
| `rts_model_state.conversation_id` | UUID string | same as `session_id` | Confirmed matching |

The filename, top-level `session_id`, and `conversation_id` are all the same UUID, providing a stable, deduplicated key.

### Turn-Level Identifier

Each entry in `user_turn_metadatas[]` has:

| Field | Type | Example |
|---|---|---|
| `result.Ok.id` | UUID | `"60f4d95d-affe-4664-bd51-04a828af5e43"` |
| `end_timestamp` | ISO datetime | `"2026-10-13T19:03:36.811978900Z"` |
| `loop_id.rand` | Integer | `1880378734` |
| `message_ids` | UUID array | 30+ UUIDs (all messages in the turn) |

There is no standalone `turn_id` field. The closest equivalent is `result.Ok.id` (the AI response message UUID) combined with `end_timestamp`.

### Correlation Matrix

| Link | Feasibility | Method |
|---|---|---|
| Session file ↔ Session | Exact | Filename = top-level `session_id` |
| Turn ↔ Session | Exact | `user_turn_metadatas[]` is nested within session |
| Turn ↔ API request | Exact | `metering_usage[i]` index = i-th API call within the turn |
| Session ↔ Workspace | Confirmed | Top-level `cwd` field in each session file |
| Session ↔ Created time | Confirmed | `created_at` ISO datetime |
| Session ↔ Last modified | Confirmed | `updated_at` ISO datetime |
| Turn ↔ End time | Confirmed | `end_timestamp` ISO datetime in each `user_turn_metadatas` entry |
| Turn ↔ Duration | Confirmed | `turn_duration: {secs, nanos}` |

---

## 9. Complete Field Reference: Session JSON Schema

The session files at `~/.kiro/sessions/cli/{session_id}.json` have the following structure (sanitized):

```
{session_id}.json
├── session_id: UUID string                       ← STABLE SESSION KEY
├── cwd: string                                   ← Working directory
├── created_at: ISO datetime                      ← Session creation time
├── updated_at: ISO datetime                      ← Last modification time
├── title: string | null                          ← Session title (auto-generated)
├── session_created_reason: string                ← e.g. "subagent"
└── session_state
    ├── version: string                           ← Schema version ("v1")
    ├── conversation_metadata
    │   ├── user_turn_metadatas: []               ← PER-TURN RECORDS (one per completed turn)
    │   │   └── [N]
    │   │       ├── loop_id: {agent_id, rand}     ← Loop instance identifier
    │   │       ├── result.Ok.id: UUID            ← AI response message ID
    │   │       ├── result.Ok.role: "assistant"   ← Always "assistant"
    │   │       ├── result.Ok.meta.timestamp: int ← Unix timestamp
    │   │       ├── message_ids: UUID[]           ← All message IDs in this turn
    │   │       ├── total_request_count: int      ← API calls made
    │   │       ├── number_of_cycles: int         ← Agentic cycles
    │   │       ├── builtin_tool_uses: int        ← Tool call count
    │   │       ├── turn_duration: {secs, nanos}  ← Wall-clock duration
    │   │       ├── end_reason: string            ← e.g. "UserTurnEnd"
    │   │       ├── end_timestamp: ISO datetime   ← ← TURN END TIME
    │   │       ├── input_token_count: int        ← Input tokens (0 for auto model)
    │   │       ├── output_token_count: int       ← Output tokens (0 for auto model)
    │   │       ├── cache_read_input_token_count: int  ← Cached tokens
    │   │       ├── cache_write_input_token_count: int ← Cache write tokens
    │   │       ├── model: string                 ← Model identifier ("auto")
    │   │       ├── context_usage_percentage: float    ← Context window %
    │   │       ├── final_context_usage_percentage: float ← Final context %
    │   │       ├── metering_usage: []            ← CREDITS (one per API call)
    │   │       │   └── {value: float, unit: "credit", unitPlural: "credits"}
    │   │       ├── assistant_response_length: int   ← Response character count
    │   │       ├── request_attempts: int         ← Total attempts incl. retries
    │   │       └── user_prompt_length: int       ← User message character count
    │   ├── last_context_usage: {percentage, model_id}
    │   ├── user_turn_start_request: null | object
    │   └── last_request: null | object
    ├── rts_model_state
    │   ├── conversation_id: UUID                 ← Same as session_id
    │   ├── model_info: {model_id, context_window_tokens}
    │   └── context_usage_percentage: float
    ├── permissions: {filesystem, trusted_tools, denied_tools, allowed_commands}
    ├── agent_name: string                        ← e.g. "kiro_default"
    └── goal: null | object
```

---

## 10. Summary of Findings by Category

### Token Counts

| Field | SQLite | Session JSON | Status |
|---|---|---|---|
| `input_token_count` | NOT FOUND | Present, value=0 (auto model) | PARTIALLY AVAILABLE |
| `output_token_count` | NOT FOUND | Present, value=0 (auto model) | PARTIALLY AVAILABLE |
| `cache_read_input_token_count` | NOT FOUND | Present, value=0 (auto model) | PARTIALLY AVAILABLE |
| `cache_write_input_token_count` | NOT FOUND | Present, value=0 (auto model) | PARTIALLY AVAILABLE |
| `uncachedInputTokens` | NOT FOUND | NOT FOUND | NOT FOUND |
| `totalTokens` | NOT FOUND | NOT FOUND | NOT FOUND |

The token count fields are present in the session JSON schema and will contain non-zero values for models that report token usage. For sessions using `model: "auto"`, the backend returns 0 for all counts. Whether a different model identifier (e.g., `anthropic.claude-sonnet-4-5`) produces non-zero counts is **UNVERIFIED** — no such session was available to inspect.

### Model Identity

| Field | SQLite | Session JSON | Status |
|---|---|---|---|
| Resolved model name | NOT FOUND | NOT FOUND (`"auto"` only) | PARTIALLY AVAILABLE |
| Model routing mode | NOT FOUND | `user_turn_metadatas[N].model` = `"auto"` | CONFIRMED AVAILABLE |
| Context window size | NOT FOUND | `rts_model_state.model_info.context_window_tokens` = 200000 | CONFIRMED AVAILABLE |

### Context Utilization

| Field | SQLite | Session JSON | Status |
|---|---|---|---|
| `context_usage_percentage` | NOT FOUND | Multiple locations, float | CONFIRMED AVAILABLE |
| Per-turn final value | NOT FOUND | `final_context_usage_percentage` | CONFIRMED AVAILABLE |

### Credit Consumption

| Field | SQLite | Session JSON | Status |
|---|---|---|---|
| Per-request credits | NOT FOUND | `metering_usage[].value` (float) | CONFIRMED AVAILABLE |
| Per-turn total credits | NOT FOUND | Sum of `metering_usage[]` | CONFIRMED AVAILABLE (computable) |
| `cost_usd` | NOT FOUND | NOT FOUND | NOT FOUND |

### Identifiers

| Field | SQLite | Session JSON | Status |
|---|---|---|---|
| `session_id` | NOT FOUND | Top-level `session_id` | CONFIRMED AVAILABLE |
| Turn ID | NOT FOUND | `result.Ok.id` (AI response message UUID) | PARTIALLY AVAILABLE |
| `request_id` | NOT FOUND | NOT FOUND | NOT FOUND |
| Message IDs | NOT FOUND | `message_ids: UUID[]` | CONFIRMED AVAILABLE |

---

## 11. Whether the SQLite Database is a Viable Source

**VERDICT: No. The `data.sqlite3` database is NOT a viable source for per-turn token, model, or credit telemetry.**

The database is empty of conversation data (`conversations_v2`, `conversations`, `history` all have 0 rows) and the populated tables (`state`, `auth_kv`) contain authentication and identity state only — no usage metrics.

The `conversations_v2` table was prepared with an appropriate schema for conversation state blobs (timestamped key-value records with a `value TEXT` column), but it was never written to in the observed sessions. Based on tui.js source analysis, the TUI process has no write path to this table. The Rust host binary may write to it under specific conditions not observed here (e.g., cloud session sync, explicit save commands).

---

## 12. The Actual Telemetry Source: Session JSON Files

The investigation identified the authoritative local telemetry store as:

**`~/.kiro/sessions/cli/{session_id}.json`**

These are the primary data files to target for `ai-telemetry-agent`.

### Access Properties

| Property | Value |
|---|---|
| Format | JSON, UTF-8, pretty-printed |
| Location | `%USERPROFILE%\.kiro\sessions\cli\` on Windows |
| Naming | `{session_id}.json` (UUID v4 format) |
| Lock status | Not byte-range locked (readable while Kiro CLI is running) |
| Write timing | Written/updated by the KAS subprocess at turn completion |
| Persistence | Files persist across Kiro CLI restarts |
| Historical data | All completed sessions remain in this directory |

### Confirmed Readable Session Files

| Session ID | Size | Turns | Key Finding |
|---|---|---|---|
| `66604d89-63c4-4ae5-ab74-6670a9e9e47b` | 13,413 bytes | 1 | 15 requests, 8.36 credits |
| `857dc9f9-5475-41f1-a9f4-3b8d1e335ab9` | 21,437 bytes | 1 | 53 requests, 12.81 credits |
| `c0c6f7c2-ed06-4df2-a56b-d394c4763261` | 1,106 bytes | 0 | Session created but no turns |
| `2aa1f3fe-9fce-429f-87b2-4cc5958e3a55` | 1,116 bytes | 0 | Active session (in-progress) |

### Viability for `ai-telemetry-agent`

| Capability | Feasibility | Notes |
|---|---|---|
| Ingest completed turn data | **Yes** | `user_turn_metadatas[]` grows by 1 per completed turn |
| Detect new turns | **Yes** | Compare `updated_at` timestamp or array length |
| Extract credit usage | **Yes** | `metering_usage[]` — one entry per API request |
| Extract context utilization | **Yes** | `context_usage_percentage` and `final_context_usage_percentage` |
| Extract model identifier | **Partially** | Only `"auto"` observed; resolved model not stored |
| Extract token counts | **Partially** | Fields present; `0` for `auto` model sessions |
| Correlate to session | **Yes** | `session_id` = filename UUID |
| Correlate to workspace | **Yes** | Top-level `cwd` field |
| Avoid duplicate ingestion | **Yes** | Use `{session_id}:{turn_index}` as cursor key |
| Read without locking source | **Yes** | Files are plain JSON, not locked |
| Privacy-safe | **Yes** | Field names, timestamps, and counts only — no prompts/responses needed |

---

## 13. Limitations and Unverified Findings

### What Could Not Be Verified

1. **Token counts for non-`auto` model sessions.** Both inspected sessions used `model: "auto"`, which returned 0 for all four token count fields. It is possible that sessions specifying an explicit model (e.g., `anthropic.claude-sonnet-4-5`) would return non-zero token counts. This would need a session file where a specific model was selected.

2. **`conversations_v2` content.** The table was empty in both database copies. The schema and indexing suggest it is designed for JSON conversation blobs keyed by `(key, conversation_id)`. Its contents during a session with active conversation persistence are unknown.

3. **Multi-turn session files.** Both substantive session files contained exactly one `user_turn_metadatas` entry. A session with multiple completed turns was not available; it is assumed (but not confirmed) that additional entries are appended to the array per turn.

4. **v3 session format.** Sessions under `~/.kiro/sessions/{workspace_hash}/sess_{id}/session.json` use a different schema (`schemaVersion: "v5"`, `dataModelVersion: 2`) from the v2 CLI sessions. These v3 sessions did not contain `user_turn_metadatas` in the observed examples and appear to be IDE-mode sessions. Their telemetry structure was not deeply inspected.

5. **Resolved model in metering_usage.** The `metering_usage` entries have no `model` field — only `{value, unit, unitPlural}`. If the resolved model name is needed per-request, it is not stored locally.

6. **`last_request` and `user_turn_start_request`.** Both fields were `null` in all observed sessions. If they are ever non-null, their structure and content are unknown.

---

## 14. Evidence Sources

| Source | Method | Key Finding |
|---|---|---|
| `data.sqlite3` (copy) | Python `sqlite3` read-only | 0 rows in conversations_v2, conversations, history; state has credentials only; no telemetry targets |
| `data.sqlite3` (copy2, refresh) | Same | Identical row counts — database is stable/empty of conversation data |
| `~/.kiro/sessions/cli/66604d89.json` | Python `json.load` | `metering_usage` (15 entries), `context_usage_percentage: 26.288101`, `model: "auto"`, token counts = 0 |
| `~/.kiro/sessions/cli/857dc9f9.json` | Python `json.load` | `metering_usage` (53 entries), `context_usage_percentage: 12.4312`, `model: "auto"`, token counts = 0 |
| `tui.js` (13.6 MB) | Python regex search | 0 occurrences of `conversations_v2`; token fields confirmed only in runtime `turn_summary` events; session files written by KAS subprocess |
| `~/.kiro/sessions/cli/2aa1f3fe.json` | `json.load` | Empty `user_turn_metadatas` — session created but no turns completed |
| `~/.kiro/sessions/d244b0fe/.index/index.json` | `json.load` | Index of v3 sessions (sess_27e57ac7, sess_b3b99b3a) |

---

## 15. Impact on `ai-telemetry-agent`

### What the SQLite Database Adds

Nothing new beyond what was already known. The database is not a viable telemetry source.

### What the Session JSON Files Add

This investigation identified the **correct telemetry source** that was missed by prior investigations:

| Gap (from prior report) | Filled by Session JSON | Classification |
|---|---|---|
| Credit consumption per turn | Yes — `metering_usage[]` with per-request values | **CONFIRMED AVAILABLE** |
| Context utilization | Yes — `context_usage_percentage` (float) | **CONFIRMED AVAILABLE** |
| Session-level identifier | Yes — `session_id` UUID (filename) | **CONFIRMED AVAILABLE** |
| Turn-level timing | Yes — `end_timestamp`, `turn_duration` | **CONFIRMED AVAILABLE** |
| Tool call count | Yes — `builtin_tool_uses` per turn | **CONFIRMED AVAILABLE** |
| API request count | Yes — `total_request_count` per turn | **CONFIRMED AVAILABLE** |
| Working directory | Yes — `cwd` field | **CONFIRMED AVAILABLE** |
| Token counts (exact) | Partial — present as fields, value=0 for `auto` model | **PARTIALLY AVAILABLE** |
| Resolved model name | Not found — only routing mode `"auto"` stored | **NOT FOUND** |
| Cost in USD | Not found | **NOT FOUND** |

### Recommended Ingestion Approach

The session JSON files are appropriate as an ingestion source for `ai-telemetry-agent` because:

- They are plain UTF-8 JSON files, not locked databases
- They persist across Kiro CLI restarts
- New turns append to the `user_turn_metadatas` array (cursor-based ingestion is feasible)
- The `{session_id}.json` filename provides a stable deduplication key
- The `updated_at` timestamp at the top level indicates when the file was last modified
- No prompt text, response text, or tool arguments are required

A cursor based on `{session_id}:{turn_index}` would allow zero-duplicate, append-only ingestion without reading files from scratch on each poll.

---

*This report was produced by a read-only automated investigation. No Kiro CLI settings, database records, or session files were modified.*
