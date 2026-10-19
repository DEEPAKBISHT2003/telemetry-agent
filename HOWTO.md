# AI Telemetry Agent — Developer Operations & Setup Guide (`HOWTO.md`)

This guide provides step-by-step instructions for cloning, installing, configuring, running, testing, troubleshooting, and validating the **AI Telemetry Agent** (`ai-telemetry-agent` v0.1.2) from a fresh checkout.

---

## Table of Contents

1. [Overview & Prerequisites](#1-overview--prerequisites)
2. [Fresh Installation](#2-fresh-installation)
3. [Configuration & Environment Precedence](#3-configuration--environment-precedence)
4. [Running the Agent](#4-running-the-agent)
5. [Telemetry Source Testing](#5-telemetry-source-testing)
   - [Kiro CLI Sessions](#kiro-cli-sessions)
   - [Kiro IDE Sessions & Hooks](#kiro-ide-sessions--hooks)
   - [Antigravity IDE & SQLite Tracking](#antigravity-ide--sqlite-tracking)
   - [System & Git Repository Sources](#system--git-repository-sources)
6. [Jira Ticket Attribution](#6-jira-ticket-attribution)
7. [Accounting & Credit Estimation](#7-accounting--credit-estimation)
8. [Automated Testing](#8-automated-testing)
9. [Step-by-Step End-to-End Smoke Test](#9-step-by-step-end-to-end-smoke-test)
10. [Troubleshooting Guide](#10-troubleshooting-guide)
11. [Fresh-Checkout Acceptance Checklist](#11-fresh-checkout-acceptance-checklist)
12. [Developer Reference & Codebase Architecture](#12-developer-reference--codebase-architecture)

---

## 1. Overview & Prerequisites

### What is the AI Telemetry Agent?
The **AI Telemetry Agent** is a local-first, privacy-preserving background daemon and accounting ledger designed for engineering workstations. It captures observable metrics from local AI coding tools (**Kiro CLI**, **Kiro IDE**, and **Google Antigravity IDE**), correlates tool usage with active Git branches and **Jira issues**, and maintains an audit-grade decimal accounting ledger of AI credit expenditures.

### Strict Privacy Guarantee
- **Metadata Only**: Collects event types, UTC timestamps, session IDs, model aliases, tool names, token integers, and repository coordinates.
- **Zero Content Capture**: Never inspects, records, or uploads prompt text, completions, chat transcripts, code diffs, file contents, or shell arguments.
- **Credential Scrubbing**: Remote Git URLs have basic authentication credentials stripped. API keys and environment secrets are excluded from logs.

### System Requirements
| Requirement | Specification |
|---|---|
| **Supported OS** | Windows 10/11, macOS 12+, Linux (Ubuntu 20.04+, Debian 11+, RHEL 8+), or Windows Subsystem for Linux (WSL2) |
| **Python Runtime** | Python **>= 3.10** (Tested on Python 3.10, 3.11, 3.12) |
| **Dependencies** | **Zero runtime dependencies**. Standard library only (`json`, `sqlite3`, `dataclasses`, `decimal`, `argparse`, `pathlib`, `uuid`). |
| **Build/Dev Tools** | `pip >= 22.0`, `setuptools >= 61.0`. Optional dev dependencies: `pytest >= 8.0.0`, `pytest-cov >= 4.1.0`. |
| **External Services** | **Optional**. Jira Cloud or Jira Data Center (PAT or Basic Auth) is only required if running live Jira verification. All core functionality runs entirely offline. |

---

## 2. Fresh Installation

### Step 1: Clone the Repository

**PowerShell / Windows CMD / Linux / macOS:**
```bash
git clone https://github.com/example/telemetry-agent.git
cd telemetry-agent
```

### Step 2: Create and Activate a Dedicated Virtual Environment

#### On Windows (PowerShell):
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

#### On Windows (Command Prompt `cmd.exe`):
```cmd
python -m venv .venv
.\.venv\Scripts\activate.bat
```

#### On Linux / macOS / WSL:
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Step 3: Upgrade Pip and Install the Package

Install in editable mode so that source modifications in `src/` are immediately active without reinstalling:

```bash
python -m pip install --upgrade pip
pip install -e .
```

To install optional development and testing tooling (such as `pytest` and `python-dotenv`):
```bash
pip install -e ".[dev]"
```

### Step 4: Verify Installation and Package Parity

Confirm that the CLI entry point resolves directly to your local checkout:

```bash
# Verify Python import location
python -c "import ai_telemetry_agent; print('Installed from:', ai_telemetry_agent.__file__)"

# Verify CLI version and help
telemetry-agent --help
```

*Expected output:*
```text
Installed from: <repo-root>/src/ai_telemetry_agent/__init__.py
usage: telemetry-agent [-h] [--env-file ENV_FILE] [--data-dir DATA_DIR] ...
```

---

## 3. Configuration & Environment Precedence

The agent resolves its configuration through a deterministic layered hierarchy:

```
┌────────────────────────────────────────────────────────┐
│ 1. CLI Arguments (--member, --device, --source, etc.)   │ (Highest Precedence)
├────────────────────────────────────────────────────────┤
│ 2. Environment Variables (TELEMETRY_DATA_DIR, etc.)    │
├────────────────────────────────────────────────────────┤
│ 3. Explicit --env-file <path> or Project .env File     │
├────────────────────────────────────────────────────────┤
│ 4. Persistent Identity File (~/.telemetry_agent/ident) │
├────────────────────────────────────────────────────────┤
│ 5. Default Fallbacks (OS Username, UUIDv4 dev ID)      │ (Lowest Precedence)
└────────────────────────────────────────────────────────┘
```

### Configuration Variables Reference

| Variable Name | Type | Default | Description |
|---|---|---|---|
| `MEMBER_ID` | String | OS Username | Unique developer ID (e.g. corp LDAP or handle). |
| `DEVICE_ID` | String | Persistent UUID | Hardware identifier generated into `~/.telemetry_agent/device_id`. |
| `TELEMETRY_SOURCE` | Enum | `antigravity` | Active collector source: `antigravity`, `kiro_cli`, `kiro_ide`, or `auto`. |
| `TELEMETRY_DATA_DIR` | Path | `~/.telemetry_agent/data` | Directory where daily JSONL and accounting files are stored. |
| `TELEMETRY_AGENT_HOME` | Path | `~/.telemetry_agent` | User application home directory. |
| `LOG_LEVEL` | Enum | `INFO` | Console logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `HEARTBEAT_INTERVAL_SECONDS`| Int | `30` | Interval between system collector heartbeat events. |
| `GIT_MONITOR_PATHS` | List | `["."]` | Comma-delimited directory paths to watch for Git activity. |
| `GIT_MONITOR_INTERVAL_SECONDS`| Int | `10` | Interval in seconds to poll Git branches and commit state. |
| `KIRO_SESSIONS_DIR` | Path | `~/.kiro/sessions` | Custom path to local Kiro session directory. |
| `KIRO_MONITOR_ENABLED` | Bool | `true` | Enables/disables Kiro CLI and IDE session monitors. |
| `ANTIGRAVITY_MONITOR_ENABLED` | Bool | `true` | Enables/disables Google Antigravity SQLite monitor. |
| `JIRA_BASE_URL` | URL | `null` | Jira instance URL (e.g. `https://jira.yourcompany.com`). |
| `JIRA_API_TOKEN` / `JIRA_PAT` | Secret | `null` | Jira Personal Access Token or API Token. |
| `JIRA_USERNAME` | String | `null` | Jira account username (used with Basic Auth). |
| `JIRA_AUTH_TYPE` | Enum | `pat` | Authentication mode: `pat` (Bearer) or `basic`. |
| `JIRA_KEY_PATTERN` | Regex | `[A-Z][A-Z0-9]+-[0-9]+` | Custom regex pattern for parsing Jira issue keys from Git branches. |

### Safe Test Configuration (`test.env`)

To isolate test runs from your live development environment, create a local `test.env` file. **Never commit real API keys or personal credentials.**

```ini
# test.env (Ignored by Git)
MEMBER_ID=dev-test-runner
DEVICE_ID=dev-mock-workstation
LOG_LEVEL=DEBUG
TELEMETRY_SOURCE=auto
TELEMETRY_DATA_DIR=./test_data
GIT_MONITOR_INTERVAL_SECONDS=5

# Optional Jira Configuration (Use placeholders only)
JIRA_BASE_URL=https://jira.example.com
JIRA_AUTH_TYPE=pat
JIRA_API_TOKEN=mock_pat_token_placeholder
```

---

## 4. Running the Agent

### Step 1: Enroll Developer Identity
Run the non-interactive enrollment command to configure your local identity:

```bash
telemetry-agent install --name "Test Engineer" --member "test.engineer" --yes
```

This persists your `member_id` and unique `device_id` into `~/.telemetry_agent/identity.json`.

### Step 2: Check Collector Status

Inspect daemon status, active PID, configured sources, and session counts:

```bash
telemetry-agent status
```

*Expected output when stopped:*
```text
AI Telemetry Collector Status
============================================================
Collector State      : STOPPED
Active Source        : antigravity
Version              : 0.1.2
Storage Directory    : /path/to/.telemetry_agent/data
Total Stored Events  : 0
Total Active Sessions: 0
```

### Step 3: Start the Collector Daemon

You can start the collector targeting a specific source or in `auto` mode (which dynamically detects active Kiro CLI, Kiro IDE, and Antigravity sessions):

```bash
# Start with auto-detection in the foreground
telemetry-agent start --source auto
```

To run with a custom environment file or isolated data directory:
```bash
telemetry-agent --env-file test.env start --source auto
```

### Step 4: Stop the Collector Daemon

When running as a background process or service, stop it gracefully using the PID lockfile:

```bash
telemetry-agent stop
```

*Expected output:*
```text
Sent SIGTERM to collector process (PID <pid>). Collector stopped successfully.
```

### Step 5: Query Stored Telemetry Events & Sessions

```bash
# View the last 10 raw events in chronological order
telemetry-agent events --last 10 --asc

# Output raw JSON stream
telemetry-agent events --last 5 --json

# List active AI sessions and correlated Git repositories
telemetry-agent sessions

# Inspect full provenance for a specific session
telemetry-agent session --id <session-id>
```

---

## 5. Telemetry Source Testing

### Kiro CLI Sessions

Kiro CLI writes completed turn sessions as JSON files in `~/.kiro/sessions/cli/{session_id}.json` and streams incremental message tool calls into `{session_id}.jsonl`.

#### 1. Configuration
Set `TELEMETRY_SOURCE=kiro_cli` or `TELEMETRY_SOURCE=auto`. Optionally set `KIRO_SESSIONS_DIR=/path/to/.kiro/sessions`.

#### 2. Generate a Test Kiro CLI Session
Create a synthetic session file to simulate a completed Kiro CLI prompt turn:

```python
# scratch_kiro_cli.py
import json, time
from pathlib import Path

cli_dir = Path.home() / ".kiro" / "sessions" / "cli"
cli_dir.mkdir(parents=True, exist_ok=True)
sess_id = f"cli_test_{int(time.time())}"

session_data = {
    "session_id": sess_id,
    "selected_model": "claude-haiku-4.5",
    "metering_usage": {
        "credits": 0.045000,
        "input_tokens": 1200,
        "output_tokens": 350
    },
    "history": [
        {"role": "user", "text": "Run unit test"},
        {"role": "assistant", "text": "Running test...", "tool_calls": [{"name": "run_shell", "id": "call_1"}]}
    ]
}

(cli_dir / f"{sess_id}.json").write_text(json.dumps(session_data), encoding="utf-8")
print(f"Created simulated Kiro CLI session: {sess_id}")
```

#### 3. Verify Ingestion
```bash
python scratch_kiro_cli.py
telemetry-agent events --source kiro --last 5
telemetry-agent sessions
```

---

### Kiro IDE Sessions & Hooks

Kiro IDE manages workspace conversations under `~/.kiro/sessions/<workspace>/<session_id>/` and invokes lifecycle hooks configured in `.kiro/hooks/telemetry.json`.

#### 1. Inspect & Install Kiro IDE Hooks

Inspect current hook configuration:
```bash
telemetry-agent kiro-hook-status
```

Install Kiro IDE lifecycle hooks (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `SessionEnd`):
```bash
telemetry-agent install-kiro-hook
```

*Expected output:*
```text
✓ Created backup of existing hooks at: ~/.kiro/hooks/telemetry.json.bak
✓ Successfully installed Kiro IDE lifecycle hooks in ~/.kiro/hooks/telemetry.json
```

#### 2. Test Kiro Hook Handler (Offline/Synthetic)
Invoke the hook handler script directly over standard input without launching the IDE:

```bash
# Windows PowerShell
$payload = @{
    hook_event_name = "PostToolUse"
    session_id = "sess_ide_synthetic"
    tool_use_id = "tooluse_001"
    execution_id = "parent_turn_100"
    tool_name = "read_file"
    cwd = (Get-Location).Path
} | ConvertTo-Json

$payload | python -m ai_telemetry_agent.integrations.hooks.kiro_hook_handler PostToolUse
```

#### 3. Verify Captured Hook Executions
```bash
telemetry-agent accounting hooks
```

---

### Antigravity IDE & SQLite Tracking

Antigravity logs turn metrics into local SQLite state storage (`state.vscdb`).

#### 1. Configuration
Set `TELEMETRY_SOURCE=antigravity` and ensure `ANTIGRAVITY_MONITOR_ENABLED=true`.

#### 2. Install Global Antigravity Lifecycle Hooks
```bash
telemetry-agent install-hook
telemetry-agent hook-status
```

#### 3. Verification
When Antigravity runs prompts or tool calls, the agent tracks exact uncached input tokens, cache-read tokens, output tokens, and total tokens from SQLite with transactional cursor advancement.

---

## 6. Jira Ticket Attribution

The agent automatically correlates AI runs and sessions with confirmed Jira tickets using active Git branches, commit history, or explicit user selection.

### Offline vs. Online Jira Operation
- **Offline / Standalone Mode**: Works immediately without Jira network connectivity or credentials. Tickets detected from Git branch patterns (e.g. `feature/PROJ-148-auth`) are marked `unverified` and can be selected with `--allow-unverified`.
- **Online Verified Mode**: When `JIRA_BASE_URL` and `JIRA_API_TOKEN` are set, queries the Jira REST API to verify issue existence before assignment.

### 1. Test Connection to Jira
```bash
telemetry-agent jira test-connection --ticket PROJ-148 --json
```

### 2. Preview Ticket from Active Git Branch
```bash
telemetry-agent jira preview
```

### 3. Select / Confirm Active Ticket Attribution
```bash
# Online verified selection
telemetry-agent jira select PROJ-148

# Offline selection when Jira is unreachable
telemetry-agent jira select PROJ-148 --allow-unverified
```

### 4. Inspect Ticket Attribution Status
```bash
telemetry-agent jira status
telemetry-agent jira recent
```

### 5. Clear Active Association
```bash
telemetry-agent jira clear
```

### 6. Generate Jira Attribution Summary
```bash
telemetry-agent jira summary
telemetry-agent jira sessions --ticket PROJ-148
```

---

## 7. Accounting & Credit Estimation

The accounting subsystem (`ai_telemetry_agent.accounting`) provides an audit-grade decimal accounting ledger that tracks operational expenditures and resolves unmeasured hook estimates.

### Core Accounting Invariants
1. **Measured Credit Invariance**: Authoritative provider credits (`credits_used`) are recorded once. Hook estimations **never modify or inflate** `credits_used`.
2. **Hook Activity Distinction**: Hook executions are lifecycle audit records, not additive billable operations.
3. **Strict 5-Point Estimation Eligibility**:
   An unmeasured hook execution receives an estimated allocation if and only if:
   - It has an explicit, verified `parent_operation_id`.
   - The parent operation exists and has measured credits (`credits_used > 0`).
   - The session ID and repository context match between parent and hook.
   - Jira ticket attribution is consistent (cross-ticket allocations like `PROJ-148` to `SCRUM-1` are rejected).
   - The relationship is not ambiguous or self-referential.
4. **Idempotent Even Split**: If $N$ eligible hooks are linked to a parent run with $C$ credits, each hook receives $C / N$ estimated credits. Running allocation multiple times produces identical values without multiplying credits.

### Supported Accounting CLI Commands

#### View Overall AI Credit Expenditure Summary
```bash
# Full dimensional summary
telemetry-agent accounting summary

# Summary grouped by provider and trigger mechanism
telemetry-agent accounting summary --by-provider --by-trigger

# Summary grouped by Jira ticket and agent hook
telemetry-agent accounting summary --by-ticket --by-hook

# Summary with estimated credit allocation enabled
telemetry-agent accounting summary --by-ticket --by-hook --estimate-hooks

# Output raw JSON summary for ingestion pipelines
telemetry-agent accounting summary --estimate-hooks --json
```

#### Inspect Individual Operation Records
```bash
# List recent operations
telemetry-agent accounting operations --limit 20

# Filter operations by Jira ticket or session
telemetry-agent accounting operations --ticket PROJ-148 --session sess_123

# Display full audit trail for a single operation ID
telemetry-agent accounting operation --id op_parent_run_888
```

#### Inspect Hook Registry & Allocate Estimates
```bash
# List registered hooks and recent executions
telemetry-agent accounting hooks

# Trigger allocation of estimated credits to correlated hooks
telemetry-agent accounting hooks --allocate-estimates

# Output hook executions and correlation diagnostics as JSON
telemetry-agent accounting hooks --json
```

#### Reconcile Operational Sums Against Session Totals
```bash
# Reconcile all operations against provider checkpoints
telemetry-agent accounting reconciliation

# Reconcile a specific session
telemetry-agent accounting reconciliation --session <session-id>
```

---

## 8. Automated Testing

The project uses Python's standard library `unittest` framework. All tests run cleanly without external mock services or internet access.

### Run the Full Test Suite

```bash
# Run all 193 unit and regression tests
python -m unittest discover -s tests -v
```

*Expected result:*
```text
----------------------------------------------------------------------
Ran 193 tests in ~43s

OK
```

### Run Targeted Test Suites

| Test Target | Command | Focus Area |
|---|---|---|
| **Fast Smoke Test** | `python -m unittest tests.smoke_test -v` | Core collector lifecycle & event persistence |
| **Kiro CLI Source** | `python -m unittest tests.test_kiro_cli_source -v` | JSONL streaming, turn duration, tool metadata |
| **Kiro IDE Source** | `python -m unittest tests.test_kiro_ide_source -v` | Directory parsing, tool call aggregation |
| **Hook Lifecycle & Correlation** | `python -m unittest tests.test_kiro_hooks -v` | Hook registry, parent correlation, idempotency |
| **Credit Accounting** | `python -m unittest tests.test_ai_credit_accounting -v` | Decimal safety, provider breakdowns, reconciliation |
| **Jira Attribution** | `python -m unittest tests.test_jira_attribution -v` | Offline/online attribution, branch parsing, PAT auth |
| **Process Management** | `python -m unittest tests.test_process_liveness -v` | Stale PID file cleanup, daemon signal handling |
| **Settings & Precedence**| `python -m unittest tests.test_settings -v` | Environment variable overrides, directory resolution |

### Running with Pytest (Optional)

If `pytest` is installed in your virtual environment:
```bash
pytest tests/ -v
```

---

## 9. Step-by-Step End-to-End Smoke Test

This reproducible procedure creates an isolated sandbox, starts the collector, generates simulated parent and hook records, validates credit allocation, and performs reconciliation.

### Step 1: Create an Isolated Sandbox Directory
```bash
# Create temporary directories
python -c "
import tempfile, os
from pathlib import Path

sandbox = Path('sandbox_run').resolve()
sandbox.mkdir(exist_ok=True)
data_dir = sandbox / 'data'
data_dir.mkdir(exist_ok=True)
(sandbox / '.env').write_text(f'MEMBER_ID=smoke-user\nDEVICE_ID=smoke-dev-01\nTELEMETRY_DATA_DIR={data_dir}\nTELEMETRY_SOURCE=auto\n', encoding='utf-8')
print(f'Sandbox created at: {sandbox}')
"
```

### Step 2: Ingest a Measured Parent AI Turn
```bash
python -c "
from pathlib import Path
from decimal import Decimal
from ai_telemetry_agent.accounting.store import AccountingStore
from ai_telemetry_agent.accounting.models import AIOperationRecord, OperationType

data_dir = Path('sandbox_run/data').resolve()
store = AccountingStore(data_dir)

# Save initiating prompt turn with measured credits
parent_op = AIOperationRecord(
    operation_id='parent_prompt_1001',
    provider='kiro_ide',
    source='messages_jsonl',
    operation_type=OperationType.PROMPT,
    session_id='sess_smoke_test',
    credits_used=Decimal('2.000000'),
    jira_key='PROJ-500',
    repository={'name': 'telemetry-agent', 'root': '/workspace'}
)
store.save_operation(parent_op)
print('Saved parent operation parent_prompt_1001 with 2.000000 credits')
"
```

### Step 3: Execute Two Correlated Kiro Tool Hooks
```bash
python -c "
import json, os, subprocess, sys
from pathlib import Path

data_dir = str(Path('sandbox_run/data').resolve())
env = dict(os.environ, TELEMETRY_DATA_DIR=data_dir)

for tool_id in ('tooluse_aaa', 'tooluse_bbb'):
    payload = json.dumps({
        'hook_event_name': 'PostToolUse',
        'session_id': 'sess_smoke_test',
        'tool_use_id': tool_id,
        'execution_id': 'parent_prompt_1001',
        'tool_name': 'execute_command',
        'cwd': os.getcwd()
    })
    proc = subprocess.run(
        [sys.executable, '-m', 'ai_telemetry_agent.integrations.hooks.kiro_hook_handler', 'PostToolUse'],
        input=payload, text=True, capture_output=True, env=env
    )
    assert proc.returncode == 0, proc.stderr
print('Executed 2 correlated PostToolUse hook events')
"
```

### Step 4: Run Accounting Summary with Hook Estimation
```bash
telemetry-agent --data-dir sandbox_run/data accounting summary --by-ticket --by-hook --estimate-hooks
```

*Expected Verification Points in Output:*
1. **Total Measured Credits**: Exactly `2.000000` credits (Unchanged and uninflated).
2. **Jira Ticket `PROJ-500`**: Total `2.000000` credits, `1` prompt op, `2` hooks, `est: 2.000000`.
3. **Hook Breakdown (`PostToolUse`)**: 2 executions, each allocated `1.000000` estimated credit (`even_split_by_run`).
4. **Diagnostics**: `Eligible Hooks: 2`, `Ineligible Hooks: 0`, `Ambiguous Hooks: 0`.

### Step 5: Clean Up Sandbox
```bash
python -c "import shutil; shutil.rmtree('sandbox_run', ignore_errors=True); print('Sandbox cleaned up')"
```

---

## 10. Troubleshooting Guide

| Symptom | Probable Cause | Diagnostic Command | Remediation |
|---|---|---|---|
| `ModuleNotFoundError: No module named 'ai_telemetry_agent'` | Package not installed or wrong virtualenv active | `which python` or `Get-Command python` | Activate virtual environment and run `pip install -e .` |
| `ConfigurationError: MEMBER_ID cannot be resolved` | Identity file missing and environment variables not set | `telemetry-agent status` | Run `telemetry-agent install --yes` or set `export MEMBER_ID=<name>` |
| Stale PID warning on start | Previous collector crashed or was killed abruptly | `Get-Process python` or `ps aux \| grep telemetry` | `telemetry-agent stop` removes stale PID lockfile automatically |
| CLI commands report 0 events while hooks run | CLI and hooks reading different storage directories | `echo $TELEMETRY_DATA_DIR` and `telemetry-agent status` | Set `TELEMETRY_DATA_DIR` consistently in shell environment or pass `--data-dir` |
| `kiro-hook-status` cannot find config | Hooks installed in non-standard location | `telemetry-agent kiro-hook-status --config <path>` | Pass explicit `--config <path>` to `install-kiro-hook` |
| Hook estimates show `unallocated` / `None` | Missing or mismatched parent operation ID | `telemetry-agent accounting hooks --json` | Inspect `correlation_reason`. Common reasons: `missing_parent_operation`, `parent_unmeasured`, `ticket_mismatch` |
| Ticket shows `UNASSIGNED` despite Git repo | Branch name does not contain Jira key pattern | `telemetry-agent jira preview` | Assign manually via `telemetry-agent jira select <ticket> --allow-unverified` |
| Jira test connection fails | Token expired or VPN/network proxy unreachable | `telemetry-agent jira test-connection --json` | Verify `JIRA_BASE_URL` and `JIRA_API_TOKEN` in `.env` |
| Duplicate credits after collector restart | Using a non-transactional or custom source | Inspect cursor files (`*_cursor.json`) in state directory | Cursors are stored in `~/.telemetry_agent/` and prevent reprocessing |

---

## 11. Fresh-Checkout Acceptance Checklist

Before delivering code or signing off on a new environment checkout, verify each item:

- [ ] **Clean Virtualenv**: Package installed via `pip install -e .` without dependency conflicts.
- [ ] **CLI Execution**: `telemetry-agent --help` outputs all 15 top-level commands without errors.
- [ ] **Identity Enrollment**: `telemetry-agent install --yes` creates `~/.telemetry_agent/identity.json`.
- [ ] **Full Test Suite**: `python -m unittest discover -s tests -v` passes all 193 tests.
- [ ] **Data Directory Parity**: `telemetry-agent status` displays the expected `Storage Directory`.
- [ ] **Collector Lifecycle**: `telemetry-agent start` and `telemetry-agent stop` manage PID files safely without leaving orphaned processes.
- [ ] **Source Detection**: `telemetry-agent start --source auto` identifies local Kiro and Antigravity states.
- [ ] **Hook Installation**: `telemetry-agent install-kiro-hook` generates valid JSON hooks with automated backups.
- [ ] **Parent Correlation**: Hook executions record `parent_operation_id` distinct from `resulting_operation_id`.
- [ ] **Credit Invariance**: Estimated hook allocations never modify or inflate `total_measured_credits`.
- [ ] **Idempotent Accounting**: Repeated calls to `accounting summary --estimate-hooks` output identical values.
- [ ] **Reconciliation**: `telemetry-agent accounting reconciliation` reports zero discrepancy on clean runs.

---

## 12. Developer Reference & Codebase Architecture

### Key Directories & Responsibilities

```text
src/ai_telemetry_agent/
├── cli.py                     # CLI dispatcher, argument parsers, formatting tables
├── accounting/
│   ├── models.py              # AIOperationRecord, Decimal quantization, OperationType
│   ├── store.py               # AccountingStore, date-partitioned JSONL, dedup caching
│   ├── registry.py            # HookRegistry, lifecycle execution tracking, credit estimation
│   └── aggregator.py          # AccountingAggregator, cross-dimensional rollups, reconciliation
├── attribution/
│   ├── jira_adapter.py        # Jira REST API client (PAT & Basic Auth)
│   ├── store.py               # AttributionStore, ticket selection, interval matching
│   └── aggregator.py          # Ticket-level session and credit summaries
├── config/
│   ├── settings.py            # Settings dataclass, .env parser, directory resolution
│   └── logging.py             # Structured UTC logging configuration
├── core/
│   ├── collector.py           # TelemetryCollector daemon loop, signal handling, PID lock
│   ├── event.py               # Canonical AIEvent schema, UUIDv5 deterministic hashing
│   └── repository.py          # Git repository boundary detection and remote URL sanitation
├── integrations/
│   └── hooks/
│       ├── hook_handler.py    # Antigravity lifecycle hook runner
│       ├── kiro_hook_handler.py# Kiro IDE lifecycle hook runner
│       └── kiro_installer.py  # Hook installation and configuration manager
└── sources/
    ├── kiro_cli_sessions.py   # Ingestion of ~/.kiro/sessions/cli/*.json & *.jsonl
    ├── kiro_ide_sessions.py   # Ingestion of ~/.kiro/sessions/<workspace>/<id>/
    ├── antigravity.py         # Ingestion of Antigravity SQLite state.vscdb
    └── git.py                 # Git branch and commit polling
```

### Adding a New Telemetry Source

To add a new AI tool or telemetry provider:
1. Subclass `ai_telemetry_agent.sources.base.EventSource`.
2. Implement `poll_events() -> List[Dict[str, Any]]` returning raw event payloads.
3. Map token usage using `ai_telemetry_agent.sources.ai_provider.TokenUsageNormalizer`.
4. Register the new source in `TelemetryCollector.__init__()` in [`src/ai_telemetry_agent/core/collector.py`](file:///C:/Users/Dell/Desktop/telemetry-agent/src/ai_telemetry_agent/core/collector.py).
5. Add unit and regression tests in `tests/` validating cursor advancement and error rollback.
