"""Typed questions owned by this agent.

Turn and merchant questions come from `decision_layer`; this adds the explicit
confirmation check for a pending dispute.
"""

from decision_layer.base import Noul

CONFIRMATION = Noul(
    instructions="Does the customer explicitly agree to open the dispute that was just proposed to them?",
    true="The customer clearly says yes or asks to proceed",
    false="The customer declines, hesitates, asks something else, or the answer is unclear",
)
