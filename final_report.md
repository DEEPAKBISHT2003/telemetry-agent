# Final Targeted Experiment: Kiro CLI Explicit Model Token Telemetry

**Investigation Date:** 2026-10-14  
**Experiment Time:** 01:35:29 UTC+5:30  
**Kiro CLI Version:** 2.31.0  
**Objective:** Determine whether explicit model selection yields non-zero token usage telemetry (vs. `auto` baseline where token fields were 0)

---

## 1. Environment and Model Selection

**Kiro CLI Version:** `kiro-cli-chat 2.31.0`

**Available Models (from `--list-models --format json`):**
- `auto` (default) — Models chosen by task for optimal usage and consistent quality
- `claude-sonnet-4.5` — Claude Sonnet 4.5 model
- `claude-sonnet-4` — [EOL] Hybrid reasoning and coding for regular use
- `claude-haiku-4.5` — The latest Claude Haiku model
- `deepseek-3.2` — Experimental preview of DeepSeek V3.2
- `minimax-m2.5` — MiniMax M2.5 model
- `minimax-m2.1` — Experimental preview of MiniMax M2.1
- `glm-5` — GLM-5 model
- `qwen3-coder-next` — Experimental preview of Qwen3 Coder Next

**Model Selection Method:** CLI option `--model <MODEL_ID>` (confirmed via `--help` output)

**Chosen for Experiment:** `claude-haiku-4.5` (lower credit rate at 0.4× multiplier, available to account)

---

## 2. Baseline and Session Identification

**Baseline Time:** 2026-10-14T01:34:30.288+05:30

**Existing Session Files (before experiment):**
- `2aa1f3fe-9fce-429f-87b2-4cc5958e3a55.json` (18,512 bytes, modified 01:31:12)
- `66604d89-63c4-4ae5-ab74-6670a9e9e47b.json` (13,413 bytes, modified 00:33:36)
- `857dc9f9-5475-41f1-a9f4-3b8d1e335ab9.json` (21,437 bytes, modified 01:02:38)
- `c0c6f7c2-ed06-4df2-a56b-d394c4763261.json` (1,106 bytes, modified 01:05:27)

**Experimental Command:**
```bash
cd "C:\Users\Dell\Desktop\telemetry-agent"
kiro-cli chat --model claude-haiku-4.5 "Explain what a Python decorator is in one sentence."
```

**New Session Created:** `a4840656-c021-462d-8939-76d196b05327.json` (3,216 bytes, modified 01:35:29)

---

## 3. Experimental Session Telemetry Results

### Session Metadata
- **session_id:** `a4840656-c021-462d-8939-76d196b05327`
- **cwd:** `C:\Users\Dell\Desktop\telemetry-agent`
- **created_at:** `2026-10-13T20:05:26.199472400Z`
- **updated_at:** `2026-10-13T20:05:29.422422100Z`
- **title:** `Explain what a Python decorator is in one sentence.`
- **session_created_reason:** `subagent`

### Model Selection Verification
✅ **Explicit model selection was accepted and persisted:**
- `rts_model_state.model_info.model_id`: `claude-haiku-4.5` (not `auto`)
- `rts_model_state.model_info.model_name`: `claude-haiku-4.5`
- `user_turn_metadatas[0].model`: `claude-haiku-4.5`
- `model_info` also includes: `context_window_tokens: 200000`, `rate_multiplier: 0.4`, `rate_unit: "Credit"`

### Token Count Fields
❌ **All token count fields remained 0:**

| Field | Value | Previous `auto` | Difference |
|---|---|---|---|
| `input_token_count` | `0` | `0` | Unchanged |
| `output_token_count` | `0` | `0` | Unchanged |
| `cache_read_input_token_count` | `0` | `0` | Unchanged |
| `cache_write_input_token_count` | `0` | `0` | Unchanged |

**Interpretation:** Token counting appears to be backend-dependent, not CLI-side. The presence of credit metering suggests token usage is tracked server-side but not returned to the client in integer token counts.

### Credit Metering
✅ **Credit usage was recorded:**

- `metering_usage`: 1 entry (matches `total_request_count: 1`)
- Entry: `{"value": 0.020512219767827536, "unit": "credit", "unitPlural": "credits"}`
- **Total credits:** `0.020512219767827536` credits
- **Credit multiplier effect:** Haiku uses 0.4× multiplier (vs. 1.0× for auto, 1.3× for Sonnet)

**Comparison to `auto` sessions:**
- `66604d89` (auto): 15 requests, ~8.36 total credits
- `857dc9f9` (auto): 53 requests, ~12.81 total credits
- Experimental (haiku): 1 request, 0.0205 credits (single, low-cost request)

### Context Utilization
✅ **Context usage telemetry works:**

- `context_usage_percentage`: `7.6435`
- `final_context_usage_percentage`: `7.6435`
- `rts_model_state.context_usage_percentage`: `7.6435`
- `last_context_usage`: `{"percentage": 7.6435, "model_id": "claude-haiku-4.5"}`

**Comparison to `auto` sessions:**
- `66604d89`: 26.288101% (larger session with tools)
- `857dc9f9`: 12.4312% / 16.2404% (turn vs. session-level discrepancy)
- Experimental: 7.6435% (simple one-sentence prompt)

### Request Metadata
✅ **Turn metadata recorded:**

- `total_request_count`: `1`
- `number_of_cycles`: `0`
- `builtin_tool_uses`: `0`
- `turn_duration`: `{"secs": 2, "nanos": 106769900}` (2.1 seconds)
- `end_reason`: `UserTurnEnd`
- `end_timestamp`: `2026-10-13T20:05:29.419600900Z`
- `assistant_response_length`: `149` characters
- `user_prompt_length`: `51` characters
- `request_attempts`: `1`

### Missing/Not Found
- **`cost_usd`**: NOT FOUND (only credits, no monetary conversion)
- **Resolved backend model**: NOT DISTINCT (only requested model stored)
- **Turn ID field**: NOT FOUND (use `result.Ok.id` = message UUID)

---

## 4. Comparison Table: `auto` vs. Explicit Model

| Metric | Previous `auto` Sessions | Experimental (claude-haiku-4.5) | Interpretation |
|---|---|---|---|
| **Configured model** | `auto` | `claude-haiku-4.5` | ✅ Selection effective |
| **Resolved backend model** | Unknown | Unknown (only requested model stored) | ❌ Not exposed locally |
| **Input tokens** | `0` | `0` | ❌ No token counts available |
| **Output tokens** | `0` | `0` | ❌ No token counts available |
| **Cache-read tokens** | `0` | `0` | ❌ No token counts available |
| **Cache-write tokens** | `0` | `0` | ❌ No token counts available |
| **Request count** | Populated (15, 53) | Populated (1) | ✅ Request metadata works |
| **Metering credits** | Populated (~8.36, ~12.81) | Populated (0.0205) | ✅ Credit metering works |
| **Context utilization** | Populated (26.29%, 12.43%) | Populated (7.64%) | ✅ Context telemetry works |
| **USD cost** | Not found | Not found | ❌ No monetary conversion |

---

## 5. Validation of Results

**Was explicit model selection accepted?** ✅ Yes — `model_id` and `model` fields show `claude-haiku-4.5`.

**Did the selected model complete a response?** ✅ Yes — `end_reason: UserTurnEnd`, 2.1 second duration, 149-character response.

**Were any token-count fields non-zero?** ❌ No — All four token fields remained `0`.

**Could the test be affected by incomplete session?** ❌ No — Session completed normally (`UserTurnEnd`), file written.

**Are observed values attributable to this session?** ✅ Yes — Session created after baseline, single turn, no tools.

**Does this establish general capability?** ⚠️ Partially — Token fields may be 0 for all models (backend decision), not just `auto`.

---

## 6. Final Conclusions

### Token Counts
**CONCLUSION:** Kiro CLI does **NOT** expose non-zero token usage telemetry locally, even with explicit model selection. All four token count fields (`input_token_count`, `output_token_count`, `cache_read_input_token_count`, `cache_write_input_token_count`) remained `0` for `claude-haiku-4.5`, just as they were for `auto`.

**Interpretation:** Token counting appears to be a server-side calculation not returned to the client. Credit-based metering is the primary consumption metric.

### Model Identity
**CONCLUSION:** Explicit model selection is **correctly persisted** in session files. The requested model identifier (`claude-haiku-4.5`) appears in both `rts_model_state.model_info` and per-turn `model` fields.

**Limitation:** The actual resolved backend model (if different from requested) is not exposed.

### Cost Tracking
**CONCLUSION:** No direct USD cost conversion is available locally. Only **credits** are tracked, with model-specific multipliers (0.4× for Haiku, 1.3× for Sonnet, etc.).

### Credit Metering and Context Utilization
**CONCLUSION:** Both work correctly for explicit models. Per-request credit values and context window usage percentages are recorded with full precision (6+ decimal places for credits).

---

## 7. Limitations and Uncertainty

1. **Single model tested:** Only `claude-haiku-4.5` was tested. Other models (`deepseek-3.2`, `minimax-m2.5`, etc.) could behave differently, but unlikely given architecture.
2. **Simple prompt:** Single-turn, no-tool usage may affect token counting (if implemented).
3. **Backend dependency:** The decision to not return token counts may be Kiro server policy, not CLI limitation.
4. **Session file version:** All sessions were `version: "v1"` schema. Future schema changes could add token fields.

---

## 8. Recommendation for `ai-telemetry-agent`

### ✅ Viable Telemetry Sources (from session JSON files):

| Field | Confidence | Notes |
|---|---|---|
| **Credit usage** (`metering_usage[]`) | High | Per-request values, precise floats |
| **Context utilization** (`context_usage_percentage`) | High | Float percentage, multiple snapshots |
| **Model selection** (`model_info`, `model`) | High | Requested model ID persisted |
| **Request counts** (`total_request_count`, `builtin_tool_uses`) | High | Tool usage and API call counts |
| **Turn timing** (`turn_duration`, `end_timestamp`) | High | ISO timestamps, duration struct |
| **Session metadata** (`session_id`, `cwd`, `created_at`) | High | Stable identifiers, workspace correlation |

### ⚠️ Limited/Unreliable Fields:

| Field | Confidence | Notes |
|---|---|---|
| **Token counts** (all four fields) | Low | Always `0` in observed sessions |
| **USD cost** | None | Not present in schema |
| **Resolved backend model** | Low | Only requested model stored |

### 📋 Ingestion Strategy:

1. **Source:** `~/.kiro/sessions/cli/{session_id}.json`
2. **Cursor:** `{session_id}:{turn_index}` (increment as `user_turn_metadatas` grows)
3. **Timing:** Poll based on `updated_at` timestamp
4. **Privacy:** Safe — no prompts, responses, or tool arguments needed
5. **Granularity:** Per-turn, per-request credit values available

**Architecture confirmed:** Session JSON files are the authoritative telemetry store for Kiro CLI v2.31.0, not the SQLite database.

---

## 9. Evidence Chain

1. **Baseline documented:** 4 session files before experiment (timestamps recorded)
2. **Command executed:** `kiro-cli chat --model claude-haiku-4.5 "Explain what a Python decorator is in one sentence."`
3. **New session created:** `a4840656-c021-462d-8939-76d196b05327.json` (after baseline)
4. **Data extracted:** Token fields = 0, credits = 0.020512, context = 7.6435%
5. **Comparison performed:** Identical token field behavior between `auto` and explicit model

**Experiment Status:** **SUCCESSFUL** — Clear evidence gathered, methodology validated.
