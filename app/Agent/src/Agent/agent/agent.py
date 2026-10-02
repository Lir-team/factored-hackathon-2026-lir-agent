"""Root ADK agent backed by a LiteLLM model.

ADK talks to Ollama through LiteLLM, so the provider is a configuration
detail: changing `LLM_MODEL` and `LLM_API_BASE` is enough to point the same
agent at a different backend.
"""

from typing import Any

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

from Agent.agent.hardening.guardrails.authentication import (
    require_authenticated_customer,
)
from Agent.agent.tools.customers import get_my_customer_profile
from Agent.config import get_settings

ROOT_AGENT_NAME = "clir_agent"

ROOT_AGENT_INSTRUCTION = """\
You are the CLIR assistant. Answer concisely and accurately.
If you do not know something, say so instead of guessing.
You serve the customer signed in to this session. Your tools already know who
they are, so never ask for or reveal a customer ID.
"""


def build_model() -> LiteLlm:
    """Create the LiteLLM-backed model from the current settings.

    Switching provider is a `.env` change, never a code change:

    - Ollama:  LLM_MODEL=ollama_chat/<model>  LLM_API_BASE=http://localhost:11434
    - OpenAI:  LLM_MODEL=openai/<model>       LLM_API_KEY=sk-...  LLM_API_BASE=

    The key is passed explicitly because `Settings` reads `.env` without
    exporting it to `os.environ`, where LiteLLM would otherwise look for it.
    """
    settings = get_settings()
    kwargs: dict[str, Any] = {}
    if settings.llm_api_base:
        kwargs["api_base"] = settings.llm_api_base

    if settings.llm_api_key:
        kwargs["api_key"] = settings.llm_api_key.get_secret_value()

    return LiteLlm(model=settings.llm_model, **kwargs)


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
        tools=[get_my_customer_profile],
        before_agent_callback=require_authenticated_customer,
    )


# ADK's CLI (`adk run`, `adk web`) looks for this module-level name.
root_agent = build_root_agent()
