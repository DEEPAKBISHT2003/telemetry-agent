"""CLI Interface for AI Telemetry Collector (Legacy entry point).

Delegates to ai_telemetry_agent.cli.
"""

import sys
from ai_telemetry_agent.cli import cli_entrypoint

if __name__ == "__main__":
    cli_entrypoint()
