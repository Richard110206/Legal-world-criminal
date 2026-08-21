"""刑法适配 — Player-as-defense-lawyer 模式（纯刑事）。

在刑事公诉案件中，玩家扮演被告人的辩护律师（defendant 模式）。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Feature flag ──────────────────────────────────────────────────
_PLAYER_MODE_ENV = "SIMLAW_PLAYER_LAWYER_MODE"


def resolve_player_party_role() -> Optional[str]:
    """返回玩家扮演的角色: 'defendant' | None（不启用）。

    纯刑事：只支持玩家扮演辩护律师（defendant）。
    """
    value = os.environ.get(_PLAYER_MODE_ENV, "").strip().lower()
    if value in {"defendant", "defense", "defense_lawyer"}:
        return "defendant"
    return None


def is_player_defendant_mode() -> bool:
    """判断是否启用玩家扮演辩护律师模式。"""
    return resolve_player_party_role() == "defendant"


def is_player_mode_enabled() -> bool:
    """判断是否启用任何玩家律师模式。"""
    return resolve_player_party_role() is not None


# ── No-op chat_agent ──────────────────────────────────────────────
class _NoOpChatAgent:
    """Minimal stub — courtroom broadcast 兼容。"""

    def update_memory(self, msg: Any, role: Any) -> None:
        pass


# ── Agent adapter（玩家扮演辩护律师）──────────────────────────────
class PlayerLawyerAgent:
    """Drop-in replacement for LawyerAgent — 玩家扮演刑事辩护律师。

    1. 构造函数固定 party_role="defendant"
    2. create_request() 中 role 固定为 "defendant_lawyer"
    3. get_prompt_info() 中的 agent_class 为 "PlayerLawyerAgent"
    """

    def __init__(
        self,
        *,
        agent_id: str,
        name: str,
        party_role: str = "defendant",               # 固定辩护律师角色
        law_firm: str = "",
        firm_id: str = "",
        gateway: Any,                                 # PlayerInputGateway
        case_id: str = "",
        sandbox_id: int = 0,
        broadcast_fn: Optional[Callable[..., Any]] = None,
    ) -> None:
        # Identity
        self.agent_id = agent_id
        self.name = name
        self._party_role = "defendant"               # 固定为辩护律师
        self.law_firm = law_firm
        self.firm_id = firm_id
        self.config_path: Optional[str] = None
        self.storage: Any = None

        # Scenario metadata
        self.scenario_type: Optional[str] = None
        self.scenario_data: Dict[str, Any] = {}
        self.tools: List[Any] = []
        self._last_tool_call_records: List[Any] = []
        self.current_scenario_id: Optional[str] = None
        self.system_prompt: str = ""
        self.skill_usage_log: List[Dict[str, Any]] = []

        # Player gateway
        self._gateway = gateway
        self._case_id = case_id
        self._sandbox_id = sandbox_id
        self._broadcast_fn = broadcast_fn

        # Active flag
        self._is_active = False
        self._current_stage: str = ""

        # No-op chat_agent adapter
        self.chat_agent = _NoOpChatAgent()

    # ── Agent protocol surface ────────────────────────────────────
    @property
    def is_active(self) -> bool:
        return self._is_active

    @property
    def agent_type(self) -> str:
        return "lawyer"

    def activate(
        self,
        system_prompt: str = "",
        model_platform: Any = None,
        model_type: Any = None,
        *,
        tools: Any = None,
        skill_dirs: Any = None,
        debug_output_dir: Any = None,
        scenario_id: Optional[str] = None,
        step_timeout_seconds: Any = None,
    ) -> None:
        self.system_prompt = system_prompt
        if scenario_id:
            self.current_scenario_id = scenario_id
        self._is_active = True
        logger.info("[PlayerAdapter %s] Activated (party_role=%s)", self.name, self._party_role)

    def deactivate(self) -> None:
        self._is_active = False
        self.current_scenario_id = None
        self.system_prompt = ""
        logger.info("[PlayerAdapter %s] Deactivated", self.name)

    def recover_from_error(self) -> None:
        logger.info("[PlayerAdapter %s] recover_from_error (no-op)", self.name)

    def reset_memory(self) -> None:
        pass

    def get_prompt_info(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_name": self.name,
            "agent_class": "PlayerLawyerAgent",
            "system_prompt": self.system_prompt,
            "party_role": self._party_role,
        }

    def get_skill_usage_report(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_name": self.name,
            "tool_call_count": 0,
            "skill_load_count": 0,
            "skills": [],
            "tool_calls": [],
        }

    def reset_skill_usage_report(self) -> None:
        self.skill_usage_log = []

    # ── Long-term memory（no-ops for player）─────────────────────
    def extract_and_save_long_term_memory(
        self,
        filepath: Optional[str] = None,
        raise_on_error: bool = False,
    ) -> Optional[Dict[str, Any]]:
        return None

    # ── Core: step() blocks on player input ───────────────────────
    def step(
        self,
        instruction: str,
        response_format: Any = None,
        image_list: Any = None,
        context: Any = None,
    ) -> str:
        stage = self._current_stage or self.scenario_type or ""

        # ── 角色标签固定为辩护律师 ──
        role_label = "defendant_lawyer"

        req = self._gateway.find_reusable_request(
            case_id=self._case_id,
            stage=stage,
            prompt=instruction,
        )
        if req is not None and req.status.value == "submitted":
            logger.info(
                "[PlayerAdapter %s] Reusing submitted player input (request=%s)",
                self.name,
                req.request_id,
            )
            return req.message

        if req is None:
            req = self._gateway.create_request(
                case_id=self._case_id,
                stage=stage,
                role=role_label,                        # 固定辩护律师角色
                speaker_label=self.name,
                prompt=instruction,
                context_summary=f"案件 {self._case_id} · {stage} 阶段",
            )
        else:
            logger.info(
                "[PlayerAdapter %s] Reusing pending player input (request=%s)",
                self.name,
                req.request_id,
            )

        # Notify frontends
        if self._broadcast_fn is not None:
            try:
                self._broadcast_fn(
                    "player_lawyer_input_required",
                    req.to_dict(),
                )
            except Exception as exc:
                logger.warning(
                    "[PlayerAdapter %s] broadcast failed: %s",
                    self.name,
                    exc,
                )

        logger.info(
            "[PlayerAdapter %s] Waiting for player input (request=%s)",
            self.name,
            req.request_id,
        )
        message = self._gateway.wait_for_response(req.request_id)
        logger.info(
            "[PlayerAdapter %s] Got player input (%d chars)",
            self.name,
            len(message),
        )
        return message

    def build_auto_opening_response(self, instruction: str = "") -> str:
        return f"您好，我是{self.name}，请您先说一下吧。"

    # ── Convenience setters ───────────────────────────────────────
    def set_stage(self, stage: str) -> None:
        self._current_stage = stage
        self.scenario_data["case_id"] = self._case_id
        self.scenario_data["current_handling_case"] = self._case_id

    @property
    def case_id(self) -> str:
        return self._case_id

    @property
    def current_handling_case(self) -> str:
        return self._case_id

    def expects_player_input_for_current_step(self) -> bool:
        return True

    def set_case_id(self, case_id: str) -> None:
        self._case_id = case_id
        self.scenario_data["case_id"] = self._case_id
        self.scenario_data["current_handling_case"] = self._case_id


# ── 向后兼容别名 ─────────────────────────────────────────────────
PlayerPlaintiffLawyerAgent = PlayerLawyerAgent  # 旧代码引用兼容


__all__ = [
    "PlayerLawyerAgent",
    "PlayerPlaintiffLawyerAgent",  # 向后兼容
    "is_player_defendant_mode",
    "is_player_mode_enabled",
    "resolve_player_party_role",
]

