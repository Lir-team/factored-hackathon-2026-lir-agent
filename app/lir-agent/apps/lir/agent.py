"""ADK entry point: run `adk web apps` or `adk run apps/lir` from the app/lir-agent directory.

`lir_agent` is installed in the PoC environment (`uv sync`), so no import-path setup is needed.
"""

from lir_agent.interface.adk import build_agent

root_agent = build_agent()
