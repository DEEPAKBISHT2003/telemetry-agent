# AI Telemetry Agent

[![PyPI Version](https://img.shields.io/pypi/v/ai-telemetry-agent.svg)](https://pypi.org/project/ai-telemetry-agent/)
[![Python Versions](https://img.shields.io/pypi/pyversions/ai-telemetry-agent.svg)](https://pypi.org/project/ai-telemetry-agent/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A local-first, privacy-preserving developer telemetry collector for **Google Antigravity IDE**, Git workflows, and engineering activity.

---

## Key Features

- **Authoritative Token Usage**: Ingests exact integer token metrics (uncached input, cached input, output, and total tokens) directly from Antigravity's local SQLite state—with zero character-based guesswork or estimation.
- **Multi-Model Session Intelligence**: Accurately tracks model usage transitions (e.g., Gemini 3.7 Flash, Claude 3.7 Sonnet) and tool call counts across IDE sessions.
- **Git & Repository Correlation**: Automatically links active IDE sessions with git repositories and branches while stripping credentials from remote URLs.
- **Zero Data Loss Architecture**: Uses append-only date-partitioned JSONL storage, deterministic RFC 4122 UUIDv5 event identities, and transactional cursor advancement that rolls back on disk/IO errors.
- **Strict Enterprise Privacy**: Collects metadata **only**. Never records prompts, model completions, conversation transcripts, source code, file contents, or raw tool call arguments.
- **Standard Library Runtime**: Built entirely on Python standard library modules—no heavy runtime dependencies required.

---

## Architecture Overview

```
                               ┌───────────────────────────────────────────────┐
                               │           Developer IDE Interaction           │
                               └───────────────────────┬───────────────────────┘
                                                       │
                     ┌─────────────────────────────────┴─────────────────────────────────┐
                     ▼                                                                   ▼
       ┌───────────────────────────┐                                       ┌───────────────────────────┐
       │   Antigravity Lifecycle   │                                       │    Antigravity SQLite     │
       │           Hooks           │                                       │   Adapter (Read-Only)     │
       │ (hooks_template.json)     │                                       │ (`state.vscdb` WAL Mode)  │
       └─────────────┬─────────────┘                                       └─────────────┬─────────────┘
                     │                                                                   │
                     │  Lifecycle Events (pre_invocation, pre_tool_use)                  │  Authoritative Tokens & Runs
                     └─────────────────────────────────┬─────────────────────────────────┘
                                                       │
                                                       ▼
                                     ┌───────────────────────────────────┐
                                     │     TelemetryCollector Daemon     │
                                     │   - UUIDv5 Deterministic IDs      │
                                     │   - Deduplication Engine          │
                                     │   - Privacy & Token Sanitization  │
                                     │   - Git Repository Correlator     │
                                     └─────────────────┬─────────────────┘
                                                       │
                                                       ▼
                                     ┌───────────────────────────────────┐
                                     │ Append-Only Daily JSONL Storage   │
                                     │    (~/.telemetry_agent/data/)     │
                                     └───────────────────────────────────┘
```

### Storage Structure

```text
~/.telemetry_agent/
├── identity.json                   # Local developer identity (member_id, member_name, device_id, hostname)
├── device_id                       # Persistent hardware UUID
├── antigravity_sqlite_cursor.json  # Ingestion cursor state for Antigravity SQLite
├── collector.pid                   # Process lock when daemon is running
└── data/
    ├── telemetry-2026-10-08.jsonl  # Append-only daily logs (UTC date)
    ├── telemetry-2026-10-09.jsonl
    └── telemetry-YYYY-MM-DD.jsonl
```

- **`identity.json`** (`WHO`): Local developer identity and hardware identifiers.
- **`data/telemetry-YYYY-MM-DD.jsonl`** (`WHAT`): Append-only daily JSONL telemetry files named by UTC date. Exactly one valid JSON object per line.
- **`antigravity_sqlite_cursor.json`**: Persisted stream cursor preventing duplicate event ingestion across daemon restarts.

---

## Privacy & Security

The AI Telemetry Agent enforces strict privacy boundaries:

- **NO Prompts or Model Responses**: Never collects, records, or transmits conversational text, prompts, or model completions.
- **NO Source Code or File Contents**: Never inspects or stores repository file contents, code diffs, or patch bodies.
- **NO Tool Arguments or Command History**: Never stores terminal history or tool call parameter payloads.
- **NO Secrets or Credentials**: Environment variables, API keys, passwords, and tokens are scrubbed and excluded. Remote git URLs are stripped of authentication tokens.
- **NO Fabricated Financial Costs**: `cost_usd` is strictly preserved as `null` unless an authoritative financial source is configured.
- **Metadata Only**: Collects only execution metadata (event type, UTC timestamp, session ID, model name, tool name, token counts, repository name, branch).

---

## Installation & Setup

### 1. Install Package

Install from [PyPI](https://pypi.org/project/ai-telemetry-agent/):

```bash
pip install ai-telemetry-agent
```

### 2. First-Time Developer Enrollment & Hook Setup

Run the setup command:

```bash
telemetry-agent install
```

When run interactively for the first time:

```text
========================================
       AI Telemetry Agent Setup
========================================

Enter your name: Rahul Sharma

Detected device:
  Hostname: WORKSTATION-01
  Device ID: dev-401e19f040bb

Developer:
  Rahul Sharma

Save this identity? [Y/n]: Y
[OK] Developer identity saved
[OK] Antigravity hooks installed
[OK] Telemetry agent configured
```

#### Non-Interactive Setup (CI / Automated Environments):

```bash
telemetry-agent install --name "Rahul Sharma" -y
```

---

## Operating the Collector

### Start the Collector Daemon

Start the background telemetry collector:

```bash
telemetry-agent start
```

### Check Collector Status

Inspect daemon status, developer identity, storage directories, and event counts:

```bash
telemetry-agent status
```

Example output:
```text
==================================================
Telemetry Collector Status
==================================================
Process Status     : RUNNING (PID: 12345)
Developer Name     : Rahul Sharma
Configured Member  : M001
Configured Device  : DEV-001
Hostname           : WORKSTATION-01
Collector Version  : 0.1.4
Storage Directory  : C:\Users\Username\.telemetry_agent\data
Storage Format     : Date-based JSONL (telemetry-YYYY-MM-DD.jsonl)
Antigravity Status : GEN_METADATA_LOCAL_SUPPORTED
Total Stored Events: 2293
Last Event Time    : 2026-10-09T11:47:32Z (ai_tool_call)
==================================================
```

### Stop the Collector Daemon

Cleanly stop the running collector:

```bash
telemetry-agent stop
```

---

## CLI Commands Reference

| Command | Description | Common Flags |
| :--- | :--- | :--- |
| `telemetry-agent install` | Enroll developer identity and configure hooks | `--name <NAME>`, `-y`, `--member <ID>`, `--device <ID>` |
| `telemetry-agent start` | Start the local telemetry collector daemon | `--member <ID>`, `--device <ID>` |
| `telemetry-agent stop` | Stop the running collector daemon | *(none)* |
| `telemetry-agent status` | Display collector daemon and storage status | *(none)* |
| `telemetry-agent summary` | View aggregated metrics per repository | `--repo <NAME>`, `--json` |
| `telemetry-agent sessions` | List Antigravity sessions and associated repos | `--repo <NAME>`, `--limit <N>`, `--json` |
| `telemetry-agent session` | Inspect detailed metrics for a single session | `--id <SESSION_ID>`, `--json` |
| `telemetry-agent events` | Inspect recent collected telemetry events | `--last <N>`, `--type <TYPE>`, `--repo <REPO>`, `--json` |
| `telemetry-agent hook-status` | Display global Antigravity lifecycle hook status | `--config <PATH>` |
| `telemetry-agent install-hook` | Install or repair global Antigravity hooks | `--config <PATH>`, `--python <PATH>` |
| `telemetry-agent uninstall-hook`| Remove Antigravity hooks cleanly | `--config <PATH>` |

### Usage Examples

```bash
# View summary of AI usage aggregated by repository
telemetry-agent summary

# Inspect a specific Antigravity session breakdown
telemetry-agent session --id 8a8bcf99-9bdf-4054-b572-10104bde081a

# View the last 10 tool call events in JSON format
telemetry-agent events --last 10 --type ai_tool_call --json

# List recent sessions for a specific repository
telemetry-agent sessions --repo ai-telemetry-collector --limit 10
```

---

## Configuration

Settings can be specified via environment variables or a local `.env` file (passed via `--env-file .env`):

| Variable | Default | Description |
| :--- | :--- | :--- |
| `MEMBER_ID` | System auto-generated | Unique developer identifier |
| `DEVICE_ID` | Persistent hardware UUID | Unique device identifier |
| `TELEMETRY_DATA_DIR` | `~/.telemetry_agent/data` | Destination directory for daily JSONL logs |
| `ANTIGRAVITY_MONITOR_ENABLED` | `true` | Enable/disable Antigravity SQLite monitor |
| `GIT_MONITOR_PATHS` | `.` | Monitored repository root paths (comma-separated) |
| `GIT_MONITOR_INTERVAL_SECONDS` | `10` | Frequency of git status checks (seconds) |
| `HEARTBEAT_INTERVAL_SECONDS` | `30` | System heartbeat frequency (seconds) |
| `LOG_LEVEL` | `INFO` | Collector log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## Development & Testing

### Running the Test Suite

Run the full automated test suite (121 tests):

```bash
pytest -v
```

### Building the Package

```bash
python -m build
python -m twine check dist/*
```

### Publishing to PyPI

Secure publishing uses `scripts/publish_pypi.py` with credentials loaded from a local, uncommitted `.env` file:

```bash
# Pre-flight check (Dry Run)
python scripts/publish_pypi.py --dry-run

# Upload to PyPI
python scripts/publish_pypi.py
```

---

## Technical Documentation

For in-depth architectural and implementation specifications:

- **[Architecture Specification](docs/architecture.md)**: Local agent, centralized backend, end-to-end flows, and Mermaid diagrams.
- **[Lifecycle Hooks & Token Accounting](docs/lifecycle-hooks-and-token-accounting.md)**: Antigravity hook IPC contract, Protobuf decoding, exact token formulas, and session correlation.
- **[PyPI Publishing & Architecture Decisions](docs/pypi-publishing-and-architecture-decisions.md)**: Packaging workflow, verification evidence, and comparative analysis of Python agents vs. native IDE extensions.

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
