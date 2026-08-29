"""案件状态机 (CaseStateMachine) — 管控刑事公诉案件流转合法性。

维护状态迁移图，校验每次状态变更的合法性，
并通过 FileStorageManager 将状态实时落盘到当事人的 config.yaml。

纯刑事流程：委托洽谈 → 侦查 → 审查起诉 → 辩护词 → 一审 → 上诉 → 二审 → 终审。
"""

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..core.event_bus import EventBus, EventType
from ..core.file_storage_manager import FileStorageManager
from ..utils.case_progress import infer_case_state_from_artifacts

logger = logging.getLogger(__name__)


class CaseState:
    """案件状态常量 — 刑事业务流程节点，非物理移动。"""

    IDLE = "空闲"

    # ── 委托洽谈（刑事案件入口）──
    WAITING_FOR_RECEPTION = "等待前台接待"           # 委托人（家属）在律所等待
    PLAINTIFF_CONSULTATION = "委托洽谈中"            # 律师与委托人家属洽谈，建立委托

    # ── 刑事侦查阶段 ──
    INVESTIGATION = "侦查阶段"                       # 律师会见嫌疑人、了解案情、申请取保候审

    # ── 刑事审查起诉阶段 ──
    PROSECUTION_REVIEW = "审查起诉阶段"              # 阅卷、听取辩护意见、决定是否起诉
    INDICTMENT_FILED = "起诉书已递交"                # 检察官将起诉书递交法院

    # ── 辩护词起草 ──
    DEFENSE_OPINION_DRAFTING = "辩护词起草中"        # 收到起诉书后起草辩护词
    DEFENSE_OPINION_FILED = "辩护词已递交"           # 辩护词递交法院

    # ── 刑事一审 ──
    WAITING_FOR_CRIMINAL_TRIAL = "等待刑事一审开庭"
    CRIMINAL_TRIAL_FIRST_INSTANCE = "刑事一审庭审中"
    CRIMINAL_FIRST_INSTANCE_VERDICT = "刑事一审判决"

    # ── 刑事上诉决策与二审 ──
    CRIMINAL_APPEAL_DECISION = "刑事上诉决策中"
    CRIMINAL_APPEAL_DRAFTING = "刑事上诉状起草中"
    CRIMINAL_APPEAL_FILED = "刑事上诉状已递交"
    WAITING_FOR_CRIMINAL_SECOND_TRIAL = "等待刑事二审开庭"
    CRIMINAL_TRIAL_SECOND_INSTANCE = "刑事二审庭审中"
    CRIMINAL_FINAL_VERDICT = "刑事终审判决"

    CLOSED = "已结案"                                # 案件归档


# 合法状态迁移图：(当前状态) -> {可达的下一状态集合}
VALID_TRANSITIONS: dict[str, set[str]] = {
    # ── 刑事入口：委托洽谈 ──
    CaseState.IDLE: {CaseState.WAITING_FOR_RECEPTION},
    CaseState.WAITING_FOR_RECEPTION: {CaseState.PLAINTIFF_CONSULTATION},
    CaseState.PLAINTIFF_CONSULTATION: {CaseState.INVESTIGATION},

    # ── 刑事流程 ──
    CaseState.INVESTIGATION: {
        CaseState.PROSECUTION_REVIEW,
        CaseState.CLOSED,  # 侦查机关撤案/不构成犯罪
    },
    CaseState.PROSECUTION_REVIEW: {
        CaseState.INDICTMENT_FILED,
        CaseState.DEFENSE_OPINION_DRAFTING,
        CaseState.CLOSED,  # 检察院不起诉决定
    },
    CaseState.INDICTMENT_FILED: {
        CaseState.DEFENSE_OPINION_DRAFTING,
        CaseState.CRIMINAL_TRIAL_FIRST_INSTANCE,
    },
    CaseState.DEFENSE_OPINION_DRAFTING: {
        CaseState.DEFENSE_OPINION_FILED,
        CaseState.INDICTMENT_FILED,
    },
    CaseState.DEFENSE_OPINION_FILED: {
        CaseState.WAITING_FOR_CRIMINAL_TRIAL,
        CaseState.CRIMINAL_TRIAL_FIRST_INSTANCE,
    },
    CaseState.WAITING_FOR_CRIMINAL_TRIAL: {CaseState.CRIMINAL_TRIAL_FIRST_INSTANCE},
    CaseState.CRIMINAL_TRIAL_FIRST_INSTANCE: {CaseState.CRIMINAL_FIRST_INSTANCE_VERDICT},
    CaseState.CRIMINAL_FIRST_INSTANCE_VERDICT: {
        CaseState.CRIMINAL_APPEAL_DECISION,
        CaseState.CRIMINAL_TRIAL_SECOND_INSTANCE,
        CaseState.CLOSED,  # 未上诉未抗诉，判决生效
    },
    CaseState.CRIMINAL_APPEAL_DECISION: {
        CaseState.CLOSED,
        CaseState.CRIMINAL_APPEAL_DRAFTING,
        CaseState.CRIMINAL_APPEAL_FILED,
        CaseState.WAITING_FOR_CRIMINAL_SECOND_TRIAL,
        CaseState.CRIMINAL_TRIAL_SECOND_INSTANCE,
    },
    CaseState.CRIMINAL_APPEAL_DRAFTING: {CaseState.CRIMINAL_APPEAL_FILED},
    CaseState.CRIMINAL_APPEAL_FILED: {CaseState.WAITING_FOR_CRIMINAL_SECOND_TRIAL},
    CaseState.WAITING_FOR_CRIMINAL_SECOND_TRIAL: {CaseState.CRIMINAL_TRIAL_SECOND_INSTANCE},
    CaseState.CRIMINAL_TRIAL_SECOND_INSTANCE: {CaseState.CRIMINAL_FINAL_VERDICT},
    CaseState.CRIMINAL_FINAL_VERDICT: {CaseState.CLOSED},

    CaseState.CLOSED: set(),  # 终态，无后续
}

SHARED_CASE_STATES: set[str] = {
    # ── 刑事共享阶段（双方状态同步）──
    CaseState.INVESTIGATION,
    CaseState.PROSECUTION_REVIEW,
    CaseState.DEFENSE_OPINION_DRAFTING,
    CaseState.DEFENSE_OPINION_FILED,
    CaseState.INDICTMENT_FILED,
    CaseState.WAITING_FOR_CRIMINAL_TRIAL,
    CaseState.CRIMINAL_TRIAL_FIRST_INSTANCE,
    CaseState.CRIMINAL_FIRST_INSTANCE_VERDICT,
    CaseState.CRIMINAL_APPEAL_DECISION,
    CaseState.CRIMINAL_APPEAL_DRAFTING,
    CaseState.CRIMINAL_APPEAL_FILED,
    CaseState.WAITING_FOR_CRIMINAL_SECOND_TRIAL,
    CaseState.CRIMINAL_TRIAL_SECOND_INSTANCE,
    CaseState.CRIMINAL_FINAL_VERDICT,
    CaseState.CLOSED,
}


class CaseStateMachine:
    """案件状态机。

    监听各阶段完成事件，校验并推进案件状态，
    将状态变更实时写入当事人的 config.yaml。
    """

    def __init__(
        self,
        event_bus: EventBus,
        storage: FileStorageManager,
        state_change_notifier: Callable[..., Awaitable[None]] | None = None,
    ):
        self.event_bus = event_bus
        self.storage = storage
        self.state_change_notifier = state_change_notifier
        self._register_listeners()

    def _register_listeners(self) -> None:
        """注册对各阶段完成事件的监听。"""
        # 每个完成事件映射到对应的下一状态
        event_to_next_state = {
            # ── 委托洽谈（刑事入口）──
            EventType.PLAINTIFF_ARRIVED: CaseState.WAITING_FOR_RECEPTION,
            EventType.CASE_ASSIGNED: None,
            EventType.PLAINTIFF_CONSULTATION_COMPLETED: CaseState.INVESTIGATION,

            # ── 结案 ──
            EventType.CASE_CLOSED: CaseState.CLOSED,

            # ── 刑事流程 ─────────────────────────────────
            EventType.INVESTIGATION_STARTED: CaseState.INVESTIGATION,
            EventType.INVESTIGATION_COMPLETED: CaseState.PROSECUTION_REVIEW,
            EventType.PROSECUTION_REVIEW_STARTED: CaseState.PROSECUTION_REVIEW,
            EventType.PROSECUTION_REVIEW_COMPLETED: CaseState.DEFENSE_OPINION_DRAFTING,
            EventType.INDICTMENT_DRAFTED: CaseState.INDICTMENT_FILED,
            EventType.INDICTMENT_FILED: CaseState.DEFENSE_OPINION_DRAFTING,
            EventType.ENTER_DEFENSE_OPINION_DRAFTING: CaseState.DEFENSE_OPINION_DRAFTING,
            EventType.DEFENSE_OPINION_DRAFTING_COMPLETED: CaseState.DEFENSE_OPINION_FILED,
            EventType.ENTER_CRIMINAL_TRIAL: CaseState.CRIMINAL_TRIAL_FIRST_INSTANCE,
            EventType.CRIMINAL_TRIAL_COMPLETED: CaseState.CRIMINAL_FIRST_INSTANCE_VERDICT,
            EventType.CRIMINAL_VERDICT_ISSUED: CaseState.CRIMINAL_APPEAL_DECISION,
            EventType.ENTER_CRIMINAL_APPEAL_TRIAL: CaseState.CRIMINAL_TRIAL_SECOND_INSTANCE,
            EventType.CRIMINAL_APPEAL_TRIAL_COMPLETED: CaseState.CRIMINAL_FINAL_VERDICT,
            EventType.CRIMINAL_FINAL_VERDICT_ISSUED: CaseState.CLOSED,
        }

        for event_type in event_to_next_state:
            self.event_bus.subscribe(
                event_type,
                self._make_handler(event_type, event_to_next_state[event_type]),
                priority=100,
            )

    def _make_handler(self, event_type: EventType, default_next: str | None):
        """为每个事件创建处理器闭包。"""

        async def handler(payload: dict):
            case_id = payload.get("case_id")
            client_path = payload.get("client_path")
            if not case_id or not client_path:
                logger.warning(f"[FSM] 事件 {event_type} 缺少 case_id 或 client_path")
                return

            # 确定目标状态
            next_state = default_next
            party_role = self._resolve_party_role(payload)

            # 特殊处理：刑事一审判决后根据是否上诉决定下一状态
            if event_type == EventType.CRIMINAL_VERDICT_ISSUED:
                # 刑事一审判决后先进入上诉决策态；服判时再由后续事件结案
                will_appeal = payload.get("will_appeal")
                if will_appeal is None:
                    next_state = CaseState.CRIMINAL_APPEAL_DECISION
                else:
                    next_state = (
                        CaseState.CRIMINAL_TRIAL_SECOND_INSTANCE if will_appeal else CaseState.CLOSED
                    )
                logger.info(f"[FSM] 刑事上诉决策: {'上诉' if will_appeal else '服判'} → {next_state}")
            elif event_type == EventType.CASE_ASSIGNED:
                # 刑事案件委托洽谈统一由委托人(plaintiff)推进
                next_state = CaseState.PLAINTIFF_CONSULTATION

            if not next_state:
                next_state = payload.get("next_state")
            if not next_state:
                logger.warning(f"[FSM] 事件 {event_type} 无法确定下一状态")
                return

            transitioned, from_state, case_runtime = await self.transition(
                case_id,
                client_path,
                next_state,
                party_role=party_role,
            )
            if transitioned and self.state_change_notifier:
                await self.state_change_notifier(
                    case_id=case_id,
                    event=event_type,
                    from_state=from_state,
                    to_state=next_state,
                    party_role=case_runtime.get("active_party_role", party_role),
                    overall_state=case_runtime.get("overall_state", next_state),
                )

        return handler

    async def transition(
        self,
        case_id: str,
        client_path: str,
        next_state: str,
        party_role: str = "",
    ) -> tuple[bool, str, dict[str, Any]]:
        """执行状态迁移。

        Args:
            case_id: 案件 ID
            client_path: 当事人 config.yaml 所在目录路径
            next_state: 目标状态

        Returns:
            迁移是否成功
        """
        config = self.storage.load_agent_config(client_path)
        current_state = config.get("case_state", CaseState.IDLE)
        current_state = self._repair_transition_precondition(
            case_id, client_path, current_state, next_state
        )

        # 如果当前状态和目标状态相同，直接返回成功（幂等性）
        if current_state == next_state:
            logger.info(f"[FSM] 案件 {case_id}: 状态已经是 {next_state}，跳过迁移")
            return True, current_state, self._load_case_runtime(case_id)

        if not self._validate_transition(current_state, next_state):
            logger.error(
                f"[FSM] 非法状态迁移: {current_state} → {next_state} (case={case_id})"
            )
            return False, current_state, self._load_case_runtime(case_id)

        # 落盘状态变更
        self.storage.update_agent_field(client_path, "case_state", next_state)
        if next_state in SHARED_CASE_STATES:
            self._sync_shared_case_state(case_id, client_path, next_state)
        case_runtime = self._update_case_runtime(case_id, next_state, party_role)
        logger.info(f"[FSM] 案件 {case_id}: {current_state} → {next_state}")

        # 结案时通知 EventBus
        if next_state == CaseState.CLOSED:
            self.event_bus.mark_case_closed(case_id)

        return True, current_state, case_runtime

    def _repair_transition_precondition(
        self,
        case_id: str,
        client_path: str,
        current_state: str,
        next_state: str,
    ) -> str:
        """修复共享阶段被错误复位后的前置状态，避免恢复事件被 FSM 拒绝。"""
        return self._repair_state_from_artifacts(
            case_id, client_path, current_state, next_state
        )

    def _repair_state_from_artifacts(
        self,
        case_id: str,
        client_path: str,
        current_state: str,
        next_state: str,
    ) -> str:
        """在状态被错误重置后，依据持久化产物补齐前置状态。"""
        if current_state == next_state or self._validate_transition(current_state, next_state):
            return current_state

        try:
            config = self.storage.load_agent_config(client_path)
        except Exception as exc:
            logger.warning(
                "[FSM] 读取案件配置失败，无法修补前置状态: %s (case=%s)",
                exc,
                case_id,
            )
            return current_state

        inferred_state = infer_case_state_from_artifacts(self.storage.base_dir, config)
        if not inferred_state or inferred_state == current_state:
            return current_state

        if inferred_state != next_state and not self._validate_transition(inferred_state, next_state):
            return current_state

        self.storage.update_agent_field(client_path, "case_state", inferred_state)
        if inferred_state in SHARED_CASE_STATES:
            self._sync_shared_case_state(case_id, client_path, inferred_state)
        logger.warning(
            "[FSM] 修补案件前置状态: %s -> %s (case=%s, target=%s)",
            current_state,
            inferred_state,
            case_id,
            next_state,
        )
        return inferred_state

    def _sync_shared_case_state(self, case_id: str, client_path: str, next_state: str) -> None:
        case_key = case_id.replace("case_", "", 1)
        for role in ("plaintiff", "defendant"):
            agent_dir = self.storage.get_case_agent_path(case_key, role)
            config_file = agent_dir / "config.yaml"
            if not config_file.exists() or str(agent_dir) == str(client_path):
                continue

            try:
                config = self.storage.load_agent_config(agent_dir)
                if config.get("case_state") != next_state:
                    self.storage.update_agent_field(agent_dir, "case_state", next_state)
            except Exception as exc:
                logger.warning(
                    "[FSM] Failed to sync shared state for %s (%s): %s",
                    case_id,
                    role,
                    exc,
                )

    @staticmethod
    def _normalize_case_id(case_id: str) -> str:
        case_key = str(case_id or "").strip()
        if case_key.startswith("case_"):
            return case_key
        return f"case_{case_key}" if case_key else ""

    @staticmethod
    def _resolve_party_role(payload: dict) -> str:
        payload_role = str(payload.get("party_role", "") or "").strip()
        if payload_role in {"plaintiff", "defendant"}:
            return payload_role

        client_path = Path(str(payload.get("client_path", "") or ""))
        if "defendant" in client_path.parts:
            return "defendant"
        if "plaintiff" in client_path.parts:
            return "plaintiff"
        return "plaintiff"

    def _load_case_runtime(self, case_id: str) -> dict[str, Any]:
        normalized_case_id = self._normalize_case_id(case_id)
        load_case_runtime = getattr(self.storage, "load_case_runtime", None)
        try:
            runtime = load_case_runtime(normalized_case_id) if callable(load_case_runtime) else {}
        except Exception:
            runtime = {}

        return {
            "case_id": normalized_case_id,
            "overall_state": str(runtime.get("overall_state", CaseState.IDLE) or CaseState.IDLE),
            "plaintiff_state": str(runtime.get("plaintiff_state", CaseState.IDLE) or CaseState.IDLE),
            "defendant_state": str(runtime.get("defendant_state", CaseState.IDLE) or CaseState.IDLE),
            "active_party_role": str(runtime.get("active_party_role", "plaintiff") or "plaintiff"),
        }

    def _resolve_runtime_state_owner(self, next_state: str, party_role: str) -> str:
        if next_state in SHARED_CASE_STATES:
            return "shared"
        if next_state == CaseState.PLAINTIFF_CONSULTATION:
            return "plaintiff"
        if next_state == CaseState.WAITING_FOR_RECEPTION:
            return party_role or "plaintiff"
        return party_role or "plaintiff"

    def _resolve_active_party_role(self, next_state: str, party_role: str) -> str:
        owner = self._resolve_runtime_state_owner(next_state, party_role)
        return "shared" if owner == "shared" else owner

    def _update_case_runtime(
        self,
        case_id: str,
        next_state: str,
        party_role: str,
    ) -> dict[str, Any]:
        runtime = self._load_case_runtime(case_id)
        owner = self._resolve_runtime_state_owner(next_state, party_role)

        if owner == "shared":
            runtime["plaintiff_state"] = next_state
            runtime["defendant_state"] = next_state
        elif owner == "defendant":
            runtime["defendant_state"] = next_state
        else:
            runtime["plaintiff_state"] = next_state

        runtime["overall_state"] = next_state
        runtime["active_party_role"] = self._resolve_active_party_role(next_state, party_role)
        save_case_runtime = getattr(self.storage, "save_case_runtime", None)
        if callable(save_case_runtime):
            save_case_runtime(self._normalize_case_id(case_id), runtime)
        return runtime


    @staticmethod
    def _validate_transition(current: str, next_state: str) -> bool:
        """校验状态迁移合法性。"""
        valid_next = VALID_TRANSITIONS.get(current, set())
        return next_state in valid_next

    @staticmethod
    def get_valid_next_states(current: str) -> set[str]:
        """获取当前状态的所有合法后续状态。"""
        return VALID_TRANSITIONS.get(current, set())
