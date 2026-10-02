"""ADK agent package.

`adk run` and `adk web` discover an agent by importing this package and
reading `agent.root_agent`, so the module import below is required.
"""

from . import agent

__all__ = ["agent"]
