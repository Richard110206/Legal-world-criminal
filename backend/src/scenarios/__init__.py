"""Scenario module for SimLawFirm framework — 纯刑事。

This module provides scenario classes for criminal legal simulation.
"""

from .base_scenario import BaseScenario
from .legal_consultation import LegalConsultationScenario
from .investigation import InvestigationScenario
from .prosecution_review import ProsecutionReviewScenario
from .defense_opinion_drafting import DefenseOpinionDraftingScenario
from .criminal_trial import CriminalTrialScenario
from .criminal_appeal_trial import CriminalAppealTrialScenario

__all__ = [
    "BaseScenario",
    "LegalConsultationScenario",
    "InvestigationScenario",
    "ProsecutionReviewScenario",
    "DefenseOpinionDraftingScenario",
    "CriminalTrialScenario",
    "CriminalAppealTrialScenario",
]
