# Local Telemetry Collector V0 Specification & Architecture

## 1. Architecture

The Telemetry Collector is an organization-level, provider-independent agent that runs locally on developer laptops. It aggregates local developer engineering activity and AI telemetry into a single standardized event model without transmitting secrets or raw source code.

```
+-------------------------------------------------------------+
|                      Developer Laptop                       |
|                                                             |
|  +------------------+  +-----------------+  +------------+  |
|  |   SystemSource   |  |    GitSource    |  |Antigravity |  |
|  |  (Lifecycle/HB)  |  | (Branch/Commit) |  |  (Adapter) |  |
|  +--------+---------+  +--------+--------+  +-----+------+  |
|           |                     |                 |         |
|           +------------------+  |  +--------------+         |
|                              |  |  |                        |
|                              v  v  v                        |
|                     +--------------------+                  |
|                     |   EventProcessor   |                  |
|                     | (Validate & Scrub) |                  |
|                     +---------+----------+                  |
|                               |                             |
|                               v                             |
|                     +--------------------+                  |
|                     |    Local Queue     |                  |
|                     |   (SQLite Buff)    |                  |
|                     +---------+----------+                  |
|                               |                             |
|                               v                             |
|                     +--------------------+                  |
|                     |   SQLite Storage   |                  |
|                     |    (Indexed DB)    |                  |
|                     +--------------------+                  |
+-------------------------------------------------------------+
```

---

## 2. Project Structure

```
telemetry-collector/
├── src/
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py          # Configuration parser (.env and env vars)
│   ├── core/
│   │   ├── __init__.py
│   │   ├── collector.py         # Main TelemetryCollector lifecycle engine
│   │   ├── event.py             # Standard event envelope and AI payload dataclasses
│   │   ├── identity.py          # Member and Device identity provider
│   │   ├── processor.py         # Normalization, validation, sanitization pipeline
│   │   └── validation.py        # Schema validator and timestamp validation
│   ├── logging/
│   │   ├── __init__.py
│   │   └── structured.py        # Structured log emitter with secret scrubber
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── base.py              # EventSource abstract base interface
│   │   ├── system.py            # Lifecycle and heartbeat event source
│   │   ├── git.py               # Read-only Git monitor
│   │   └── antigravity.py       # Antigravity AI telemetry adapter
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── base.py              # EventStore abstract base interface
│   │   ├── queue.py             # Local SQLite event queue
│   │   └── sqlite_store.py      # SQLite persistent event store
│   ├── __init__.py
│   └── main.py                  # CLI entrypoint (start, status, events, stop)
├── tests/
│   ├── test_identity.py
│   ├── test_event_model.py
│   ├── test_storage.py
│   ├── test_queue.py
│   ├── test_git_source.py
│   ├── test_collector_lifecycle.py
│   ├── test_privacy.py
│   └── test_e2e.py
├── docs/
│   └── TELEMETRY_COLLECTOR_V0.md
├── .env.example
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

---

## 3. Event Schema

All events adhere to a standard envelope:

```json
{
    "event_id": "8f88cf50-8dc4-4d89-a292-12a8a77d54e4",
    "event_type": "git_commit_detected",
    "timestamp": "2026-10-05T16:30:10Z",
    "collector": {
        "version": "0.1.0",
        "device_id": "DEV-001"
    },
    "identity": {
        "member_id": "M001"
    },
    "context": {
        "session_id": null,
        "project_id": null,
        "jira_task_id": null
    },
    "payload": {
        "repository": "ai-telemetry-collector",
        "branch": "main",
        "commit_sha": "9a38efc...",
        "author": "Alice",
        "message": "feat: implement telemetry collector v0"
    }
}
```

### AI Telemetry Schema

For AI events, payloads conform to:

```json
{
    "provider": null,
    "model": null,
    "input_tokens": null,
    "output_tokens": null,
    "cache_read_tokens": null,
    "cache_write_tokens": null,
    "total_tokens": null,
    "duration_ms": null,
    "cost_usd": null,
    "turns": null,
    "tools": [],
    "agents": []
}
```

*Rule: If an AI telemetry field is unavailable from the source, it is set to `null`. It is never estimated or inferred from character length.*

---

## 4. Supported Event Types

### Collector Events
- `collector_started`: Emitted upon agent initialization.
- `collector_stopped`: Emitted during graceful agent shutdown with uptime stats.
- `collector_error`: Emitted on runtime errors.
- `collector_heartbeat`: Emitted periodically (default: 30s) with health and uptime status.

### Developer & Session Events
- `developer_detected`: Emitted on startup with hostname and local member binding.
- `session_started`: Session initialization event.
- `session_ended`: Session termination event.

### Git Events
- `git_repository_detected`: Emitted when a local Git repository is discovered.
- `git_branch_changed`: Emitted when switching Git branches.
- `git_commit_detected`: Emitted when a new commit is created locally.

### AI Telemetry Events
- `ai_request`
- `ai_response`
- `ai_run`
- `tool_call`
- `agent_started`
- `agent_completed`

---

## 5. Identity Mechanism

In Phase 1, human developer identity is decoupled from shared IDE/organization accounts:
- `MEMBER_ID`: Identifies the human developer (e.g. `M001`).
- `DEVICE_ID`: Identifies the developer machine (e.g. `DEV-001`).
- Sourced via `.env` configuration or environment variables.
- Verified at startup by `LocalIdentityProvider`. Missing configuration triggers clear startup errors.
- In future phases, `LocalIdentityProvider` will be replaced by an SSO/Auth provider without modifying the downstream pipeline.

---

## 6. Storage Mechanism

- Engine: **SQLite** (`./data/telemetry.db`).
- Schema:
  - `events`: `event_id` (PK), `event_type`, `timestamp`, `member_id`, `device_id`, `session_id`, `project_id`, `jira_task_id`, `payload` (JSON), `created_at`.
- Indexes:
  - `idx_events_event_id`
  - `idx_events_timestamp`
  - `idx_events_event_type`
  - `idx_events_member_id`
  - `idx_events_device_id`

---

## 7. Queue Mechanism

- Implemented as `SQLiteEventQueue` conforming to the `EventQueue` abstract interface.
- Supports `enqueue`, `dequeue`, `retry`, and `mark_processed`.
- Decouples event generation from persistence, enabling seamless future swap to HTTP/FastAPI remote ingest without changing event models.

---

## 8. Git Telemetry

- Monitors repository roots via non-destructive, read-only Git commands (`git rev-parse`, `git log`).
- Tracks repository discovery, active branch transitions, and commit authorship.
- Strictly captures metadata only: no file diffs, code content, or working tree data are ever collected.

---

## 9. Antigravity Telemetry Investigation & Adapter

### Investigation Findings
- Antigravity IDE stores internal runtime sessions and execution transcripts locally under `.gemini/antigravity-ide/brain/<conversation-id>/...`.
- However, there is currently **no public, documented real-time event socket or webhook** for external streaming of per-request token usage or cost metrics.
- Attempting to scrape internal files or session transcripts risks leaking private code and violating user privacy constraints.

### Adapter Status
- Status: `ANTIGRAVITY_AI_TELEMETRY_STATUS = "NOT_AVAILABLE"`
- An `AntigravitySource` adapter interface is provided. It enforces schema compliance and preserves strict `null` values for unknown metrics.
- Future Integration: Once an official Antigravity plugin, MCP sidecar, or network proxy is configured, the `AntigravitySource.emit_ai_event` adapter will stream real-time events directly into the pipeline.

---

## 10. Privacy Model & Allowlist Approach

- **Strict Allowlist**: Only structured engineering telemetry (branch names, commit hashes, uptime, agent lifecycle) is collected.
- **Never Collected**:
  - Source code or file contents
  - Unstaged code or diffs
  - Passwords, secrets, `.env` contents
  - API keys (OpenAI, Anthropic, AWS, GitHub)
  - Full shell / terminal history
  - Browser history
- **Automated Scrubbing**: All payload fields pass through `sanitize_data()` which redacts sensitive key names and regex matches for access tokens.

---

## 11. Configuration Reference

| Variable | Default | Description |
| :--- | :--- | :--- |
| `MEMBER_ID` | *Required* | Human developer identity (e.g. `M001`) |
| `DEVICE_ID` | *Required* | Machine identifier (e.g. `DEV-001`) |
| `COLLECTOR_VERSION` | `0.1.0` | Collector version |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `HEARTBEAT_INTERVAL_SECONDS` | `30` | Interval between heartbeat pulses |
| `LOCAL_DATABASE_PATH` | `./data/telemetry.db` | Local SQLite database location |
| `GIT_MONITOR_INTERVAL_SECONDS` | `10` | Git polling interval in seconds |
| `GIT_MONITOR_PATHS` | `.` | Comma-separated list of repository paths to monitor |
| `ANTIGRAVITY_MONITOR_ENABLED` | `true` | Enable Antigravity adapter |

---

## 12. CLI Usage

```bash
# Start collector
python -m src.main start

# Start with custom member/device overrides
python -m src.main start --member M002 --device DEV-002

# Check collector status
python -m src.main status

# Inspect last 20 events in table format
python -m src.main events --last 20

# Filter events by type or member
python -m src.main events --type git_commit_detected
python -m src.main events --json

# Stop running collector process
python -m src.main stop
```

---

## 13. Testing

Automated test suites cover:
- Identity validation (`test_identity.py`)
- Standard event envelope and AI payload null preservation (`test_event_model.py`)
- SQLite storage, indexing, and duplicate prevention (`test_storage.py`)
- Queue buffering, batching, and retries (`test_queue.py`)
- Git repository, branch, and commit detection (`test_git_source.py`)
- Collector lifecycle, heartbeats, and error handling (`test_collector_lifecycle.py`)
- Privacy redaction and secret sanitization (`test_privacy.py`)
- End-to-end flow execution (`test_e2e.py`)

Run tests with:
```bash
pytest tests -v
```

---

## 14. Known Limitations (V0)

1. Antigravity AI metrics are currently marked `NOT_AVAILABLE` due to the lack of an official local real-time event hook in the IDE runtime.
2. Local Git detection operates via periodic polling rather than OS filesystem hooks (e.g. inotify / fsevents).
3. Storage is local SQLite only; no remote sync in V0 (by design).

---

## 15. Next-Phase Recommendations (Phase 2 Preview)

1. **Remote Ingestion Worker**: Connect `SQLiteEventQueue` dequeue worker to an HTTP export client transmitting batches to a central FastAPI service.
2. **SSO / Keycloak Integration**: Replace `LocalIdentityProvider` with token-based human identity verification.
3. **Antigravity Extension / Hook**: Implement an official IDE extension or local MCP sidecar to intercept agent runs, model choices, and token counts legitimately.
4. **Jira & Project Binding**: Automatically infer `jira_task_id` from git branch conventions (e.g. `PROJ-123-feature-name`).
