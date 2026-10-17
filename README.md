# AI Telemetry Agent

[![PyPI Version](https://img.shields.io/pypi/v/ai-telemetry-agent.svg)](https://pypi.org/project/ai-telemetry-agent/)
[![Python Versions](https://img.shields.io/pypi/pyversions/ai-telemetry-agent.svg)](https://pypi.org/project/ai-telemetry-agent/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A local-first, privacy-preserving developer telemetry collector for **Google Antigravity IDE**, Git workflows, and engineering activity.

---

## Key Features

- **Authoritative Token Usage**: Ingests exact integer token metrics (uncached input, cached input, output, and total tokens) directly from Antigravity's local SQLite state—with zero character-based guesswork or estimation.
- **Kiro CLI & Kiro IDE Support**: Supports Kiro developer workflows across both CLI session files (`~/.kiro/sessions/cli/{session_id}.json`) and IDE session directories (`~/.kiro/sessions/<workspace>/<session_id>/`).
- **Clean Token & Credit Separation**: Maps Kiro's unreported zero token fields to `None` with diagnostic logging, accurately records Kiro credits (`meteringUsage`), and keeps credits strictly distinct from USD financial costs.
- **Multi-Model Session Intelligence**: Accurately tracks model usage transitions (e.g., Gemini 3.7 Flash, Claude 3.7 Sonnet, Claude Haiku 4.5) and tool call counts across IDE and CLI sessions.
- **Git & Repository Correlation**: Automatically links active IDE/CLI sessions with git repositories and branches while stripping credentials from remote URLs.
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
         ┌─────────────────────────────┬───────────────┴───────────────┬─────────────────────────────┐
         ▼                             ▼                               ▼                             ▼
┌──────────────────┐          ┌──────────────────┐           ┌──────────────────┐          ┌──────────────────┐
│   Antigravity    │          │   Antigravity    │           │     Kiro IDE     │          │  Kiro CLI & IDE  │
│ Lifecycle Hooks  │          │      SQLite      │           │    Hooks JSON    │          │  Sessions JSON   │
│  (hooks.json)    │          │  (`state.vscdb`) │           │ (telemetry.json) │          │ (`~/.kiro/...`)  │
└────────┬─────────┘          └────────┬─────────┘           └────────┬─────────┘          └────────┬─────────┘
         │                             │                              │                             │
         └─────────────────────────────┼──────────────────────────────┴─────────────────────────────┘
                                       │
                                       ▼
                     ┌───────────────────────────────────┐
                     │     TelemetryCollector Daemon     │
                     │   - Source: antigravity|kiro|auto │
                     │   - UUIDv5 Deterministic IDs      │
                     │   - Turn-level Deduplication      │
                     │   - Token & Credit Separation     │
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
├── kiro_cli_cursor.json            # Ingestion cursor state for Kiro CLI sessions
├── kiro_ide_cursor.json            # Ingestion cursor state for Kiro IDE sessions
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
Collector Version  : 0.1.2
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
| `telemetry-agent summary` | View aggregated metrics and credits per repo | `--repo <NAME>`, `--json` |
| `telemetry-agent sessions` | List AI sessions and correlated repos | `--repo <NAME>`, `--limit <N>`, `--json` |
| `telemetry-agent session` | Inspect detailed metrics for a single session | `--id <SESSION_ID>`, `--json` |
| `telemetry-agent events` | Inspect recent collected telemetry events | `--last <N>`, `--type <TYPE>`, `--repo <REPO>`, `--json` |
| `telemetry-agent hook-status` | Display global Antigravity lifecycle hook status | `--config <PATH>` |
| `telemetry-agent install-hook` | Install or repair global Antigravity hooks | `--config <PATH>`, `--python <PATH>` |
| `telemetry-agent uninstall-hook`| Remove Antigravity hooks cleanly | `--config <PATH>` |
| `telemetry-agent kiro-hook-status` | Display Kiro IDE lifecycle hook status | `--config <PATH>` |
| `telemetry-agent install-kiro-hook` | Install or repair Kiro IDE hooks (`telemetry.json`) | `--config <PATH>`, `--python <PATH>` |
| `telemetry-agent uninstall-kiro-hook`| Remove Kiro IDE hooks cleanly | `--config <PATH>` |
| `telemetry-agent jira select` | Assign a Jira ticket to the current repository | `<TICKET>`, `--repo <PATH>` |
| `telemetry-agent jira status` | Display active ticket association and branch state | `--repo <PATH>`, `--json` |
| `telemetry-agent jira recent` | List recent or previously associated Jira tickets | `--repo <PATH>`, `--limit <N>`, `--json` |
| `telemetry-agent jira clear` | Clear current association and mark work as unassigned | `--repo <PATH>` |
| `telemetry-agent jira preview` | Preview branch-detected issue key without changing state | `--repo <PATH>`, `--json` |
| `telemetry-agent jira confirm` | Confirm a proposed ticket switch detected from a branch | `--repo <PATH>` |
| `telemetry-agent jira summary` | View aggregated session metrics and credits per ticket | `--repo <NAME>`, `--ticket <KEY>`, `--from`, `--to`, `--json` |
| `telemetry-agent jira sessions` | List AI sessions with attributed Jira tickets | `--repo <NAME>`, `--ticket <KEY>`, `--limit <N>`, `--json` |
| `telemetry-agent jira test-connection` | Verify read-only Jira API authentication & issue access | `[--issue <KEY>]`, `--json` |

---

## Automatic Jira Ticket Attribution

The agent provides persistent, low-friction Jira ticket attribution for AI telemetry events across Antigravity, Kiro CLI, and Kiro IDE.

### How Attribution Works

1. **Immutable Raw Telemetry**: Raw telemetry events are append-only and immutable. Attribution metadata is stored separately in `~/.telemetry_agent/data/ticket_attributions.json` so ticket associations can be evaluated or audited without rewriting historical telemetry files.
2. **Stable Repository Context**: Attribution is anchored to the repository's root working directory, not merely ephemeral branch names. Working on `main` preserves the assigned ticket context.
3. **Session-Level Attribution**: Telemetry usage is attributed at the session boundary based on the active ticket at session start time. This prevents duplicate counting: a session's runs and credits are never split or counted toward multiple tickets.

### Workflows and Examples

#### 1. Selecting a Ticket while on `main`

Developers do not need to create a ticket branch before starting work. You can assign a ticket while on `main` or any default branch:

```bash
# Assign PROJ-148 to current workspace
telemetry-agent jira select PROJ-148
```

Output:
```text
✓ Jira ticket 'PROJ-148' verified and selected for repository 'telemetry-agent'.
  Repository Root: C:/Users/Developer/Desktop/telemetry-agent
  Branch         : main
  Validation     : verified (HTTP 200 - issue exists in Jira)
  Issue Title    : User authentication flow refactor
  Issue Status   : In Progress
  Confirmation   : confirmed (user selected)
  Source         : manual
  Active Since   : 2026-10-14T10:00:00Z
```

##### Existence Validation & Offline Selection Policy
 
- **Live Validation When Configured**: Every ticket key is validated against the Jira REST API before confirming attribution.
- **Strict Rejection of Nonexistent Issues**: If Jira returns HTTP 404 (Not Found), the selection is rejected unconditionally. The active ticket and its attribution interval remain completely unchanged.
- **Connectivity & Authentication Failures**: If Jira cannot be reached (network error, timeout, HTTP 401/403), selection is rejected by default. To attribute telemetry to an unverified ticket while offline or disconnected, pass `--allow-unverified` (or `--offline`):
  ```bash
  telemetry-agent jira select PROJ-148 --allow-unverified
  ```
- **Unconfigured Jira Connection**: When Jira connectivity is not configured, running `jira select` or `jira confirm` is rejected by default to prevent accidental attribution to invalid tickets. You must pass `--allow-unverified` (or `--offline`) to explicitly proceed in offline mode:
  ```bash
  telemetry-agent jira select PROJ-148 --allow-unverified
  ```
  The ticket will be recorded with `validation_status="unverified"`. The agent clearly notes that ticket existence cannot be confirmed and the ticket may not exist.
- **Separation of Confirmation and Validation**: Developer confirmation (`confirmation_state="confirmed"`) reflects user intent to assign work; it is strictly separated from Jira existence validation (`validation_status="verified"` vs `"unverified"`). User confirmation is never treated as proof that the issue exists in Jira.

#### 2. Remembered Context Across Sessions and Branches

Once `PROJ-148` is selected:
- **Daemon Restarts**: The association persists across restarts of the telemetry agent daemon.
- **Multiple AI Sessions**: New Kiro CLI sessions, Kiro IDE sessions, and Antigravity sessions in this workspace automatically inherit `PROJ-148` without running start commands for each task.
- **Branch Creation**: When the developer creates `feature/PROJ-148-login`, the branch key agrees with the active association, and the context is preserved automatically.

#### 3. Automatic Branch Detection and Conflict Confirmation

The agent monitors git branch names for Jira issue keys (e.g., `feature/PROJ-148-login`, `bugfix/PROJ-162-timeout`, `users/dev/PROJ-148-fix`).

- **Agrees**: If the branch contains the same key (`PROJ-148`), the association is preserved.
- **Branch without key (`main`/`master`)**: If the branch has no issue key, an existing valid association is **never erased**.
- **Different key requiring confirmation**: If the branch switches to `bugfix/PROJ-162-timeout` while `PROJ-148` is active, the agent **does not silently overwrite** the confirmed ticket. Instead, it proposes `PROJ-162` as a pending switch:

```bash
telemetry-agent jira status
```

Output:
```text
==================================================
Active Jira Ticket Association
==================================================
Repository     : telemetry-agent
Current Branch : bugfix/PROJ-162-timeout
Active Ticket  : PROJ-148 (confirmed)
Source         : manual
Proposed Switch: PROJ-162 (detected on branch 'bugfix/PROJ-162-timeout')
Action Required: Run 'telemetry-agent jira confirm' to switch active ticket.
==================================================
```

To accept the proposed switch:
```bash
telemetry-agent jira confirm
```

To preview branch detection without applying changes:
```bash
telemetry-agent jira preview
```

#### 4. Switching or Clearing Tickets

```bash
# Explicitly switch active ticket to PROJ-162
telemetry-agent jira select PROJ-162

# Clear association and mark work as unassigned
telemetry-agent jira clear
```

#### 5. Unassigned Usage and Summary Reporting

Usage that occurred before a ticket was assigned or while marked unassigned is reported under `UNASSIGNED`. All usage strictly reconciles without duplicate counting:

```bash
telemetry-agent jira summary
```

Example Output:
```text
======================================================================
JIRA TICKET TELEMETRY SUMMARY
======================================================================

Ticket        : PROJ-148
Title         : User authentication flow refactor
Status        : In Progress
Repositories  : telemetry-agent
Branches      : main, feature/PROJ-148-login
Sessions      : 3
AI Runs       : 12
Total Credits : 4.5200 credits
Tokens        : Input: 12,500 | Output: 3,200 | Cache: 5,000 | Total: 20,700
Duration      : 1450.2s (tracked session duration, not active coding time)
Source        : manual (confirmed)
Session IDs   : sess-001, sess-002, sess-003
--------------------------------------------------
Ticket        : UNASSIGNED
Repositories  : telemetry-agent
Branches      : main
Sessions      : 1
AI Runs       : 2
Total Credits : 0.5000 credits
Duration      : 120.0s (tracked session duration, not active coding time)
Source        : none (none)
Session IDs   : sess-004
--------------------------------------------------

Reconciliation:
  Attributed Sessions: 3 | Unassigned: 1 | Total: 4
  Attributed Credits : 4.5200 | Unassigned: 0.5000 | Total: 5.0200
======================================================================
```

To list sessions with their attributed ticket:
```bash
telemetry-agent jira sessions
```

### Attribution Granularity & Limitations

- **Session-Level Attribution**: Attribution is calculated at the session level. A session is assigned to the ticket active when the session started. If an AI session spans across an association change, all credits and runs for that session are preserved with the initiating ticket to guarantee that credits and runs are never double-counted.
- **Credits vs. Cost**: AI credits (e.g. from Kiro metering) represent consumption units and are kept strictly distinct from USD financial costs.
- **Tracked Duration**: Session duration measures elapsed time between first and last events in a session; it is not equivalent to active developer coding time.
- **Token Counts**: Unreported token metrics remain `None` (displayed as `-`), never fabricated as zeros.

### Jira API Integration (Optional)

The core attribution features (selection, remembered context, branch detection, reconciliation, and reporting) work **100% offline** without network access or Jira API configuration.

When configured, the agent uses a **read-only** adapter to resolve issue titles and statuses. It never alters Jira issues, comments, workflows, or permissions. Because organization Jira deployment types (Cloud vs. Server/Data Center) and authentication mechanisms vary, the deployment and authentication method must be confirmed separately.

Supported environment variables:

| Variable | Description | Default |
| :--- | :--- | :--- |
| `JIRA_BASE_URL` | Base URL of Jira instance (e.g. `https://your-domain.atlassian.net`) | *(none, offline)* |
| `JIRA_USERNAME` | Account email address for Jira Cloud Basic Auth | *(none)* |
| `JIRA_API_TOKEN` / `JIRA_PAT` | Personal Access Token or Atlassian Cloud API Token (never logged or stored) | *(none)* |
| `JIRA_AUTH_TYPE` | Authentication type (`basic`, `pat`, `bearer`, `token`) | `basic` for Cloud / `pat` for Server |
| `JIRA_KEY_PATTERN` | Custom regex pattern for issue key extraction | `(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]+-[0-9]+)(?![A-Za-z0-9])` |

---

## Configuration

Settings can be specified via environment variables or a local `.env` file (passed via `--env-file .env`):

| Variable | Default | Description |
| :--- | :--- | :--- |
| `MEMBER_ID` | System auto-generated | Unique developer identifier |
| `DEVICE_ID` | Persistent hardware UUID | Unique device identifier |
| `TELEMETRY_DATA_DIR` | `~/.telemetry_agent/data` | Destination directory for daily JSONL logs |
| `TELEMETRY_SOURCE` | `antigravity` | Active source (`antigravity`, `kiro_ide`, `kiro_cli`, `auto`) |
| `ANTIGRAVITY_MONITOR_ENABLED` | `true` | Enable/disable Antigravity SQLite monitor |
| `KIRO_MONITOR_ENABLED` | `true` | Enable/disable Kiro session monitor |
| `KIRO_SESSIONS_DIR` | `~/.kiro/sessions` | Base directory for Kiro CLI and IDE sessions |
| `GIT_MONITOR_PATHS` | `.` | Monitored repository root paths (comma-separated) |
| `GIT_MONITOR_INTERVAL_SECONDS` | `10` | Frequency of git status checks (seconds) |
| `HEARTBEAT_INTERVAL_SECONDS` | `30` | System heartbeat frequency (seconds) |
| `LOG_LEVEL` | `INFO` | Collector log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `JIRA_BASE_URL` | `None` | Optional Jira Server/Data Center base URL |
| `JIRA_AUTH_TYPE` | `pat` | Jira authentication strategy (`pat`, `bearer`, `basic`) |
| `JIRA_API_TOKEN` / `JIRA_PAT` | `None` | Jira API Token or Personal Access Token |
| `JIRA_KEY_PATTERN` | `None` | Optional custom issue key regex pattern |

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

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
