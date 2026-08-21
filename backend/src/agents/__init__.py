"""Agent module for SimLawFirm framework — 纯刑事。

This module provides agent classes for criminal legal simulation scenarios.
"""

from .base_agent import BaseAgent
from .client_agent import ClientAgent
from .lawyer_agent import LawyerAgent
from .judge_agent import JudgeAgent
from .receptionist_agent import ReceptionistAgent
from .prosecutor_agent import ProsecutorAgent
from .investigator_agent import InvestigatorAgent

__all__ = [
    "BaseAgent",
    "ClientAgent",
    "LawyerAgent",
    "JudgeAgent",
    "ReceptionistAgent",
    "ProsecutorAgent",
    "InvestigatorAgent",
]
