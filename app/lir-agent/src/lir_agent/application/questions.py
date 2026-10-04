"""Typed questions owned by this agent.

Turn and merchant questions come from `decision_layer`; this adds the explicit
confirmation check for a pending dispute and whether the customer rejects an explanation.
"""

from decision_layer.base import Noul

CONFIRMATION = Noul(
    instructions="Does the customer explicitly agree to open the dispute that was just proposed to them?",
    true="The customer clearly says yes or asks to proceed",
    false="The customer declines, hesitates, asks something else, or the answer is unclear",
)

REJECTS_EXPLANATION = Noul(
    instructions=(
        "The bank just explained a charge to the customer. Does the customer still say the "
        "charge is not theirs or ask to dispute it?"
    ),
    true="The customer insists they do not recognize the charge or wants to dispute it",
    false="The customer accepts the explanation, thanks, asks something else, or is unclear",
)
