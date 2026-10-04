"""Typed questions owned by this agent.

Turn and merchant questions come from `decision_layer`; this adds whether the customer
still rejects a charge the agent explained. Disputes are approved with a button, never
inferred from a message (domain/approvals.py).
"""

from decision_layer.base import Noul

REJECTS_EXPLANATION = Noul(
    instructions=(
        "The bank just explained a charge to the customer. Does the customer still say the "
        "charge is not theirs or ask to dispute it?"
    ),
    true="The customer insists they do not recognize the charge or wants to dispute it",
    false="The customer accepts the explanation, thanks, asks something else, or is unclear",
)
