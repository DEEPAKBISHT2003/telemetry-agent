# AI Telemetry Collector (Phase 1 — V0)

A standalone local telemetry collector designed to run on a developer's laptop to capture local developer activity, Git metadata, and AI telemetry signals in an organization with shared accounts.

---

## Key Features

- **Decoupled Developer Identity**: Supports local configurable `MEMBER_ID` and `DEVICE_ID`, decoupling machine activity from shared organizational accounts.
- **Provider-Independent Architecture**: Generic telemetry architecture with pluggable event sources (`SystemSource`, `GitSource`, `AntigravitySource`).
- **Standardized Event Schema**: Single standardized envelope with strict ISO-8601 timestamps, UUIDs, collector metadata, and sanitized payloads.
- **Privacy First (Allowlist & Secret Redaction)**: Never collects source code, terminal history, passwords, or API keys. Automatic sanitization of sensitive values.
- **Resilient Local Pipeline**: Events flow through `Source -> Validation -> Normalization -> Local Queue -> SQLite Storage`.
- **Zero Fabrication of AI Telemetry**: Standard AI schema that strictly stores `null` when metrics are unavailable from official sources.
- **Structured Logging & CLI**: Built-in CLI for starting, inspecting event status, viewing historical events, and stopping the daemon.

---

## Installation

The collector is built with standard Python and has zero mandatory runtime dependencies.

1. Clone or navigate to the repository:
```bash
cd ai-telemetry-collector
```

2. (Optional) Install development and testing dependencies:
```bash
pip install -r requirements-dev.txt
```

---

## Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Set your local developer identity and preferences:

```ini
# Developer Identity (Required)
MEMBER_ID=M001
DEVICE_ID=DEV-001

# Collector Configuration
COLLECTOR_VERSION=0.1.0
LOG_LEVEL=INFO
HEARTBEAT_INTERVAL_SECONDS=30
LOCAL_DATABASE_PATH=./data/telemetry.db

# Event Source Settings
GIT_MONITOR_INTERVAL_SECONDS=10
GIT_MONITOR_PATHS=.
ANTIGRAVITY_MONITOR_ENABLED=true
```

---

## Quickstart & CLI Commands

### 1. Start the Collector
```bash
python -m src.main start
```

Or with inline identity overrides:
```bash
python -m src.main start --member M001 --device DEV-001
```

### 2. Check Collector Status
```bash
python -m src.main status
```

### 3. Inspect Collected Events
View recent events in a structured table:
```bash
python -m src.main events --last 20
```

Filter by event type:
```bash
python -m src.main events --type git_commit_detected
```

Export events as JSON:
```bash
python -m src.main events --json
```

### 4. Stop the Collector
```bash
python -m src.main stop
```

---

## Running Tests

Run the full automated test suite (22 unit, lifecycle, privacy, and E2E tests):

```bash
pytest tests -v
```

---

## Architecture & Documentation

For detailed architectural diagrams, schema definitions, and investigation notes regarding Antigravity telemetry, see [TELEMETRY_COLLECTOR_V0.md](file:///c:/Users/Dell/Desktop/ai-telemetry-collector/docs/TELEMETRY_COLLECTOR_V0.md).
