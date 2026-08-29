"""Criminal Trial (CR) scenario — 刑事一审庭审。

刑事诉讼一审普通程序：
1. 开庭审理（宣布开庭、核实被告人身份、告知诉讼权利、询问回避）
2. 法庭调查（公诉人宣读起诉书、被告人陈述、讯问被告人、举证质证）
3. 法庭辩论（公诉词 vs 辩护词，审判长动态主持）
4. 被告人最后陈述
5. 评议与宣判（刑事判决书，含量刑评价）

角色：
- judge: 审判长
- prosecutor: 公诉人
- defendant: 被告人
- defense_lawyer: 辩护人（可与 lawyer 同一 agent）
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..tools.legal import (
    extract_judgment_document_tool_payload,
    get_judgment_document_type_for_scenario,
    render_judgment_document_payload,
)
from .base_scenario import BaseScenario

logger = logging.getLogger(__name__)

JUDGMENT_SKELETON = """
[刑事判决书模板]
刑事判决书

（案号：待补充）

公诉机关：XX市人民检察院。
被告人：{姓名}，{身份信息}。

XX市人民检察院以X检X诉〔X〕X号起诉书指控被告人XX犯XX罪，向本院提起公诉。本院依法组成合议庭，公开（或不公开）开庭审理了本案。XX市人民检察院指派检察员XX出庭支持公诉，被告人XX及其辩护人到庭参加诉讼。现已审理终结。

XX市人民检察院指控：{指控事实}。

被告人XX对指控{供述情况}。辩护人提出{辩护要点}。

经审理查明：{查明事实}。

上述事实有经庭审举证、质证并确认的证据在案证实。

本院认为，被告人XX的行为已构成{罪名}。{量刑情节评价}。依照《中华人民共和国刑法》第X条之规定，判决如下：

被告人XX犯{罪名}，判处{主刑}（{附加刑/缓刑}）。

如不服本判决，可在接到判决书的第二日起十日内，通过本院或者直接向XX中级人民法院提出上诉。

审判长：XX

X年X月X日
"""


class CriminalTrialScenario(BaseScenario):
    """刑事一审庭审场景（普通程序）。"""

    scenario_type = "CR"

    ROLE_LABEL = {
        "judge": "审判长",
        "clerk": "书记员",
        "prosecutor": "公诉人",
        "defendant": "被告人",
        "defense_lawyer": "辩护人",
    }

    PROCEDURAL_JUDGE_TEMPLATES = {
        "书记员核对到庭情况": "请公诉人、被告人及辩护人核对到庭情况。",
        "书记员宣布法庭纪律": "现在宣布法庭纪律。请所有在庭人员遵守法庭秩序，未经许可不得录音录像、摄影，不得随意走动、喧哗。",
        "核实被告人身份": "请被告人本人陈述姓名、出生日期、民族、住址等身份信息，并确认本人到庭。",
        "告知诉讼权利义务": "告知被告人依法享有申请回避、自行辩护、最后陈述等诉讼权利。",
        "询问回避申请": "被告人是否申请回避？",
        "讯问被告人是否收到起诉书副本": "被告人是否收到起诉书副本？距开庭是否超过十日？",
        "被告人最后陈述": "辩论终结，现在由被告人作最后陈述。",
        "宣布休庭评议": "现在休庭，本庭将进行评议后宣判。",
    }

    def __init__(
        self,
        judge_agent: Any,
        prosecutor_agent: Any,
        defendant_agent: Any,
        defense_lawyer_agent: Any | None = None,
        max_debate_rounds: int = 4,
        max_investigation_rounds: int = 5,
        verbose: bool = False,
        court_finding: str = "",
        court_opinion: str = "",
        output_path: str | None = None,
        case_charge: str = "",
        case_source_title: str = "",
        max_prosecutor_challenges: int = 2,
        **kwargs,
    ):
        agents: dict[str, Any] = {
            "judge": judge_agent,
            "prosecutor": prosecutor_agent,
            "defendant": defendant_agent,
        }
        if defense_lawyer_agent is not None:
            agents["defense_lawyer"] = defense_lawyer_agent
        super().__init__(agents=agents, verbose=verbose, **kwargs)

        self.max_debate_rounds = max_debate_rounds
        self.max_investigation_rounds = max_investigation_rounds
        self.court_finding = court_finding
        self.court_opinion = court_opinion
        self.output_path = output_path
        self.case_charge = case_charge
        self.case_source_title = case_source_title
        self.max_prosecutor_challenges = max(0, int(max_prosecutor_challenges))
        self._prosecutor_challenges_used = 0
        self.current_stage = "未开始"
        self.stage_results: dict[str, Any] = {}
        self.final_judgment: str | None = None
        self._drafted_document_payload: dict[str, str] = {}

    # ── 基础设施（与 CI 相同的广播机制）────────────────────
    def _broadcast_message(
        self,
        sender_role: str,
        message: str,
        exclude_roles: list[str] | None = None,
    ) -> None:
        from camel.messages import BaseMessage
        from camel.types import OpenAIBackendRole, RoleType

        sender_label = self.ROLE_LABEL.get(sender_role, sender_role)
        broadcast_content = f"{sender_label}说：{message}"

        skip_roles = {sender_role}
        if exclude_roles:
            skip_roles.update(exclude_roles)

        for role_key, agent in self.agents.items():
            if role_key in skip_roles:
                continue
            try:
                msg = BaseMessage(
                    role_name="User",
                    role_type=RoleType.USER,
                    meta_dict=None,
                    content=broadcast_content,
                )
                agent.chat_agent.update_memory(msg, OpenAIBackendRole.USER)
            except Exception as exc:
                logger.warning(
                    "[CR] 广播失败 %s -> %s: %s", sender_role, role_key, exc
                )

    def _execute_fixed_speech(
        self,
        step_name: str,
        speaker_role: str,
        message: str,
    ) -> dict[str, Any]:
        self.turn_count += 1
        self._log(f"[{self.current_stage}] {step_name}")
        self._add_dialog(speaker_role, message)
        self._broadcast_message(sender_role=speaker_role, message=message)
        return {"step": step_name, "speaker_message": message, "responder_message": None}

    def _execute_step(
        self,
        step_name: str,
        instruction: str,
        speaker_role: str = "judge",
        responder_role: str | None = None,
        responder_instruction: str | None = None,
    ) -> dict[str, Any]:
        self.turn_count += 1
        self._log(f"[{self.current_stage}] {step_name}")

        speaker = self.agents[speaker_role]
        if speaker_role == "judge" and step_name in self.PROCEDURAL_JUDGE_TEMPLATES:
            speaker_msg = self.PROCEDURAL_JUDGE_TEMPLATES[step_name]
        else:
            prompt = f"[{self.current_stage}] {step_name}: {instruction}"
            self._check_pause_sync()
            speaker_msg = speaker.step(prompt)
        self._add_dialog(speaker_role, speaker_msg)

        exclude = [responder_role] if responder_role else None
        self._broadcast_message(sender_role=speaker_role, message=speaker_msg, exclude_roles=exclude)

        responder_msg = None
        if responder_role:
            responder = self.agents[responder_role]
            role_label = self.ROLE_LABEL.get(responder_role, responder_role)
            responder_prompt = (
                f"{speaker_msg}\n\n"
                f"[流程控制要求]\n你现在是{role_label}，本轮指定发言人就是你。"
                "只输出你自己的本轮发言，不要转述他人发言，不要冒充其他角色。"
            )
            if responder_instruction:
                responder_prompt += f"\n{responder_instruction}"
            self._check_pause_sync()
            responder_msg = responder.step(responder_prompt)
            self._add_dialog(responder_role, responder_msg)
            self._broadcast_message(sender_role=responder_role, message=responder_msg)

        return {
            "step": step_name,
            "speaker_message": speaker_msg,
            "responder_message": responder_msg,
        }

    def _parse_judge_stage_control(
        self,
        message: str,
        target_labels: dict[str, str],
        end_token: str,
    ) -> dict[str, str | None]:
        text = str(message or "").strip()
        if not text:
            return {"target_role": None, "end_stage": False}
        if end_token in text or end_token.strip("【】") in text:
            return {"target_role": None, "end_stage": True}
        for label, role in target_labels.items():
            if f"【对{label}说】" in text or re.search(rf"对{re.escape(label)}说[:：]?", text):
                return {"target_role": role, "end_stage": False}
        return {"target_role": None, "end_stage": False}

    def _execute_free_stage(
        self,
        *,
        stage_name: str,
        opening_instruction: str,
        target_labels: dict[str, str],
        initial_target_role: str,
        max_rounds: int,
        end_token: str,
        stage_goal: str,
        force_close_instruction: str,
        enable_challenges: bool = False,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        last_message = opening_instruction
        last_target_role: str | None = None
        allowed_labels = list(target_labels.keys())
        allowed_text = "、".join(allowed_labels)

        def _challenge_budget_left() -> bool:
            return self._prosecutor_challenges_used < self.max_prosecutor_challenges

        for round_index in range(1, max_rounds + 1):
            self.turn_count += 1
            step_name = f"{stage_name}-动态主持-{round_index}"
            self._log(f"[{self.current_stage}] {step_name}")

            judge_prompt = (
                f"[当前阶段] {stage_name}\n"
                f"[当前轮次] 第{round_index}轮 / 最多{max_rounds}轮\n"
                f"[本阶段任务] {stage_goal}\n"
                f"[参与人范围] 仅限：审判长、{allowed_text}\n"
                f"[控制格式]\n"
                f"1. 如果要点名某一方发言，必须以"
                + "、".join(f"【对{label}说】" for label in allowed_labels)
                + "之一开头。\n"
                f"2. 认为本阶段可以结束时，必须以{end_token}开头。\n"
                "3. 每轮只做一件事：点名一方发言，或宣布结束本阶段。\n"
                "4. 保持审判长身份和法庭用语。\n\n"
                f"[阶段起始要求]\n{opening_instruction}\n\n"
                f"[最近一轮法庭发言]\n{str(last_message or '')[-1200:] or '（本阶段刚开始）'}"
            )
            # 对抗质询：辩护人刚发言且追问预算未耗尽 → 引导法官优先让公诉人质询
            if (
                enable_challenges
                and last_target_role == "defense_lawyer"
                and "prosecutor" in self.agents
                and "defense_lawyer" in self.agents
                and _challenge_budget_left()
            ):
                judge_prompt += (
                    "\n\n[庭审提示] 辩护人刚作出发言。如其主张存在值得追问的漏洞"
                    "（事实、证据、法律适用或量刑情节），优先以【对公诉人说】开头，"
                    "要求公诉人针对性质询。"
                )
            elif (
                enable_challenges
                and last_target_role == "defense_lawyer"
                and "prosecutor" in self.agents
                and not _challenge_budget_left()
            ):
                judge_prompt += (
                    "\n\n[庭审提示] 公诉人质询机会已用完，请推进辩论或收束本阶段。"
                )
            self._check_pause_sync()
            judge_msg = self.agents["judge"].step(judge_prompt)
            parsed = self._parse_judge_stage_control(judge_msg, target_labels=target_labels, end_token=end_token)
            self._add_dialog("judge", judge_msg)

            responder_role = parsed["target_role"] or (
                initial_target_role if last_target_role is None else last_target_role
            )
            challenge_active = bool(
                enable_challenges
                and responder_role == "prosecutor"
                and last_target_role == "defense_lawyer"
                and _challenge_budget_left()
            )
            # 预算耗尽后法官仍点名公诉人质询 → 确定性改路由给辩护人继续辩论
            challenge_overridden = False
            if (
                enable_challenges
                and responder_role == "prosecutor"
                and last_target_role == "defense_lawyer"
                and not _challenge_budget_left()
                and "defense_lawyer" in self.agents
            ):
                responder_role = "defense_lawyer"
                challenge_overridden = True

            exclude = [responder_role] if responder_role and not parsed["end_stage"] else None
            self._broadcast_message(sender_role="judge", message=judge_msg, exclude_roles=exclude)

            step_result: dict[str, Any] = {
                "step": step_name,
                "speaker_message": judge_msg,
                "responder_message": None,
                "target_role": responder_role,
                "end_stage": bool(parsed["end_stage"]),
            }
            if challenge_overridden:
                step_result["challenge_override"] = True
            results.append(step_result)
            if parsed["end_stage"]:
                break

            role_label = self.ROLE_LABEL.get(str(responder_role), str(responder_role))
            responder_prompt = (
                f"{judge_msg}\n\n"
                "[流程控制要求]\n"
                f"当前阶段：{stage_name}\n"
                f"你现在是{role_label}，本轮回应审判长点名。"
                "只直接回应审判长本轮要求，不要宣布流程，不要冒充审判长。"
            )
            challenge_audit: dict[str, Any] | None = None
            if challenge_active:
                from .adversarial_grounding import build_challenge_block

                prompt_block, challenge_audit = build_challenge_block(
                    defense_statement=str(last_message or ""),
                    charge=self.case_charge,
                    exclude_title=self.case_source_title,
                )
                responder_prompt += f"\n\n{prompt_block}"
                self._prosecutor_challenges_used += 1
                step_result["adversarial_challenge"] = {
                    **challenge_audit,
                    "challenge_index": self._prosecutor_challenges_used,
                }
            self._check_pause_sync()
            responder_msg = self.agents[str(responder_role)].step(responder_prompt)
            self._add_dialog(str(responder_role), responder_msg)
            step_result["responder_message"] = responder_msg
            self._broadcast_message(sender_role=str(responder_role), message=responder_msg)
            last_message = responder_msg
            last_target_role = str(responder_role)
        else:
            self.turn_count += 1
            self._log(f"[{self.current_stage}] {stage_name}-超限收束")
            force_prompt = (
                f"[当前阶段] {stage_name}\n"
                f"[控制要求] 已达最大轮数 {max_rounds}。{force_close_instruction}\n"
                f"请以审判长身份发言，必须以{end_token}开头。"
            )
            self._check_pause_sync()
            judge_msg = self.agents["judge"].step(force_prompt)
            self._add_dialog("judge", judge_msg)
            self._broadcast_message(sender_role="judge", message=judge_msg)
            results.append({
                "step": f"{stage_name}-超限收束",
                "speaker_message": judge_msg,
                "responder_message": None,
                "target_role": None,
                "end_stage": True,
            })

        return results

    # ── 庭审阶段 ──────────────────────────────────────────
    def _execute_opening_session(self) -> None:
        self.current_stage = "开庭审理"
        results = []
        results.append(self._execute_fixed_speech("书记员核对到庭情况", "clerk", self.PROCEDURAL_JUDGE_TEMPLATES["书记员核对到庭情况"]))
        results.append(self._execute_fixed_speech("书记员宣布法庭纪律", "clerk", self.PROCEDURAL_JUDGE_TEMPLATES["书记员宣布法庭纪律"]))
        steps = [
            ("核实被告人身份", "请被告人陈述身份信息。", "defendant"),
            ("告知诉讼权利义务", "依法告知诉讼权利义务。", None),
            ("询问回避申请", "询问是否申请回避。", "defendant"),
            ("讯问被告人是否收到起诉书副本", "询问收到起诉书副本的时间。", "defendant"),
        ]
        for name, instr, responder in steps:
            results.append(self._execute_step(name, instr, responder_role=responder))
        self.stage_results["opening"] = results

    def _execute_court_investigation(self) -> None:
        self.current_stage = "法庭调查"
        has_defense = "defense_lawyer" in self.agents
        target_labels: dict[str, str] = {"公诉人": "prosecutor"}
        if has_defense:
            target_labels["辩护人"] = "defense_lawyer"

        # 公诉人宣读起诉书（固定起手）
        self._execute_step(
            "公诉人宣读起诉书",
            "请公诉人当庭宣读起诉书，指控被告人犯罪事实。",
            speaker_role="prosecutor",
        )

        results = self._execute_free_stage(
            stage_name="法庭调查",
            opening_instruction=(
                "起诉书已宣读完毕。现在对被告人进行讯问，并围绕指控证据进行举证质证。"
                "你可以自主决定下一轮由公诉人举证、辩护人质证，或对被告人进行讯问。"
            ),
            target_labels=target_labels,
            initial_target_role="prosecutor",
            max_rounds=self.max_investigation_rounds,
            end_token="【结束法庭调查】",
            stage_goal="围绕讯问被告人、举证、质证和回应推进。",
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
                "现在进入法庭辩论。请先由公诉人发表公诉词。"
                "此后围绕指控罪名是否成立、量刑情节进行辩论。"
            ),
            target_labels=target_labels,
            initial_target_role="prosecutor",
            max_rounds=self.max_debate_rounds,
            end_token="【结束庭审辩论】",
            stage_goal="围绕罪名成立、证据采信、量刑情节和法律适用展开辩论。",
            force_close_instruction="请收束庭审辩论，宣布辩论终结。",
            enable_challenges=True,
        )
        self.stage_results["debate"] = results

    def _execute_final_statement(self) -> None:
        self.current_stage = "最后陈述"
        res = self._execute_step(
            "被告人最后陈述",
            "现在由被告人作最后陈述。",
            responder_role="defendant",
            responder_instruction="请以被告人身份作简短最后陈述（认罪悔罪、请求从轻处理或其他意见）。",
        )
        self.stage_results["final_statement"] = [res]

    def _execute_deliberation_and_judgment(self) -> str:
        self.current_stage = "评议宣判"
        judge_agent = self.agents["judge"]

        instr = f"""
庭审已经结束，请你根据庭审情况，结合【参考资料-审理查明】与【参考资料-法院意见】，撰写一审刑事判决书。

【参考资料-审理查明】
{self.court_finding}

【参考资料-法院意见】
{self.court_opinion}

请参照以下骨架保持判决书格式：

{JUDGMENT_SKELETON}

注意：
1. 事实认定基于庭审举证质证，并重点参考【参考资料-审理查明】。
2. "本院认为"部分须评价已查明的量刑情节（自首/坦白/认罪认罚/赔偿谅解/累犯等），参考【参考资料-法院意见】但用自己的语言。
3. 判决结果（刑期、罚金、缓刑）必须明确具体。
4. 最终回复必须直接给出完整《刑事判决书》正文，然后立即调用 `draft_first_instance_criminal_judgment_document` 工具，把同一份正文作为 `document_text` 传入导出。不得输出摘要、PDF路径说明或任何过程性提示。
"""
        res = self._execute_step("撰写判决书", instr, responder_role=None)
        self._capture_judgment_tool_result(judge_agent)
        judgment = res["speaker_message"]
        self.stage_results["judgment"] = [res]
        return judgment

    def _capture_judgment_tool_result(self, judge_agent: Any) -> None:
        try:
            payload = extract_judgment_document_tool_payload(
                list(getattr(judge_agent, "_last_tool_call_records", []) or []),
                document_type=get_judgment_document_type_for_scenario("CR"),
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
                document_type="CR",
                document_text=str(self.final_judgment or ""),
            )
        except Exception as exc:
            logger.warning("[CR] Failed to backfill criminal judgment PDF: %s", exc)

    # ── 主流程 ────────────────────────────────────────────
    def execute(self) -> dict[str, Any]:
        self._log("开始执行刑事一审庭审场景")
        start_time = datetime.now()

        self._execute_opening_session()
        self._execute_court_investigation()
        self._execute_court_debate()
        self._execute_final_statement()

        self._execute_fixed_speech(
            "宣布休庭评议", "judge", self.PROCEDURAL_JUDGE_TEMPLATES["宣布休庭评议"]
        )
        self.final_judgment = self._execute_deliberation_and_judgment()
        self.completed = True
        self._ensure_pdf_output(self.agents["judge"])

        result = self._build_result((datetime.now() - start_time).total_seconds())
        if self.output_path:
            self._save_result(result)
        return result

    def _build_result(self, duration: float = 0.0) -> dict[str, Any]:
        return {
            "scenario_type": self.scenario_type,
            "dialog_history": self.dialog_history,
            "stage_results": self.stage_results,
            "final_judgment": self.final_judgment,
            "drafted_document_payload": self._drafted_document_payload,
            "pdf_path": str(self._drafted_document_payload.get("pdf_path", "") or ""),
            "total_turns": self.turn_count,
            "duration": duration,
            "completed": self.completed,
        }

    def _build_checkpoint_data(self) -> dict[str, Any]:
        return {
            "dialog_history": self.dialog_history,
            "turn_count": self.turn_count,
            "completed": self.completed,
            "current_stage": self.current_stage,
            "stage_results": self.stage_results,
            "final_judgment": self.final_judgment,
            "drafted_document_payload": self._drafted_document_payload,
        }

    async def resume_from_checkpoint(self, checkpoint_data: dict[str, Any]) -> dict[str, Any]:
        self.dialog_history = checkpoint_data.get("dialog_history", [])
        self.turn_count = checkpoint_data.get("turn_count", 0)
        self.completed = checkpoint_data.get("completed", False)
        self.current_stage = checkpoint_data.get("current_stage", self.current_stage)
        self.stage_results = checkpoint_data.get("stage_results", {})
        self.final_judgment = checkpoint_data.get("final_judgment")
        self._drafted_document_payload = checkpoint_data.get("drafted_document_payload", {}) or {}
        if self.completed:
            return self._build_result()
        return self.execute()

    def _save_result(self, result: dict[str, Any]) -> None:
        Path(self.output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(self.output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        self._log(f"结果已保存到 {self.output_path}")


__all__ = ["CriminalTrialScenario"]
