"""Criminal Appeal Trial (CRA) scenario — 刑事二审庭审。

继承 CriminalTrialScenario，程序差异：
- 出庭方：审判长、公诉人、上诉人（原审被告人）、辩护人
- 法庭调查围绕上诉理由对一审证据进行复核
- 辩论围绕一审判决事实认定/法律适用/量刑是否不当
- 宣判输出二审刑事判决书（draft_second_instance_criminal_judgment_document）
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..tools.legal import (
    extract_judgment_document_tool_payload,
    get_judgment_document_type_for_scenario,
    render_judgment_document_payload,
)
from .criminal_trial import CriminalTrialScenario

logger = logging.getLogger(__name__)


class CriminalAppealTrialScenario(CriminalTrialScenario):
    """刑事二审庭审场景。"""

    scenario_type = "CRA"

    ROLE_LABEL = {
        "judge": "审判长",
        "clerk": "书记员",
        "prosecutor": "公诉人",
        "defendant": "上诉人",
        "defense_lawyer": "辩护人",
    }

    def __init__(
        self,
        judge_agent: Any,
        prosecutor_agent: Any,
        appellant_agent: Any,
        defense_lawyer_agent: Any | None = None,
        first_verdict_summary: str = "",
        first_court_opinion: str = "",
        **kwargs,
    ):
        super().__init__(
            judge_agent=judge_agent,
            prosecutor_agent=prosecutor_agent,
            defendant_agent=appellant_agent,
            defense_lawyer_agent=defense_lawyer_agent,
            **kwargs,
        )
        self.first_verdict_summary = first_verdict_summary
        self.first_court_opinion = first_court_opinion

    def _execute_court_investigation(self) -> None:
        self.current_stage = "法庭调查"
        has_defense = "defense_lawyer" in self.agents
        target_labels: dict[str, str] = {"公诉人": "prosecutor"}
        if has_defense:
            target_labels["辩护人"] = "defense_lawyer"

        # 上诉人陈述上诉理由（固定起手）
        self._execute_step(
            "上诉人陈述上诉理由",
            "请上诉人陈述上诉理由。",
            speaker_role="defendant",
        )

        results = self._execute_free_stage(
            stage_name="法庭调查",
            opening_instruction=(
                "上诉理由已陈述完毕。现在围绕上诉理由复核一审认定的事实和证据。"
                "你可以自主决定下一轮由公诉人发表意见、辩护人发表意见，或讯问上诉人。"
            ),
            target_labels=target_labels,
            initial_target_role="prosecutor",
            max_rounds=self.max_investigation_rounds,
            end_token="【结束法庭调查】",
            stage_goal="围绕上诉理由、一审证据复核、事实认定是否清楚推进。",
            force_close_instruction="请收束法庭调查，进入法庭辩论。",
        )
        self.stage_results["investigation"] = results

    def _execute_court_debate(self) -> None:
        self.current_stage = "法庭辩论"
        has_defense = "defense_lawyer" in self.agents
        target_labels: dict[str, str] = {"公诉人": "prosecutor"}
        if has_defense:
            target_labels["辩护人"] = "defense_lawyer"

        results = self._execute_free_stage(
            stage_name="庭审辩论",
            opening_instruction=(
                "现在进入法庭辩论。请先由辩护人围绕一审判决是否存在错误发表辩护意见，"
                "再由公诉人发表出庭意见。"
            ),
            target_labels=target_labels,
            initial_target_role="defense_lawyer" if has_defense else "prosecutor",
            max_rounds=self.max_debate_rounds,
            end_token="【结束庭审辩论】",
            stage_goal="围绕一审裁判是否存在错误（事实、法律、量刑）及二审处理方式展开。",
            force_close_instruction="请收束庭审辩论，宣布辩论终结。",
            enable_challenges=True,
        )
        self.stage_results["debate"] = results

    def _execute_final_statement(self) -> None:
        self.current_stage = "最后陈述"
        res = self._execute_step(
            "上诉人最后陈述",
            "现在由上诉人作最后陈述。",
            responder_role="defendant",
            responder_instruction="请以上诉人身份作简短最后陈述。",
        )
        self.stage_results["final_statement"] = [res]

    def _execute_deliberation_and_judgment(self) -> str:
        self.current_stage = "评议宣判"
        judge_agent = self.agents["judge"]

        instr = f"""
二审庭审已经结束，请你全面审查一审判决，结合【参考资料-一审法院意见】与【参考资料-一审法院查明】，撰写二审刑事判决书。

【参考资料-一审法院查明】
{self.court_finding}

【参考资料-一审法院意见】
{self.first_court_opinion or self.court_opinion}

【一审判决主文】
{self.first_verdict_summary}

注意：
1. 二审全面审查，不受上诉理由范围限制；围绕事实是否清楚、证据是否确实充分、法律适用是否正确、量刑是否适当展开。
2. "本院认为"部分须对上诉理由逐项回应，说明采纳与否。
3. 判决结果必须明确：驳回上诉维持原判，或改判，或发回重审。
4. 最终回复必须直接给出完整《刑事判决书》（二审）正文，然后立即调用 `draft_second_instance_criminal_judgment_document` 工具，把同一份正文作为 `document_text` 传入导出。不得输出摘要、PDF路径说明或任何过程性提示。
"""
        res = self._execute_step("撰写二审判决书", instr, responder_role=None)
        self._capture_judgment_tool_result(judge_agent)
        judgment = res["speaker_message"]
        self.stage_results["judgment"] = [res]
        return judgment

    def _capture_judgment_tool_result(self, judge_agent: Any) -> None:
        try:
            payload = extract_judgment_document_tool_payload(
                list(getattr(judge_agent, "_last_tool_call_records", []) or []),
                document_type=get_judgment_document_type_for_scenario("CRA"),
            )
        except Exception:
            return
        if payload.get("pdf_path"):
            self._drafted_document_payload = payload

    def _ensure_pdf_output(self, judge_agent: Any) -> None:
        if self._drafted_document_payload.get("pdf_path") or not str(self.final_judgment or "").strip():
            return
        try:
            self._drafted_document_payload = render_judgment_document_payload(
                judge_agent,
                document_type="CRA",
                document_text=str(self.final_judgment or ""),
            )
        except Exception as exc:
            logger.warning("[CRA] Failed to backfill second-instance criminal judgment PDF: %s", exc)

    def _save_result(self, result: dict[str, Any]) -> None:
        Path(self.output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(self.output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        self._log(f"结果已保存到 {self.output_path}")


__all__ = ["CriminalAppealTrialScenario"]
