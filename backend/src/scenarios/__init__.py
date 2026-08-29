"""Scenario module for SimLawFirm framework — 纯刑事。

This module provides scenario classes for criminal legal simulation.
"""

from .base_scenario import BaseScenario
from .criminal_appeal_trial import CriminalAppealTrialScenario
from .criminal_trial import CriminalTrialScenario
from .defense_opinion_drafting import DefenseOpinionDraftingScenario
from .investigation import InvestigationScenario
from .legal_consultation import LegalConsultationScenario
from .prosecution_review import ProsecutionReviewScenario

__all__ = [
    "BaseScenario",
    "LegalConsultationScenario",
    "InvestigationScenario",
    "ProsecutionReviewScenario",
    "DefenseOpinionDraftingScenario",
    "CriminalTrialScenario",
    "CriminalAppealTrialScenario",
]
