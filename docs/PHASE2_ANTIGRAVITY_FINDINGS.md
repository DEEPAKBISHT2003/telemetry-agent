# Phase 2 Investigation: Antigravity AI Telemetry Capture

## Executive Finding

Our forensic investigation of the Antigravity developer environment confirms that **Lifecycle Hooks (`hooks.json`)** are the only verified, non-intrusive official mechanism for capturing developer session state, model selection, and tool invocations locally without scraping private runtime files.

However, neither Hooks, MCP, nor any local public API expose raw LLM **token counts (input, output, cache)** or **cost (USD)**. MCP provides tool invocation routing to custom servers but cannot observe the agent's internal model execution or token metrics. Therefore, while local lifecycle and tool telemetry is **`HOOK_SUPPORTED`**, capturing exact token counts and cost requires an upstream enterprise **AI Gateway / Proxy (`PROXY_REQUIRED`)** or a native IDE telemetry exporter in future versions.

---

## What Antigravity exposes officially

1. **Lifecycle Hooks (`.agents/hooks.json`)**:
   - `PreInvocation`: Triggered before the model is called. Provides `conversationId`, `modelName`, `stepIdx`, `workspacePaths`.
   - `PostInvocation`: Triggered after tool steps finish. Provides `conversationId`, `modelName`.
   - `PreToolUse` / `PostToolUse`: Triggered before/after a tool executes. Provides `toolCall.name`, `toolCall.args`, `stepIdx`, `error`.
   - `Stop`: Triggered on conversation loop termination. Provides `executionNum`, `terminationReason`, `error`.

2. **Model Context Protocol (MCP)** (`~/.gemini/config/mcp_config.json`):
   - Exposes registered tools to the LLM via stdio / SSE transport.
   - Invokes the MCP server strictly when the model calls tools belonging to that specific server.

3. **Customization Configuration**:
   - Directory and project rules (`GEMINI.md`, `AGENTS.md`, `.agents/rules/*.md`).
   - Progressive disclosure skills (`skills/<name>/SKILL.md`).
   - Plugin packaging (`plugins/<name>/plugin.json`).

---

## What we can capture today

With our non-invasive, privacy-preserving collector architecture:
- **Developer & Device Identity**: Human developer (`member_id`) and machine (`device_id`) bound locally.
- **Git Context**: Active repository, branch changes, and commit SHAs without capturing code diffs.
- **AI Session Lifecycles**: Start, failure, and completion of agent runs via `PreInvocation` and `Stop` hooks.
- **Model Selected**: Active model identifier (e.g. `gemini-3.7-flash`) passed in hook payloads.
- **Tool Invocations**: Standard and custom tool calls (`tool_name`, execution duration, step index) via `PreToolUse`/`PostToolUse`.
- **MCP Tool Usage**: Invocations of custom organizational MCP tools.

---

## What we cannot capture today

- **Prompt Text & Model Responses**: Excluded intentionally to guarantee privacy and security.
- **Input Tokens & Output Tokens**: Not emitted by local Hooks or MCP APIs.
- **Cache Read / Cache Write Tokens**: Not emitted by local Hooks or MCP APIs.
- **Cost (USD)**: Not emitted locally.
- **Per-Token Generation Latency**: Internal LLM inference timings are not streamed to local clients.

*Strict Rule: All unexposed fields remain `NULL`. They are never estimated or inferred from string lengths.*

---

## MCP feasibility

- **Classification**: `MCP_SUPPORTED_BUT_INSUFFICIENT`
- **Analysis**:
  - Antigravity functions as an MCP *client*. An MCP server functions solely as an external tool provider.
  - The MCP server is unaware of general LLM activity, prompt lengths, model token metrics, or other tool calls (e.g. `run_command`, `view_file`).
  - MCP is valuable for custom organizational tools, but is technically insufficient as an AI usage telemetry backbone.

---

## Extension feasibility

- **Classification**: `NOT_CURRENTLY_AVAILABLE`
- **Analysis**:
  - Antigravity does not currently provide an open, public IDE extension API for streaming internal LLM request/response telemetry.
  - Plugins in Antigravity are bundles of skills, rules, and hooks, rather than executable background telemetry daemons.

---

## Hook/plugin feasibility

- **Classification**: `HOOK_SUPPORTED`
- **Analysis**:
  - The `hooks.json` lifecycle hook system is stable, official, and provides synchronous JSON contracts over standard input/output (`stdin`/`stdout`).
  - Hooks enable capturing agent run starts, tool executions, errors, and completions with zero risk of breaking IDE stability.

---

## Token/cost availability

- **Direct Local API**: Unavailable.
- **Fabrication Policy**: The collector strictly enforces `null` values for `input_tokens`, `output_tokens`, `cache_tokens`, and `cost_usd`.
- **Resolution Strategy**: Accurate token and cost tracking requires an enterprise AI Gateway / Proxy intercepting requests between the client and model provider API, or an official IDE telemetry export mechanism.

---

## Privacy risks

### Unsafe Approaches (Rejected)
- Scraping `.gemini/antigravity-ide/brain/.../transcript.jsonl` contains raw source code, developer prompts, `.env` file contents, and API keys.
- Scraping SQLite or UI memory buffers is brittle and violates developer data boundaries.

### Safe Approach (Implemented)
- Strict metadata-only allowlist via [sanitize_data()](file:///c:/Users/Dell/Desktop/ai-telemetry-collector/src/logging/structured.py#L32-L48).
- Automated redaction of regex patterns for passwords, tokens, and API keys.
- No source code or prompt bodies are ever persisted.

---

## Recommended integration

Implement a hybrid integration strategy:
1. **Local Agent Lifecycle & Tool Telemetry**: Use `.agents/hooks.json` invoking [src/integrations/hooks/hook_handler.py](file:///c:/Users/Dell/Desktop/ai-telemetry-collector/src/integrations/hooks/hook_handler.py) to capture session IDs, model choices, tool calls, and run durations.
2. **Local Machine & Git Telemetry**: Use the existing [GitSource](file:///c:/Users/Dell/Desktop/ai-telemetry-collector/src/sources/git.py) and [SystemSource](file:///c:/Users/Dell/Desktop/ai-telemetry-collector/src/sources/system.py).
3. **Future Token & Cost Telemetry**: Channel enterprise model requests through an organization AI Proxy Gateway that emits exact token and cost usage records tagged with `session_id`.

---

## Architecture decision

### Primary Classification
**`HOOK_SUPPORTED`** (for local lifecycle and tool telemetry) combined with **`PROXY_REQUIRED`** (for token counts and USD costs).

---

## Next implementation phase (Phase 3 Preview)

1. **Remote Ingestion Worker**: Wire [SQLiteEventQueue](file:///c:/Users/Dell/Desktop/ai-telemetry-collector/src/storage/queue.py) to an HTTP client pushing event batches to a central ingestion service.
2. **AI Proxy / Gateway Integration**: Connect model provider API responses to enrich session events with exact token counts and costs.
3. **SSO Developer Authentication**: Replace local `.env` identity with organizational SSO tokens.
