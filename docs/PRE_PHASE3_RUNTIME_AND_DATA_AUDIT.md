# Pre-Phase 3 Technical Audit: Runtime & Antigravity Data Sources

## 1. Executive Summary

This audit evaluates the runtime portability and Antigravity data ingestion mechanics of the `ai-telemetry-collector` codebase prior to Phase 3 development. 

### Key Findings
1. **Zero Runtime Dependencies**: The collector is implemented purely using the Python standard library. It executes without third-party package dependencies across Python 3.10 through 3.14.
2. **No Internal File Scraping**: The collector **does not** read `.gemini/antigravity-ide/brain/.../transcript.jsonl`, internal SQLite databases, or private session caches.
3. **Legitimate Hook Ingestion**: AI lifecycle and tool events are received exclusively via official Antigravity Lifecycle Hooks (`.agents/hooks.json`) communicating structured JSON directly over standard input (`stdin`).
4. **Null Metric Preservation**: Token counts and USD costs are preserved as `null` since no local hook or MCP API emits them.
5. **Deployment Recommendation**: A standalone single-binary executable (`telemetry-agent.exe`) is recommended for enterprise deployment to eliminate Python runtime version mismatches on developer machines.

---

## 2. Current Python Version & Verification

The test suite and runtime environment were audited on the local host machine:

- **Installed Python Version**: `Python 3.14.4`
- **Build Identification**: `3.14.4 (tags/v3.14.4:23116f9, Apr 7 2026, 14:10:54) [MSC v.1944 64 bit (AMD64)]`
- **Test Suite Results**: `33 passed in 10.69s` (100% pass rate)

```powershell
PS C:\Users\Dell\Desktop\ai-telemetry-collector> python --version
Python 3.14.4
PS C:\Users\Dell\Desktop\ai-telemetry-collector> python -m pytest tests -v
============================= 33 passed in 10.69s =============================
```

---

## 3. Supported Python Version Assessment

| Python Version | Compatibility Status | Technical Notes |
| :--- | :--- | :--- |
| **Python < 3.10** | **Unsupported** | Lacks standard union type syntax and modern `dataclasses` features. |
| **Python 3.10** | **Supported** | Baseline supported version (`requires-python = ">=3.10"` in `pyproject.toml`). `is_valid_iso8601()` normalizes trailing `Z` for compatibility with 3.10's `datetime.fromisoformat()`. |
| **Python 3.11** | **Supported** | Fully compatible. Standard library enhancements (native `datetime.fromisoformat('Z')`, `tomllib`). |
| **Python 3.12** | **Supported** | Fully compatible. Standard library modules (`sqlite3`, `pathlib`, `subprocess`) adhere to stable APIs. |
| **Python 3.13** | **Supported** | Fully compatible. Zero deprecated standard library calls. |
| **Python 3.14** | **Verified & Tested** | Baseline development environment. All 33 test cases verified. |

---

## 4. Dependency Compatibility

| Dependency | Classification | Python Version Constraints | Notes |
| :--- | :--- | :--- | :--- |
| `sqlite3` | Python Standard Library | Built-in (3.10 - 3.14+) | Used for local indexed persistence and local event queue. Thread-safe with locking. |
| `dataclasses` | Python Standard Library | Built-in (3.10 - 3.14+) | Used for [TelemetryEvent](file:///c:/Users/Dell/Desktop/ai-telemetry-collector/src/core/event.py) and [AITelemetryPayload](file:///c:/Users/Dell/Desktop/ai-telemetry-collector/src/core/event.py). |
| `pathlib`, `os`, `sys` | Python Standard Library | Built-in (3.10 - 3.14+) | Cross-platform file path resolution. |
| `subprocess` | Python Standard Library | Built-in (3.10 - 3.14+) | Read-only Git command execution (`git rev-parse`, `git log`). |
| `threading`, `queue` | Python Standard Library | Built-in (3.10 - 3.14+) | Thread-safe locks and background polling loops. |
| `argparse`, `json`, `re` | Python Standard Library | Built-in (3.10 - 3.14+) | CLI argument parsing, serialization, and regex secret redaction. |
| `pytest` | Dev / Test Dependency | `>=8.0.0` | Used strictly for testing during development. Not required in production runtime. |
| `git` | External Executable | System path (`git.exe`) | Required only if Git telemetry is enabled. Handled gracefully if missing. |

---

## 5. Python Version Mismatch Scenarios

### Scenario 1: Collector developed on Python 3.14, Developer has Python 3.11
- **Outcome**: **Runs successfully**.
- **Reason**: The collector uses only standard library features that have been stable since Python 3.10. No 3.14-exclusive syntax is used.

### Scenario 2: Collector developed on Python 3.11, Developer has Python 3.14
- **Outcome**: **Runs successfully**.
- **Reason**: Standard library APIs utilized (`sqlite3`, `pathlib`, `dataclasses`, `subprocess`) maintain full forward compatibility in 3.14.

### Scenario 3: Developer has no Python installed
- **Outcome**: **Fails immediately**.
- **Error**: `Python was not found; run without arguments to install from the Microsoft Store...`
- **Mitigation**: Requires standalone binary deployment (`telemetry-agent.exe`).

### Scenario 4: Developer has multiple Python versions installed (or Windows App Execution Aliases)
- **Outcome**: **Potential invocation failure or wrong interpreter selection**.
- **Error**: Windows `python.exe` shim redirecting to Microsoft Store instead of actual Python path.
- **Mitigation**: Hook scripts must use explicit interpreter paths or standalone binary.

### Scenario 5: Developer has Python but no package installation permissions (`pip install` blocked)
- **Outcome**: **Runs successfully**.
- **Reason**: The collector has zero runtime pip dependencies (`requirements.txt` is empty).

---

## 6. Recommended Deployment Model

| Model | Setup Complexity | Version Safety | Update Mechanism | Dev Experience | Recommendation |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **A. Direct Python Script** (`python -m src.main`) | High | Low (Depends on dev Python) | Manual Git pull | Poor | Not recommended for non-Python devs |
| **B. Virtual Environment** (`.venv`) | Moderate | Medium | Scripted pip | Acceptable | Standard for development |
| **C. Standalone Binary** (`telemetry-agent.exe`) | **Zero** | **High (Bundled runtime)** | Self-update / Installer | **Seamless** | **RECOMMENDED FOR PRODUCTION** |
| **D. Background Windows Service** | High (Admin required) | High | MSI / Enterprise deployment | Transparent | Ideal for managed enterprise IT |

### Architectural Recommendation
Build and distribute a **Standalone Executable (`telemetry-agent.exe`)** (e.g. packaged via PyInstaller or Nuitka) that bundles an isolated Python runtime. Developers run a single self-contained background executable without configuring Python interpreters.

---

## 7. Antigravity Data Sources Audit

| Data Source | Location | Used by Collector? | Official API? | Purpose & Data Accessed |
| :--- | :--- | :--- | :--- | :--- |
| **Global Gemini Root** | `C:\Users\<user>\.gemini\` | **Probe check only** | Documented Path | `AntigravityCapabilityProbe.probe()` checks `is_dir()` to detect presence. |
| **Antigravity IDE Directory** | `C:\Users\<user>\.gemini\antigravity-ide\` | **Probe check only** | Documented Path | Checks `is_dir()` to verify IDE installation. |
| **MCP Config** | `~/.gemini/config/mcp_config.json` | **Probe check only** | Official Config | Checks `is_file()` to verify if MCP is configured. |
| **Hooks Config** | `.agents/hooks.json` | **Config Reference** | Official Contract | Declares hook commands executed by Antigravity. |
| **Hook Stdin Stream** | Process Pipe (`sys.stdin`) | **Active Ingestion** | Official Contract | Receives JSON lifecycle metadata (`conversationId`, `modelName`, `stepIdx`, `toolCall.name`). |
| **Session Transcripts** | `.gemini/antigravity-ide/brain/.../transcript.jsonl` | **NO (Ignored)** | Internal / Undocumented | Contains private source code, developer prompts, and secrets. **Never read.** |
| **Internal SQLite DBs** | `.gemini/antigravity-ide/.../*.db` | **NO (Ignored)** | Internal / Undocumented | Internal runtime state. **Never read.** |

---

## 8. Internal JSON / JSONL Investigation

**Finding**: **The collector does NOT consume Antigravity internal transcript JSON.**

### Forensic Verification
1. **Search Results**: Codebase search across all `.py` files confirms zero `open()` or `read()` calls targeting `transcript.jsonl` or `.gemini/antigravity-ide/brain/`.
2. **Schema of Internal Transcripts (Inspected for Audit Purpose Only)**:
   - `conversationId`: string
   - `step_index`: integer
   - `type`: string (e.g. `USER_INPUT`, `PLANNER_RESPONSE`, `TOOL_CALL`)
   - `content`: string (**SENSITIVE**: Contains raw prompt text, private source code snippets, `.env` values)
   - `tool_calls`: array of objects (**SENSITIVE**: Contains full command lines, file write contents, directory paths)
3. **Why It Is Tempting**: It contains the complete conversation history.
4. **Why We Do NOT Depend On It**:
   - Scraping internal transcripts violates developer privacy boundaries.
   - It risks ingesting proprietary intellectual property, passwords, and API keys.
   - The format is internal and subject to change without notice.

---

## 9. Hook JSON Investigation

The collector receives data strictly from Antigravity via standard input (`stdin`) when Antigravity executes the hooks registered in `.agents/hooks.json`.

```
Antigravity IDE
      │
      │ 1. Executes command configured in .agents/hooks.json
      │ 2. Pipes JSON payload into stdin
      ▼
src/integrations/hooks/hook_handler.py
      │
      │ 3. Parses JSON from stdin
      │ 4. Extracts metadata (drops raw args)
      │ 5. Writes { "decision": "allow" } to stdout
      ▼
Standard Telemetry Pipeline (EventProcessor -> Queue -> SQLite)
```

### Stdin Schemas Received from Antigravity

#### 1. `PreInvocation` & `PostInvocation`
```json
{
  "conversationId": "string (UUID)",
  "workspacePaths": ["string (path)"],
  "transcriptPath": "string (path)",
  "artifactDirectoryPath": "string (path)",
  "modelName": "string (e.g. 'gemini-3.7-flash')",
  "invocationNum": 1,
  "initialNumSteps": 10
}
```

#### 2. `PreToolUse` & `PostToolUse`
```json
{
  "conversationId": "string (UUID)",
  "stepIdx": 5,
  "modelName": "string",
  "toolCall": {
    "name": "string (e.g. 'run_command')",
    "args": { "CommandLine": "string" }
  },
  "error": "string (optional, present in PostToolUse on failure)"
}
```

#### 3. `Stop`
```json
{
  "conversationId": "string (UUID)",
  "modelName": "string",
  "executionNum": 1,
  "terminationReason": "string (e.g. 'model_stop', 'error')",
  "error": "string",
  "fullyIdle": true
}
```

---

## 10. Exact Data Flow

```
+-------------------------------------------------------------------------+
|                           Developer Machine                             |
|                                                                         |
|  +---------------------+                                                |
|  |   Antigravity IDE   |                                                |
|  +----------+----------+                                                |
|             |                                                           |
|             | (1) Invocations via .agents/hooks.json                    |
|             |     Pipes JSON payload to stdin                           |
|             v                                                           |
|  +-----------------------------------+                                  |
|  | src/integrations/hooks/           |                                  |
|  | hook_handler.py                   |                                  |
|  +------------------+----------------+                                  |
|                     |                                                   |
|                     | (2) Parses metadata, extracts:                    |
|                     |     - conversationId -> session_id                |
|                     |     - modelName      -> model                     |
|                     |     - toolCall.name  -> tool_name                 |
|                     |     - stepIdx        -> metadata.step_idx         |
|                     |     (Explicitly drops toolCall.args)              |
|                     v                                                   |
|  +-----------------------------------+     +-------------------------+  |
|  | src/sources/ai_provider.py        |     | src/sources/git.py      |  |
|  | AIEventBuilder                    |     | GitSource (Local Repo)  |  |
|  +------------------+----------------+     +------------+------------+  |
|                     |                                   |               |
|                     +-----------------+-----------------+               |
|                                       |                                 |
|                                       | (3) Raw Telemetry Events        |
|                                       v                                 |
|                    +-------------------------------------+              |
|                    | src/core/processor.py               |              |
|                    | EventProcessor                      |              |
|                    | - Attach local MEMBER_ID, DEVICE_ID |              |
|                    | - Attach ISO-8601 UTC timestamp     |              |
|                    | - Generate UUID event_id            |              |
|                    | - Sanitize metadata (regex scrub)   |              |
|                    | - Schema validation                 |              |
|                    +------------------+------------------+              |
|                                       |                                 |
|                                       | (4) Normalized TelemetryEvent   |
|                                       v                                 |
|                    +-------------------------------------+              |
|                    | src/storage/queue.py                |              |
|                    | SQLiteEventQueue                    |              |
|                    +------------------+------------------+              |
|                                       |                                 |
|                                       | (5) Dequeue & Persist           |
|                                       v                                 |
|                    +-------------------------------------+              |
|                    | src/storage/sqlite_store.py         |              |
|                    | SQLiteEventStore (Indexed DB)       |              |
|                    +-------------------------------------+              |
+-------------------------------------------------------------------------+
```

---

## 11. Telemetry Field Origin Matrix

| Telemetry Field | Source | Direct or Derived | Currently Available? | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `member_id` | [LocalIdentityProvider](file:///c:/Users/Dell/Desktop/ai-telemetry-collector/src/core/identity.py#L20-L40) | Direct | **Yes** | Configured in `.env` (decoupled from shared IDE). |
| `device_id` | [LocalIdentityProvider](file:///c:/Users/Dell/Desktop/ai-telemetry-collector/src/core/identity.py#L20-L40) | Direct | **Yes** | Configured in `.env`. |
| `session_id` | Hook `conversationId` | Direct | **Yes** | Extracted from hook stdin payload. |
| `run_id` | Auto-generated UUID | Derived | **Yes** | Generated per invocation turn. |
| `provider` | Hardcoded `"antigravity"` | Direct | **Yes** | Sourced from adapter identity. |
| `model` | Hook `modelName` | Direct | **Yes** | E.g. `gemini-3.7-flash` from hook payload. |
| `tool_name` | Hook `toolCall.name` | Direct | **Yes** | E.g. `run_command`, `view_file`. |
| `duration_ms` | System clock delta | Derived | **Yes** | Calculated between `PreInvocation` and `Stop`. |
| `input_tokens` | **None** | — | **No (`null`)** | Not emitted by local hooks/MCP. |
| `output_tokens` | **None** | — | **No (`null`)** | Not emitted by local hooks/MCP. |
| `cache_tokens` | **None** | — | **No (`null`)** | Not emitted by local hooks/MCP. |
| `total_tokens` | **None** | — | **No (`null`)** | Not emitted by local hooks/MCP. |
| `cost_usd` | **None** | — | **No (`null`)** | Not emitted by local hooks/MCP. |
| `repository` | [GitSource](file:///c:/Users/Dell/Desktop/ai-telemetry-collector/src/sources/git.py) | Direct | **Yes** | Extracted via `git rev-parse --show-toplevel`. |
| `branch` | [GitSource](file:///c:/Users/Dell/Desktop/ai-telemetry-collector/src/sources/git.py) | Direct | **Yes** | Extracted via `git rev-parse --abbrev-ref HEAD`. |
| `commit_sha` | [GitSource](file:///c:/Users/Dell/Desktop/ai-telemetry-collector/src/sources/git.py) | Direct | **Yes** | Extracted via `git log -1`. |

---

## 12. Privacy & Security Findings

### Protections Implemented
1. **Tool Argument Dropping**: [hook_handler.py](file:///c:/Users/Dell/Desktop/ai-telemetry-collector/src/integrations/hooks/hook_handler.py) only extracts `toolCall.name`. The `toolCall.args` dictionary is intentionally **discarded** to prevent storing command-line secrets or file edit bodies.
2. **Regex-Based Data Sanitization**: [sanitize_data()](file:///c:/Users/Dell/Desktop/ai-telemetry-collector/src/logging/structured.py#L32-L48) scrubs sensitive keys (`password`, `secret`, `token`, `api_key`, `auth`, `bearer`) and known token value patterns (`sk-...`, `ghp_...`, `AKIA...`).
3. **No Working Tree Diffing**: Git telemetry tracks only commit metadata (author, branch, commit hash, sanitized subject), never file diffs or working tree contents.

---

## 13. Identified Risks & Boundary Limitations

1. **Regex Sanitization Limitations**: While regex scrubbing catches known key names and token structures, it cannot guarantee detection of unstructured arbitrary secrets (e.g. passwords formatted as plain dictionary words embedded in error messages). Dropping raw content entirely remains the primary safeguard.
2. **Hook Execution Latency**: Hooks run synchronously in the Antigravity agent loop. The hook handler must return immediately (`< 50ms`) to avoid introducing latency into developer IDE interactions.
3. **Absence of Token Ingestion**: Local hooks cannot measure prompt or completion tokens. Tracking organization-wide token consumption requires upstream AI Gateway integration.

---

## 14. Recommended Architecture for Phase 3

```
+--------------------------------------------------------------------------+
|                            Developer Laptop                              |
|                                                                          |
|  +--------------------+         +-------------------+                    |
|  |  Antigravity IDE   |         |  Git Workspace    |                    |
|  +---------+----------+         +---------+---------+                    |
|            |                              |                              |
|            | (Hooks: lifecycle/tools)     | (Git metadata)               |
|            v                              v                              |
|  +--------------------------------------------------+                    |
|  |     Local Telemetry Collector (telemetry-agent)  |                    |
|  |                                                  |                    |
|  |  - Member/Device Identity Attachment             |                    |
|  |  - Metadata Normalization & Secret Redaction     |                    |
|  |  - SQLite Buffer Queue                           |                    |
|  +------------------------+-------------------------+                    |
|                           |                                              |
+---------------------------|----------------------------------------------+
                            |
                            | (HTTP Batch Ingestion - Phase 3)
                            v
+--------------------------------------------------------------------------+
|                       Central Platform Backend                           |
|                                                                          |
|  +--------------------------------------------------+                    |
|  | FastAPI Ingestion Service                        |                    |
|  +------------------------+-------------------------+                    |
|                           |                                              |
|                           v                                              |
|  +--------------------------------------------------+                    |
|  | PostgreSQL Telemetry Data Warehouse              |                    |
|  | - Enriched with Token & Cost metrics from        |                    |
|  |   Upstream AI Gateway Proxy                      |                    |
|  +--------------------------------------------------+                    |
+--------------------------------------------------------------------------+
```

---

## 15. Answers to the Five Critical Audit Questions

### Question 1: Can the telemetry collector run reliably on different Python versions?
**YES WITH CONSTRAINTS**.
- **Supported Versions**: Python 3.10 through 3.14+ without code changes.
- **Constraints**: Uses exclusively Python Standard Library (`sqlite3`, `dataclasses`, `pathlib`, `subprocess`). However, if a developer machine has no Python installed or lacks executable path configuration, direct execution will fail. Single-binary packaging (`telemetry-agent.exe`) eliminates this constraint.

### Question 2: Does our collector currently read Antigravity's internal `.gemini` transcript JSON?
**NO**.
The collector does not read, tail, or parse `.gemini/antigravity-ide/brain/.../transcript.jsonl` or any internal session files.

### Question 3: Where does the current AI telemetry actually come from?
```
Antigravity IDE Runtime
       ↓
Lifecycle Hook (.agents/hooks.json)
       ↓
Piped stdin JSON (metadata only)
       ↓
hook_handler.py (extracts session_id, model, tool_name)
       ↓
EventProcessor (attaches member_id, device_id, scrubs)
       ↓
SQLite Queue & Indexed Store
```

### Question 4: Are we depending on undocumented/private Antigravity files?
**NO**.
The implementation relies strictly on documented, supported Lifecycle Hooks (`hooks.json`) and standard path presence checks.

### Question 5: What should the final developer installation look like?
**Standalone Executable (`telemetry-agent.exe`)**.
- Self-contained binary bundling the runtime.
- Zero manual Python, pip, or virtualenv configuration for developers.
- Single command execution with optional background Windows Service registration.

---

## 16. Recommendations Before Starting Phase 3

1. **Keep Local Pipeline Pure**: Maintain the standard library foundation for the local collector agent to ensure minimal footprint and high execution speed.
2. **Implement Phase 3 HTTP Exporter on Top of SQLite Queue**: The existing [SQLiteEventQueue](file:///c:/Users/Dell/Desktop/ai-telemetry-collector/src/storage/queue.py) is already architected with `enqueue`, `dequeue`, `retry`, and `mark_processed`. Wire the Phase 3 HTTP background sender directly to this queue interface.
3. **Session ID Joining**: In Phase 3, use the `session_id` (`conversationId`) to correlate local developer/Git events with upstream token counts from the central AI proxy.
