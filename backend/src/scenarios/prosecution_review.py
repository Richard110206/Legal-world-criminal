"""Prosecution Review (PR) scenario — 审查起诉阶段。

仿 defense_drafting.py 结构构建。
此阶段模拟辩护律师在检察院审查起诉期间的法定活动：
1. 查阅、摘抄、复制案卷材料（阅卷）
2. 会见被告人，核实证据
3. 向检察院提交辩护意见
4. 提出不起诉或变更强制措施的申请

角色：
- lawyer: 辩护律师
- prosecutor: 检察官（AI）
- defendant: 被告人
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_PROSECUTION_REVIEW_MAX_TURNS = 12


class ProsecutionReviewScenario:
    """审查起诉阶段场景。

    流程:
    1. 律师阅卷，了解全案证据
    2. 会见被告人，核对证据、沟通辩护策略
    3. 向检察官提交书面辩护意见
    4. 申请变更强制措施 / 不起诉
    5. 检察官决定是否提起公诉
    6. 输出: review_findings, defense_strategy
    """

    END_MARKER = "【审查起诉阶段结束】"
    OPENING_PROMPT = "请自然开始当前交流。"
    scenario_type = "PR"

    def __init__(
        self,
        lawyer_agent,
        prosecutor_agent=None,
        defendant_agent=None,
        max_turns: Optional[int] = None,
        output_path: Optional[str] = None,
        verbose: bool = False,
        **kwargs,
    ):
        self.agents: Dict[str, Any] = {
            "lawyer": lawyer_agent,
        }
        if prosecutor_agent is not None:
            self.agents["prosecutor"] = prosecutor_agent
        if defendant_agent is not None:
            self.agents["defendant"] = defendant_agent

        self.max_turns = (
            max_turns if max_turns is not None else DEFAULT_PROSECUTION_REVIEW_MAX_TURNS
        )
        self.output_path = output_path
        self.verbose = verbose

        # 阶段状态
        self.dialog_history: list[Dict[str, Any]] = []
        self.turn_count = 0
        self.completed = False
        self.finish_reason = "max_turns"

        # 产出
        self.review_findings: Dict[str, Any] = {}
        self.defense_strategy: str = ""
        self.indictment_received: bool = False

        # 额外属性
        self.trace_recorder = kwargs.get("trace_recorder")
        self.trace_stage_code = str(kwargs.get("trace_stage_code", "PR")).strip().upper()
        self.trace_stage_key = str(kwargs.get("trace_stage_key", "PR")).strip().upper()

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[ProsecutionReviewScenario] {message}")
        logger.debug(f"[ProsecutionReviewScenario] {message}")

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
        """执行审查起诉阶段场景。"""
        lawyer = self.agents["lawyer"]
        prosecutor = self.agents.get("prosecutor")
        defendant = self.agents.get("defendant")

        self._log("开始审查起诉阶段场景")

        # ── 步骤 1: 律师阅卷并会见被告人 ─────────────────────
        lawyer_opening = "请以辩护律师身份，开始审查起诉阶段的工作。你已经阅卷完毕，现在会见被告人。"
        lawyer_response = getattr(lawyer, "step", lambda x: "")(lawyer_opening)
        self._add_dialog("lawyer", lawyer_response)

        # ── 步骤 2: 被告人回应 ───────────────────────────────
        if defendant is not None:
            defendant_response = getattr(defendant, "step", lambda x: "")(lawyer_response)
            self._add_dialog("defendant", defendant_response)

        # ── 步骤 3: 律师向检察官提交辩护意见 ──────────────────
        if prosecutor is not None:
            submission = "辩护律师向检察官提交了书面辩护意见。"
            prosecutor_response = getattr(prosecutor, "step", lambda x: "")(submission)
            self._add_dialog("prosecutor", prosecutor_response)

            reply = getattr(lawyer, "step", lambda x: "")(prosecutor_response)
            self._add_dialog("lawyer", reply)

        # ── 步骤 4: 多轮对话循环 ─────────────────────────────
        while self.turn_count < self.max_turns:
            if prosecutor is not None and self.turn_count % 2 == 0:
                msg = getattr(prosecutor, "step", lambda x: "")(
                    self.dialog_history[-1]["content"] if self.dialog_history else ""
                )
                self._add_dialog("prosecutor", msg)
            else:
                msg = getattr(lawyer, "step", lambda x: "")(
                    self.dialog_history[-1]["content"] if self.dialog_history else ""
                )
                self._add_dialog("lawyer", msg)

            if self.END_MARKER in msg:
                self.completed = True
                self.finish_reason = "end_marker"
                break

            self.turn_count += 1

        if not self.completed:
            self.completed = True
            self.finish_reason = "turn_limit_reached"

        # ── 收集产出 ──────────────────────────────────────────
        self.defense_strategy = "辩护策略：基于阅卷和会见形成的完整辩护思路。"
        self.indictment_received = True

        self.review_findings = {
            "defense_strategy": self.defense_strategy,
            "indictment_received": self.indictment_received,
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
            "review_findings": self.review_findings,
            "defense_strategy": self.defense_strategy,
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
            "review_findings": self.review_findings,
            "defense_strategy": self.defense_strategy,
            "finish_reason": self.finish_reason,
        }

    async def resume_from_checkpoint(self, checkpoint_data: Dict[str, Any]) -> Dict[str, Any]:
        self.dialog_history = checkpoint_data.get("dialog_history", [])
        self.turn_count = checkpoint_data.get("turn_count", 0)
        self.completed = checkpoint_data.get("completed", False)
        self.review_findings = checkpoint_data.get("review_findings", {})
        self.defense_strategy = checkpoint_data.get("defense_strategy", "")
        self.finish_reason = checkpoint_data.get("finish_reason", self.finish_reason)

        if self.completed:
            return self._build_result()

        return self.execute()


__all__ = [
    "ProsecutionReviewScenario",
]
