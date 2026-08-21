"""Defense Opinion Drafting (DS) scenario — 刑事辩护词起草。

复用 DefenseDraftingScenario 的对话与文书产出机制，仅切换：
- scenario_type = DS → 工具解析为 draft_defense_opinion_document
- 文书标题 = 辩护词
- END_MARKER = 【起草结束】
"""

from __future__ import annotations

from .defense_drafting import DefenseDraftingScenario


class DefenseOpinionDraftingScenario(DefenseDraftingScenario):
    """刑事辩护词起草场景（DS）。"""

    scenario_type = "DS"
    DOCUMENT_TITLE = "辩护词"
    END_MARKER = "【起草结束】"


__all__ = ["DefenseOpinionDraftingScenario"]
