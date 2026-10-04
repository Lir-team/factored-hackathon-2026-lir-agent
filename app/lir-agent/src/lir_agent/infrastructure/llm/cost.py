"""Turn cost from token counts, priced with LiteLLM's model price list."""

import logging

logger = logging.getLogger(__name__)


def litellm_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """USD cost of one call, or None when the model has no known price (e.g. local Ollama)."""
    import litellm  # heavy import, only when a turn is priced

    try:
        prompt_cost, completion_cost = litellm.cost_per_token(
            model=model,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
        )
    except Exception:
        logger.debug("No price for model %s", model)
        return None
    return round(prompt_cost + completion_cost, 8)
