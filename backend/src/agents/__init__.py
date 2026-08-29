"""Agent module for SimLawFirm framework — 纯刑事。

This module provides agent classes for criminal legal simulation scenarios.
"""

from .base_agent import BaseAgent
from .client_agent import ClientAgent
from .investigator_agent import InvestigatorAgent
from .judge_agent import JudgeAgent
from .lawyer_agent import LawyerAgent
from .prosecutor_agent import ProsecutorAgent
from .receptionist_agent import ReceptionistAgent

__all__ = [
    "BaseAgent",
    "ClientAgent",
    "LawyerAgent",
    "JudgeAgent",
    "ReceptionistAgent",
    "ProsecutorAgent",
    "InvestigatorAgent",
]
