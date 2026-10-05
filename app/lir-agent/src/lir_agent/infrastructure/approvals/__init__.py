"""Approval request stores."""

from lir_agent.infrastructure.approvals.email import EmailApprovalSurface
from lir_agent.infrastructure.approvals.in_memory import InMemoryApprovalRepository
from lir_agent.infrastructure.approvals.slack import SlackApprovalSurface
from lir_agent.infrastructure.approvals.telegram import TelegramApprovalSurface

__all__ = [
    "EmailApprovalSurface",
    "InMemoryApprovalRepository",
    "SlackApprovalSurface",
    "TelegramApprovalSurface",
]
