"""Investigation Stage (INV) scenario — 侦查阶段。

仿 legal_consultation.py 结构构建。
此阶段模拟辩护律师在侦查阶段的法定活动：
1. 了解涉嫌罪名和案件情况
2. 会见犯罪嫌疑人
3. 申请取保候审
4. 收集有利证据线索

角色：
- lawyer: 辩护律师（玩家或 AI）
- investigator: 公安侦查员（AI 模拟或数据驱动，可选）
- suspect: 犯罪嫌疑人（数据驱动，可替换为 defendant agent）
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_INVESTIGATION_MAX_TURNS = 10


class InvestigationScenario:
    """侦查阶段场景。

    流程:
    1. 律师收到委托 → 前往看守所会见嫌疑人
    2. 向侦查机关了解涉嫌罪名
    3. 与嫌疑人沟通案情
    4. 形成初步辩护思路
    5. 输出: investigation_findings
    """

    END_MARKER = "【侦查阶段结束】"
    OPENING_PROMPT = "请自然开始当前交流。"
    scenario_type = "INV"

    def __init__(
        self,
        lawyer_agent,
        investigator_agent=None,
        suspect_agent=None,
        max_turns: Optional[int] = None,
        output_path: Optional[str] = None,
        verbose: bool = False,
        **kwargs,
    ):
        self.agents: Dict[str, Any] = {
            "lawyer": lawyer_agent,
        }
        if investigator_agent is not None:
            self.agents["investigator"] = investigator_agent
        if suspect_agent is not None:
            self.agents["suspect"] = suspect_agent

        self.max_turns = (
            max_turns if max_turns is not None else DEFAULT_INVESTIGATION_MAX_TURNS
        )
        self.output_path = output_path
        self.verbose = verbose

        # 阶段状态
        self.dialog_history: list[Dict[str, Any]] = []
        self.turn_count = 0
        self.completed = False
        self.finish_reason = "max_turns"

        # 产出
        self.investigation_findings: Dict[str, Any] = {}
        self.charge_suspected: str = ""
        self.bail_application_result: str = ""

        # 额外属性
        self.trace_recorder = kwargs.get("trace_recorder")
        self.trace_stage_code = str(kwargs.get("trace_stage_code", "INV")).strip().upper()
        self.trace_stage_key = str(kwargs.get("trace_stage_key", "INV")).strip().upper()

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[InvestigationScenario] {message}")
        logger.debug(f"[InvestigationScenario] {message}")

    def _add_dialog(self, role: str, content: str) -> None:
        from datetime import datetime
        entry = {
            "turn": self.turn_count,
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        }
        self.dialog_history.append(entry)

    def execute(self) -> Dict[str, Any]:
        """执行侦查阶段场景。"""
        lawyer = self.agents["lawyer"]
        investigator = self.agents.get("investigator")
        suspect = self.agents.get("suspect")

        self._log("开始侦查阶段场景")

        # ── 步骤 1: 律师了解案情 ─────────────────────────────
        lawyer_opening = "请以辩护律师身份开启侦查阶段工作。"
        lawyer_response = getattr(lawyer, "step", lambda x: "")(lawyer_opening)
        self._add_dialog("lawyer", lawyer_response)

        # ── 步骤 2: 与侦查员沟通（如有）───────────────────────
        if investigator is not None:
            investigator_response = getattr(investigator, "step", lambda x: "")(lawyer_response)
            self._add_dialog("investigator", investigator_response)

            reply = getattr(lawyer, "step", lambda x: "")(investigator_response)
            self._add_dialog("lawyer", reply)

        # ── 步骤 3: 会见嫌疑人（如有）──────────────────────────
        if suspect is not None:
            suspect_response = getattr(suspect, "step", lambda x: "")(
                self.OPENING_PROMPT
            )
            self._add_dialog("suspect", suspect_response)

        # ── 步骤 4: 多轮对话循环 ──────────────────────────────
        while self.turn_count < self.max_turns:
            lawyer_msg = getattr(lawyer, "step", lambda x: "")(
                self.dialog_history[-1]["content"] if self.dialog_history else ""
            )
            self._add_dialog("lawyer", lawyer_msg)

            if self.END_MARKER in lawyer_msg:
                self.completed = True
                self.finish_reason = "end_marker"
                break

            if suspect is not None:
                suspect_msg = getattr(suspect, "step", lambda x: "")(lawyer_msg)
                self._add_dialog("suspect", suspect_msg)

            self.turn_count += 1

        if not self.completed:
            self.completed = True
            self.finish_reason = "turn_limit_reached"

        # ── 收集产出 ──────────────────────────────────────────
        self.investigation_findings = {
            "charge_suspected": self.charge_suspected,
            "bail_application_result": self.bail_application_result,
        }

        result = self._build_result()
        if self.output_path:
            self._save_result(result)
        return result

    def _build_result(self) -> Dict[str, Any]:
        return {
            "scenario_type": self.scenario_type,
            "dialog_history": self.dialog_history,
            "turn_count": self.turn_count,
            "completed": self.completed,
            "finish_reason": self.finish_reason,
            "investigation_findings": self.investigation_findings,
        }

    def _save_result(self, result: Dict[str, Any]) -> None:
        if not self.output_path:
            return
        Path(self.output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(self.output_path, "w", encoding="utf-8") as file:
            json.dump(result, file, ensure_ascii=False, indent=2)
        self._log(f"结果已保存到 {self.output_path}")

    def _build_checkpoint_data(self) -> Dict[str, Any]:
        return {
            "scenario_type": self.scenario_type,
            "dialog_history": self.dialog_history,
            "turn_count": self.turn_count,
            "completed": self.completed,
            "investigation_findings": self.investigation_findings,
            "finish_reason": self.finish_reason,
        }

    async def resume_from_checkpoint(self, checkpoint_data: Dict[str, Any]) -> Dict[str, Any]:
        self.dialog_history = checkpoint_data.get("dialog_history", [])
        self.turn_count = checkpoint_data.get("turn_count", 0)
        self.completed = checkpoint_data.get("completed", False)
        self.investigation_findings = checkpoint_data.get("investigation_findings", {})
        self.finish_reason = checkpoint_data.get("finish_reason", self.finish_reason)

        if self.completed:
            return self._build_result()

        return self.execute()


__all__ = [
    "InvestigationScenario",
]
