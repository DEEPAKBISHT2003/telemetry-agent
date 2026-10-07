# Model Context Protocol (MCP) Telemetry Probe

## Objective
Determine whether configuring an MCP server in Antigravity can allow the telemetry collector to capture AI execution events (prompts, tokens, costs, model choices, tool calls).

---

## Technical Architecture & Findings

### Direction of Communication
- **Antigravity** acts as the **MCP Client**.
- **The Telemetry Collector probe** acts as the **MCP Server**.

```
+------------------+                    +---------------------------+
| Antigravity IDE  | --- tools/list --> | ai-telemetry-mcp-probe    |
|   (MCP Client)   | <-- tool defs ---  |      (MCP Server)         |
|                  |                    |                           |
|                  | --- tools/call --> | (Executes specific tool)  |
|                  | <-- result ------- |                           |
+------------------+                    +---------------------------+
```

### Can MCP observe AI execution events?
**NO.**

1. **No LLM Metrics**: An MCP server is purely an external tool execution provider. It is never informed of:
   - Prompt input tokens
   - Completion output tokens
   - Cache tokens
   - Model cost
   - LLM model version
   - Reasoning turns
2. **No General Tool Observability**: An MCP server is only invoked when the LLM specifically selects *its own registered tool* (`telemetry_health_check`). It cannot observe when the agent calls built-in tools like `run_command`, `view_file`, or tools from other MCP servers.
3. **Conclusion**: MCP is **`MCP_SUPPORTED_BUT_INSUFFICIENT`** for organization-wide AI telemetry tracking.

---

## Registration in `mcp_config.json` (Optional Verification)

To test registration with Antigravity:

```json
{
  "mcpServers": {
    "telemetry-probe": {
      "command": "python",
      "args": ["-m", "ai_telemetry_agent.integrations.mcp_probe.mcp_server"]
    }
  }
}
```
