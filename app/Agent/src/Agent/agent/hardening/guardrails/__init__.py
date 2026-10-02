"""Guardrails: ADK callbacks that block unsafe requests before they run.

One module per guardrail, each registered on the agent in `agent.py`.
A guardrail enforces policy in code; never rely on the prompt alone.
"""
