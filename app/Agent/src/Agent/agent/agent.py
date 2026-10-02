"""Root ADK agent backed by a LiteLLM model.

ADK talks to Ollama through LiteLLM, so the provider is a configuration
detail: changing `LLM_MODEL` and `LLM_API_BASE` is enough to point the same
agent at a different backend.
"""

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

from Agent.agent.tools.customers import get_customer_by_id
from Agent.config import get_settings

ROOT_AGENT_NAME = "clir_agent"

ROOT_AGENT_INSTRUCTION = """\
You are the CLIR assistant. Answer concisely and accurately.
If you do not know something, say so instead of guessing.
"""


def build_model() -> LiteLlm:
    """Create the LiteLLM-backed model from the current settings."""
    settings = get_settings()
    return LiteLlm(model=settings.llm_model, api_base=settings.llm_api_base)


def build_root_agent() -> LlmAgent:
    """Create the root agent from the current settings.

    Building on demand, rather than at import time, lets tests and callers
    change the environment before the model is resolved.
    """
    return LlmAgent(
        name=ROOT_AGENT_NAME,
        model=build_model(),
        description="Root agent for the CLIR hackathon project.",
        instruction=ROOT_AGENT_INSTRUCTION,
        tools=[get_customer_by_id],
    )


# ADK's CLI (`adk run`, `adk web`) looks for this module-level name.
root_agent = build_root_agent()
