"""Approval request stores."""

from lir_agent.infrastructure.approvals.in_memory import InMemoryApprovalRepository
from lir_agent.infrastructure.approvals.telegram import TelegramApprovalSurface

__all__ = ["InMemoryApprovalRepository", "TelegramApprovalSurface"]
