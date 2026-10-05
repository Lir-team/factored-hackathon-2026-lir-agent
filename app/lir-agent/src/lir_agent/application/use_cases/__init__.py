"""Use cases, one class per business action, each exposing `execute`."""

from lir_agent.application.use_cases.answer_approval_button import AnswerApprovalButton
from lir_agent.application.use_cases.answer_telegram import AnswerTelegramMessage
from lir_agent.application.use_cases.approvals import (
    DecideApproval,
    PresentApprovals,
    RequestActionApproval,
    RequestApproval,
    VerifyApprovalLink,
)
from lir_agent.application.use_cases.demo_sign_in import DemoSession, IssueDemoSession
from lir_agent.application.use_cases.dispute_approval import OpenDisputeAction
from lir_agent.application.use_cases.find_candidates import (
    FindCandidateTransactions,
    SearchCriteria,
)
from lir_agent.application.use_cases.gather_evidence import GatherTransactionEvidence
from lir_agent.application.use_cases.get_profile import GetCustomerProfile
from lir_agent.application.use_cases.process_case import ProcessCase
from lir_agent.application.use_cases.request_handoff import RequestHandoff
from lir_agent.application.use_cases.route_turn import RouteTurn
from lir_agent.application.use_cases.submit_case import SubmitCase

__all__ = [
    "AnswerApprovalButton",
    "AnswerTelegramMessage",
    "DecideApproval",
    "DemoSession",
    "FindCandidateTransactions",
    "GatherTransactionEvidence",
    "GetCustomerProfile",
    "IssueDemoSession",
    "OpenDisputeAction",
    "PresentApprovals",
    "ProcessCase",
    "RequestActionApproval",
    "RequestApproval",
    "RequestHandoff",
    "RouteTurn",
    "SearchCriteria",
    "SubmitCase",
    "VerifyApprovalLink",
]
