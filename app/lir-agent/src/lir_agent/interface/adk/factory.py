"""Builds the ADK agent from a dependency container."""

from typing import Any

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

from lir_agent.config.settings import Settings, get_settings
from lir_agent.container import Container, build_container
from lir_agent.interface.adk.callbacks import AgentCallbacks
from lir_agent.interface.adk.guidance import CustomerMessages, TurnGuidance
from lir_agent.interface.adk.toolkit import ChargeInvestigationToolkit

AGENT_NAME = "lir_agent"
AGENT_DESCRIPTION = "Lir case agent for charges a customer does not recognize."


def build_model(settings: Settings) -> LiteLlm:
    """Create the LiteLLM-backed model from settings.

    Switching provider is a `.env` change, never a code change:

    - Ollama:  LLM_MODEL=ollama_chat/<model>  LLM_API_BASE=http://localhost:11434
    - OpenAI:  LLM_MODEL=openai/<model>       LLM_API_KEY=sk-...  LLM_API_BASE=

    The key is passed explicitly because `Settings` reads `.env` without exporting it
    to `os.environ`, where LiteLLM would otherwise look for it.
    """
    kwargs: dict[str, Any] = {}
    if settings.llm_api_base:
        kwargs["api_base"] = settings.llm_api_base
    if settings.llm_api_key:
        kwargs["api_key"] = settings.llm_api_key.get_secret_value()
    return LiteLlm(model=settings.llm_model, **kwargs)


class AgentFactory:
    """Wires the toolkit, callbacks and model around one container."""

    def __init__(self, container: Container) -> None:
        """Keep the container whose dependencies the agent will use."""
        self._container = container

    def create(self) -> LlmAgent:
        """Build the root agent."""
        c = self._container
        toolkit = ChargeInvestigationToolkit(
            c.get_profile,
            c.find_candidates,
            c.gather_evidence,
            c.open_dispute,
            c.request_handoff,
            c.propose_dispute,
        )
        tools = toolkit.tools()
        self._check_policy_tool_names({tool.__name__ for tool in tools})
        callbacks = AgentCallbacks(
            settings=c.settings,
            policy=c.policy,
            route_turn=c.route_turn,
            dispute_guard=c.dispute_guard,
            guidance=TurnGuidance(
                c.resources.load_mapping(c.settings.turn_guidance_path)
            ),
            messages=CustomerMessages(
                c.resources.load_localized_mapping(c.settings.customer_messages_path)
            ),
            audit=c.audit,
        )
        return LlmAgent(
            name=AGENT_NAME,
            description=AGENT_DESCRIPTION,
            model=build_model(c.settings),
            instruction=c.resources.load_text(c.settings.instruction_path),
            tools=[*tools],
            before_agent_callback=callbacks.before_agent,
            before_model_callback=callbacks.before_model,
            after_model_callback=callbacks.after_model,
            before_tool_callback=callbacks.before_tool,
            after_tool_callback=callbacks.after_tool,
        )

    def _check_policy_tool_names(self, tool_names: set[str]) -> None:
        unknown = self._container.policy.all_referenced_tools() - tool_names
        if unknown:
            raise ValueError(f"policy.yaml references unknown tools: {sorted(unknown)}")


def build_agent(
    settings: Settings | None = None, container: Container | None = None
) -> LlmAgent:
    """Build the agent from settings, or from a prebuilt container in tests."""
    return AgentFactory(
        container or build_container(settings or get_settings())
    ).create()
