# AI Telemetry Agent

A local developer telemetry agent for Antigravity AI usage, Git activity, and engineering signals.

---

## Architecture Overview

```
Developer Interaction
       │
       ▼
Antigravity Lifecycle Hooks (%USERPROFILE%\.gemini\config\hooks.json)
       │
       ▼
AI Telemetry Agent Collector (ai_telemetry_agent)
       │
       ▼
Append-Only Daily JSONL Storage (~/.telemetry_agent/)
```

### Storage Structure

```
~/.telemetry_agent/
├── identity.json
├── device_id
└── data/
    ├── telemetry-2026-10-07.jsonl
    ├── telemetry-2026-10-08.jsonl
    └── telemetry-YYYY-MM-DD.jsonl
```

- **`identity.json`** (`WHO`): Local developer identity and hardware metadata (`member_id`, `member_name`, `device_id`, `hostname`).
- **`data/telemetry-YYYY-MM-DD.jsonl`** (`WHAT`): Append-only daily JSONL telemetry files named by UTC date. Exactly one valid JSON object per line.

---

## Privacy & Security

The AI Telemetry Agent enforces strict privacy boundaries:
- **NO Prompts or Model Responses**: Never collects, records, or transmits conversational text or prompts.
- **NO Source Code or File Contents**: Never collects repository file contents, code diffs, or patch bodies.
- **NO Tool Arguments or Command History**: Never stores terminal history or tool call parameter values.
- **NO Secrets or Credentials**: Environment variables, API keys, passwords, and tokens are scrubbed and excluded.
- **Metadata Only**: Collects only execution metadata (event type, UTC timestamp, session ID, model name, tool name, repository name, branch).

---

## Installation & Setup

### 1. Install Package
```bash
pip install ai-telemetry-agent
```

### 2. First-Time Developer Enrollment & Hook Setup
Run the setup command:
```bash
telemetry-agent install
```

When run for the first time, you will be prompted for your name:
```text
========================================
       AI Telemetry Agent Setup
========================================

Enter your name: Rahul Sharma

Developer: Rahul Sharma
Device: DELL-LAPTOP-123
Device ID: dev-401e19f040bb

Save this identity? [Y/n]: Y
[OK] Developer identity saved
[OK] Antigravity hooks installed
[OK] Telemetry agent configured
```

Subsequent runs are idempotent and reuse the saved identity:
```text
========================================
       AI Telemetry Agent Setup
========================================
[OK] Telemetry Agent already configured
Developer: Rahul Sharma
Device: DELL-LAPTOP-123
Device ID: dev-401e19f040bb
Member ID: mem-401e19f040
```

---

## CLI Commands

### Agent Status
```bash
telemetry-agent status
```
Displays collector daemon state, local developer identity, device ID, hook installation status, and total recorded events.

### View Recent Events
```bash
# View last 10 events
telemetry-agent events --last 10

# Filter by event type
telemetry-agent events --type ai_run_started

# Filter by repository
telemetry-agent events --repo chatbot-service

# Output formatted JSON
telemetry-agent events --last 5 --json
```

### View Session Summaries
```bash
# Aggregate multi-model sessions across all JSONL logs
telemetry-agent sessions

# Inspect specific session history
telemetry-agent session <session-id>
```

### Repository & Engineering Summary
```bash
# Aggregated metrics per repository
telemetry-agent summary
```

### Manage Antigravity Hooks
```bash
# Check hook installation status
telemetry-agent hook-status

# Install or repair global hooks
telemetry-agent install-hook

# Uninstall hooks cleanly
telemetry-agent uninstall-hook
```

---

## Running Tests

Run the full automated test suite:
```bash
pytest tests -v
```

---

## Building Distribution Packages

```bash
# Clean previous artifacts
python -m build

# Validate distributions
python -m twine check dist/*
```
