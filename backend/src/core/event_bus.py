"""异步事件总线 (EventBus) — 沙盒小镇的神经中枢。

基于 asyncio 的发布-订阅系统，替代原有的流程式控制。
所有 Agent 通过事件总线解耦通信，互不直接调用。
"""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from enum import Enum
from typing import Any

from ..utils.case_progress import infer_case_state_from_artifacts

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """沙盒事件类型枚举 — 纯刑事（公诉案件）流程。"""

    # ── 委托洽谈（前台接待 → 委托洽谈 → 侦查）──
    PLAINTIFF_ARRIVED = "PLAINTIFF_ARRIVED"                                # 委托人（家属）抵达律所
    CASE_ASSIGNED = "CASE_ASSIGNED"                                        # 前台完成分单
    ENTER_PLAINTIFF_CONSULTATION = "ENTER_PLAINTIFF_CONSULTATION"          # 进入委托洽谈
    PLAINTIFF_CONSULTATION_COMPLETED = "PLAINTIFF_CONSULTATION_COMPLETED"  # 委托洽谈结束

    # ── 结案清场 ──
    CASE_CLOSED = "CASE_CLOSED"                                            # 结案清场

    # ── 等候队列管理 ──
    CLIENT_WAITING = "CLIENT_WAITING"                                      # 委托人在沙发等候
    CLIENT_CALLED = "CLIENT_CALLED"                                        # 通知等候的当事人

    # ── 律师状态通知 ──
    LAWYER_AVAILABLE = "LAWYER_AVAILABLE"                                  # 律师空闲，可接待下一位

    # ── 刑事侦查阶段 ──────────────────────────────────────────
    INVESTIGATION_STARTED = "INVESTIGATION_STARTED"
    INVESTIGATION_COMPLETED = "INVESTIGATION_COMPLETED"

    # ── 刑事审查起诉阶段 ──────────────────────────────────────
    PROSECUTION_REVIEW_STARTED = "PROSECUTION_REVIEW_STARTED"
    PROSECUTION_REVIEW_COMPLETED = "PROSECUTION_REVIEW_COMPLETED"
    INDICTMENT_DRAFTED = "INDICTMENT_DRAFTED"                    # 检察官完成起诉书起草
    INDICTMENT_FILED = "INDICTMENT_FILED"                        # 起诉书递交法院

    # ── 刑事辩护词起草 ────────────────────────────────────────
    ENTER_DEFENSE_OPINION_DRAFTING = "ENTER_DEFENSE_OPINION_DRAFTING"
    DEFENSE_OPINION_DRAFTING_COMPLETED = "DEFENSE_OPINION_DRAFTING_COMPLETED"

    # ── 刑事一审审判 ──────────────────────────────────────────
    ENTER_CRIMINAL_TRIAL = "ENTER_CRIMINAL_TRIAL"
    CRIMINAL_TRIAL_COMPLETED = "CRIMINAL_TRIAL_COMPLETED"
    CRIMINAL_VERDICT_ISSUED = "CRIMINAL_VERDICT_ISSUED"

    # ── 刑事二审审判 ──────────────────────────────────────────
    ENTER_CRIMINAL_APPEAL_TRIAL = "ENTER_CRIMINAL_APPEAL_TRIAL"
    CRIMINAL_APPEAL_TRIAL_COMPLETED = "CRIMINAL_APPEAL_TRIAL_COMPLETED"
    CRIMINAL_FINAL_VERDICT_ISSUED = "CRIMINAL_FINAL_VERDICT_ISSUED"


# 事件处理器类型：接收 payload dict，返回协程
EventHandler = Callable[[dict], Coroutine[Any, Any, None]]


class EventBus:
    """纯 asyncio 的发布-订阅事件总线。"""

    _RUNTIME_ISSUE_STAGE_MAP: dict[str, tuple[str, str]] = {
        str(EventType.ENTER_PLAINTIFF_CONSULTATION): ("LC", "委托洽谈"),
        str(EventType.INVESTIGATION_STARTED): ("INV", "侦查阶段"),
        str(EventType.PROSECUTION_REVIEW_STARTED): ("PR", "审查起诉阶段"),
        str(EventType.ENTER_DEFENSE_OPINION_DRAFTING): ("DS", "辩护词起草"),
        str(EventType.ENTER_CRIMINAL_TRIAL): ("CR", "刑事一审庭审"),
        str(EventType.ENTER_CRIMINAL_APPEAL_TRIAL): ("CRA", "刑事二审庭审"),
    }

    def __init__(self):
        self._subscribers: dict[str, list[tuple[int, int, EventHandler]]] = {}
        self._subscriber_order = 0
        self._event_log: list[dict] = []
        self._closed_cases: set = set()
        self._expected_cases: int = 0
        self._all_closed_event = asyncio.Event()
        self._closed_case_event = asyncio.Event()
        self._active_scenarios: dict[str, dict] = {}  # 跟踪活跃场景及其参与者
        self.runtime_issue_reporter: Callable[..., Coroutine[Any, Any, bool]] | None = None
        logger.info("EventBus initialized")

    async def _wait_if_paused(self, agent_registry) -> None:
        """Block maintenance work while the simulation is paused."""
        map_engine = getattr(agent_registry, "map_engine", None)
        resumed_event = getattr(map_engine, "_resumed_event", None)
        if map_engine and getattr(map_engine, "_paused", False) and resumed_event is not None:
            logger.info("[EventBus] 检测到暂停，等待恢复后再继续")
            await resumed_event.wait()

    def subscribe(self, event_type: str, handler: EventHandler, *, priority: int = 0) -> None:
        """注册事件监听器。

        Args:
            event_type: 事件类型（字符串或 EventType 枚举）
            handler: 异步回调函数，签名为 async def handler(payload: dict)
        """
        key = str(event_type)
        if key not in self._subscribers:
            self._subscribers[key] = []
        self._subscriber_order += 1
        self._subscribers[key].append((int(priority or 0), self._subscriber_order, handler))
        self._subscribers[key].sort(key=lambda item: (-item[0], item[1]))
        logger.debug(f"Subscribed handler {handler.__qualname__} to {key}")

    async def publish(self, event_type: str, payload: dict | None = None) -> None:
        """向总线广播事件，所有订阅者将被异步调用。

        Args:
            event_type: 事件类型
            payload: 事件数据负载
        """
        key = str(event_type)
        payload = payload or {}
        self._event_log.append({"event": key, "payload": payload})
        logger.info(f"[EventBus] 广播事件: {key} | payload: {payload}")

        entries = self._subscribers.get(key, [])
        if not entries:
            logger.warning(f"[EventBus] 事件 {key} 无订阅者")
            return

        priority_groups: list[tuple[int, list[EventHandler]]] = []
        for priority, _order, handler in entries:
            if not priority_groups or priority_groups[-1][0] != priority:
                priority_groups.append((priority, []))
            priority_groups[-1][1].append(handler)

        for _priority, handlers in priority_groups:
            tasks = [asyncio.create_task(h(payload)) for h in handlers]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    import traceback
                    error_trace = "".join(traceback.format_exception(type(result), result, result.__traceback__))
                    logger.error(
                        f"[EventBus] 处理器 {handlers[i].__qualname__} 处理 {key} 时异常: {result}\n{error_trace}"
                    )
                    await self._report_handler_runtime_issue(
                        event_type=key,
                        payload=payload,
                        handler=handlers[i],
                        exc=result,
                    )

    @classmethod
    def _resolve_runtime_issue_context(cls, event_type: str, payload: dict[str, Any]) -> dict[str, str] | None:
        case_id = str(payload.get("case_id", "") or "").strip()
        if not case_id:
            return None

        scenario_type = str(payload.get("scenario_type", "") or "").strip()
        stage_label = str(payload.get("stage_label", "") or "").strip()
        if not scenario_type:
            scenario_type, fallback_label = cls._RUNTIME_ISSUE_STAGE_MAP.get(event_type, ("", ""))
            stage_label = stage_label or fallback_label

        if not scenario_type:
            return None

        return {
            "case_id": case_id,
            "scenario_type": scenario_type,
            "stage_label": stage_label,
        }

    async def _report_handler_runtime_issue(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        handler: EventHandler,
        exc: Exception,
    ) -> None:
        reporter = getattr(self, "runtime_issue_reporter", None)
        if not callable(reporter):
            return

        context = self._resolve_runtime_issue_context(event_type, payload)
        if context is None:
            return

        handler_name = getattr(handler, "__qualname__", "") or getattr(handler, "__name__", "") or "unknown_handler"
        try:
            await reporter(
                case_id=context["case_id"],
                scenario_type=context["scenario_type"],
                exc=exc,
                stage_label=context["stage_label"],
                event_type=event_type,
                handler_name=handler_name,
            )
        except TypeError:
            await reporter(
                case_id=context["case_id"],
                scenario_type=context["scenario_type"],
                exc=exc,
                stage_label=context["stage_label"],
            )
        except Exception as report_exc:
            logger.warning(
                "[EventBus] 上报运行异常失败: event=%s handler=%s error=%s",
                event_type,
                handler_name,
                report_exc,
            )

    def set_expected_cases(self, count: int) -> None:
        """设置预期需要关闭的案件总数。"""
        self._expected_cases = count
        self._closed_cases.clear()
        self._all_closed_event.clear()
        self._closed_case_event.clear()

    def get_closed_case_count(self) -> int:
        """返回当前已结案数量。"""
        return len(self._closed_cases)

    async def wait_for_case_close_since(self, previous_count: int) -> int:
        """等待直到已结案数量大于给定值。"""
        while len(self._closed_cases) <= previous_count:
            await self._closed_case_event.wait()
            self._closed_case_event.clear()
        return len(self._closed_cases)

    def mark_case_closed(self, case_id: str) -> None:
        """标记一个案件已结案。"""
        previous_count = len(self._closed_cases)
        self._closed_cases.add(case_id)
        if len(self._closed_cases) == previous_count:
            return
        self._closed_case_event.set()
        # 清理该案件的活跃场景记录
        self._active_scenarios.pop(case_id, None)
        logger.info(
            f"[EventBus] 案件 {case_id} 已结案 "
            f"({len(self._closed_cases)}/{self._expected_cases})"
        )
        if self._expected_cases > 0 and len(self._closed_cases) >= self._expected_cases:
            self._all_closed_event.set()

    def register_active_scenario(
        self, case_id: str, scenario_type: str, participant_ids: list[str]
    ) -> None:
        """注册活跃场景及其参与者。

        Args:
            case_id: 案件 ID
            scenario_type: 场景类型（如 "LC", "CD" 等）
            participant_ids: 参与该场景的 Agent ID 列表
        """
        self._active_scenarios[case_id] = {
            "scenario_type": scenario_type,
            "participants": set(participant_ids),
            "start_time": asyncio.get_event_loop().time(),
        }
        logger.debug(
            f"[EventBus] 注册活跃场景: {case_id} ({scenario_type}), "
            f"参与者: {participant_ids}"
        )

    def unregister_active_scenario(self, case_id: str) -> None:
        """注销活跃场景。"""
        if case_id in self._active_scenarios:
            self._active_scenarios.pop(case_id)
            logger.debug(f"[EventBus] 注销活跃场景: {case_id}")

    def get_active_participants(self) -> set[str]:
        """获取所有活跃场景中的参与者 ID 集合。"""
        participants = set()
        for scenario_info in self._active_scenarios.values():
            participants.update(scenario_info["participants"])
        return participants

    def restore_active_scenarios(self, scenarios_dict: dict[str, dict]) -> None:
        """从检查点恢复活跃场景状态。

        Args:
            scenarios_dict: 场景字典，格式为 {case_id: {scenario_type, participants, start_time, status}}
        """
        self._active_scenarios.clear()
        for case_id, scenario_info in scenarios_dict.items():
            # 只恢复 in_progress 状态的场景
            if scenario_info.get("status") == "in_progress":
                self._active_scenarios[case_id] = {
                    "scenario_type": scenario_info["scenario_type"],
                    "participants": set(scenario_info["participants"]),
                    "start_time": scenario_info.get("start_time", 0),
                }
                logger.info(
                    f"[EventBus] 恢复活跃场景: {case_id} ({scenario_info['scenario_type']}), "
                    f"参与者: {scenario_info['participants']}"
                )

    def is_agent_busy(self, agent_id: str) -> bool:
        """检查 Agent 是否参与任何活跃场景。

        Args:
            agent_id: Agent ID

        Returns:
            True 如果 Agent 正在参与活跃场景，否则 False
        """
        for scenario_info in self._active_scenarios.values():
            if agent_id in scenario_info["participants"]:
                return True
        return False

    def get_agent_current_scenario(self, agent_id: str) -> dict | None:
        """获取 Agent 当前参与的场景信息。

        Args:
            agent_id: Agent ID

        Returns:
            场景信息字典 {case_id, scenario_type, participants, start_time} 或 None
        """
        for case_id, scenario_info in self._active_scenarios.items():
            if agent_id in scenario_info["participants"]:
                return {
                    "case_id": case_id,
                    "scenario_type": scenario_info["scenario_type"],
                    "participants": list(scenario_info["participants"]),
                    "start_time": scenario_info["start_time"],
                }
        return None

    def get_active_scenarios_snapshot(self) -> dict[str, dict]:
        """获取活跃场景的快照，用于持久化到检查点。

        Returns:
            场景字典，格式为 {case_id: {scenario_type, participants, start_time, status}}
        """
        snapshot = {}
        for case_id, scenario_info in self._active_scenarios.items():
            snapshot[case_id] = {
                "scenario_type": scenario_info["scenario_type"],
                "participants": list(scenario_info["participants"]),
                "start_time": scenario_info["start_time"],
                "status": "in_progress",
            }
        return snapshot

    async def spin_until_all_closed(
        self,
        timeout: float | None = None,  # 保留以兼容旧代码
        storage_manager=None,
        agent_registry=None,
        check_interval: float = 30.0,
    ) -> None:
        """挂起主循环，直到所有预期案件全部 CLOSED。

        定期检查是否有 Agent 被标记为等待但未参与任何活跃场景，
        自动将其状态重置为空闲以防止卡死。

        Args:
            timeout: 已废弃，保留参数以兼容旧代码
            storage_manager: FileStorageManager 实例，用于更新 Agent 状态
            agent_registry: AgentRegistry 实例，用于查找 Agent
            check_interval: 检查间隔（秒）
        """
        if self._expected_cases <= 0:
            logger.warning("[EventBus] 未设置预期案件数，spin 立即返回")
            return

        logger.info(
            f"[EventBus] 等待 {self._expected_cases} 个案件全部结案..."
        )

        # 启动前立即执行一次防卡死检查（处理程序重启后的残留状态）
        if storage_manager and agent_registry:
            await self._wait_if_paused(agent_registry)
            logger.info("[EventBus] 执行启动时防卡死检查...")
            await self._check_and_fix_deadlocked_agents(
                storage_manager, agent_registry
            )

        # 启动防卡死检查任务
        check_task = None
        if storage_manager and agent_registry:
            check_task = asyncio.create_task(
                self._periodic_deadlock_check(
                    storage_manager, agent_registry, check_interval
                )
            )

        try:
            # 无超时限制 - 依赖检查点恢复机制确保案件最终完成
            await self._all_closed_event.wait()
            logger.info("[EventBus] 所有案件已结案，沙盒循环结束")
        finally:
            if check_task:
                check_task.cancel()
                try:
                    await check_task
                except asyncio.CancelledError:
                    pass

    async def _periodic_deadlock_check(
        self,
        storage_manager,
        agent_registry,
        interval: float,
    ) -> None:
        """定期检查并清理卡死的 Agent。"""
        while True:
            try:
                await self._wait_if_paused(agent_registry)
                await asyncio.sleep(interval)
                await self._wait_if_paused(agent_registry)
                await self._check_and_fix_deadlocked_agents(
                    storage_manager, agent_registry
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[EventBus] 防卡死检查异常: {e}")

    def _infer_client_resume_state(self, storage_manager, config: dict) -> str:
        """推断被错误复位后的可恢复状态（纯刑事）。"""
        case_state = infer_case_state_from_artifacts(storage_manager.base_dir, config)
        if case_state != "空闲":
            return case_state

        case_id = f"case_{config.get('case_id', '1')}"
        party_role = config.get("party_role", "plaintiff")
        counterpart_role = "defendant" if party_role == "plaintiff" else "plaintiff"
        counterpart_state = "空闲"

        try:
            counterpart_path = storage_manager.get_case_agent_path(
                config.get("case_id", "1"),
                counterpart_role,
            )
            counterpart_config = storage_manager.load_agent_config(counterpart_path)
            counterpart_state = counterpart_config.get("case_state", "空闲")
        except Exception:
            counterpart_state = "空闲"

        if counterpart_state in {
            "侦查阶段",
            "审查起诉阶段",
            "辩护词起草中",
            "辩护词已递交",
            "起诉书已递交",
            "等待刑事一审开庭",
            "刑事一审庭审中",
            "刑事一审判决",
            "刑事上诉决策中",
            "等待刑事二审开庭",
            "刑事二审庭审中",
            "刑事终审判决",
        }:
            return counterpart_state

        return case_state

    async def _check_and_fix_deadlocked_agents(
        self, storage_manager, agent_registry
    ) -> None:
        """检查并修复卡死的 Agent（纯刑事）。"""
        active_participants = self.get_active_participants()
        shared_case_resumed: set[str] = set()

        # 检查所有当事人
        for client in agent_registry.get_agents_by_type("client"):
            if not client.config_path:
                continue

            try:
                config = storage_manager.load_agent_config(client.config_path)
                stored_state = config.get("case_state", "空闲")
                case_state = self._infer_client_resume_state(storage_manager, config)
                if case_state != stored_state:
                    storage_manager.update_agent_field(
                        client.config_path, "case_state", case_state
                    )
                    config["case_state"] = case_state
                    logger.info(
                        "[EventBus] 修补案件状态后重试恢复: %s -> %s (%s)",
                        stored_state,
                        case_state,
                        client.name,
                    )

                # 如果状态不是空闲/已结案，但不在活跃场景中
                if case_state not in ["空闲", "已结案", "等待前台接待"]:
                    if client.agent_id not in active_participants:
                        logger.warning(
                            f"[EventBus] 检测到卡死的当事人: {client.name} "
                            f"(状态: {case_state}, 未参与任何活跃场景)"
                        )

                        # 根据状态恢复场景执行，而不是直接结案
                        case_id = f"case_{config.get('case_id', '1')}"
                        party_role = config.get("party_role", "plaintiff")
                        client_path = client.config_path

                        if case_state in ["原告咨询中"]:
                            # 恢复委托洽谈场景
                            lawyer_id = config.get("assigned_lawyer_id", "")
                            target_firm = config.get("assigned_firm", "law_firm_A")
                            map_prefix = "lawfirmA" if target_firm == "law_firm_A" else "lawfirmB"

                            if not lawyer_id:
                                logger.warning(
                                    "[EventBus] %s 缺少 assigned_lawyer_id，退回前台接待重新分单",
                                    client.name,
                                )
                                storage_manager.update_agent_field(
                                    client.config_path, "case_state", "等待前台接待"
                                )
                                storage_manager.update_agent_field(
                                    client.config_path, "assigned_lawyer_id", ""
                                )
                                await self.publish(EventType.PLAINTIFF_ARRIVED, {
                                    "client_id": client.agent_id,
                                    "case_id": case_id,
                                    "target_firm": target_firm,
                                    "map_prefix": map_prefix,
                                    "party_role": party_role,
                                    "client_path": client.config_path,
                                })
                                continue

                            logger.info(
                                f"[EventBus] 恢复委托洽谈场景: {case_id}, "
                                f"client={client.agent_id}, lawyer={lawyer_id}"
                            )

                            # 关键修复：先清空律师的当前案件，避免被判定为繁忙
                            if lawyer_id:
                                lawyer = agent_registry.get_agent(lawyer_id)
                                if lawyer and lawyer.config_path:
                                    try:
                                        storage_manager.update_agent_field(
                                            lawyer.config_path, "current_handling_case", None
                                        )
                                        logger.info(
                                            f"[EventBus] 已清空律师 {lawyer_id} 的当前案件，准备恢复委托洽谈"
                                        )
                                    except Exception as e:
                                        logger.error(f"[EventBus] 清空律师状态失败: {e}")

                            # 重新发布 CASE_ASSIGNED 事件来恢复委托洽谈
                            await self.publish(EventType.CASE_ASSIGNED, {
                                "client_id": client.agent_id,
                                "case_id": case_id,
                                "target_firm": target_firm,
                                "map_prefix": map_prefix,
                                "party_role": party_role,
                                "client_path": client.config_path,
                                "lawyer_id": lawyer_id,
                            })
                        elif case_state in {"辩护词已递交", "起诉书已递交", "等待刑事一审开庭"}:
                            if case_id in shared_case_resumed:
                                logger.info(
                                    f"[EventBus] 案件 {case_id} 的刑事一审共享阶段已恢复，跳过重复触发"
                                )
                                continue
                            shared_case_resumed.add(case_id)
                            logger.info(
                                f"[EventBus] 恢复刑事一审流程: {case_id}, state={case_state}"
                            )
                            await self.publish(EventType.ENTER_CRIMINAL_TRIAL, {
                                "case_id": case_id,
                                "client_path": client_path,
                            })
                        elif case_state == "刑事一审庭审中":
                            if case_id in shared_case_resumed:
                                logger.info(
                                    f"[EventBus] 案件 {case_id} 的刑事一审共享阶段已恢复，跳过重复触发"
                                )
                                continue
                            shared_case_resumed.add(case_id)
                            logger.info(
                                f"[EventBus] 恢复刑事一审庭审: {case_id}, state={case_state}"
                            )
                            await self.publish(EventType.ENTER_CRIMINAL_TRIAL, {
                                "case_id": case_id,
                                "client_path": client_path,
                            })
                        elif case_state == "刑事一审判决":
                            if case_id in shared_case_resumed:
                                logger.info(
                                    f"[EventBus] 案件 {case_id} 的刑事一审判决已恢复，跳过重复触发"
                                )
                                continue
                            shared_case_resumed.add(case_id)
                            logger.info(
                                f"[EventBus] 恢复刑事一审判决后流程: {case_id}, state={case_state}"
                            )
                            await self.publish(EventType.CRIMINAL_VERDICT_ISSUED, {
                                "case_id": case_id,
                                "client_path": client_path,
                            })
                        elif case_state in {"刑事上诉决策中", "刑事上诉状起草中", "刑事上诉状已递交", "等待刑事二审开庭", "刑事二审庭审中"}:
                            if case_id in shared_case_resumed:
                                logger.info(
                                    f"[EventBus] 案件 {case_id} 的刑事二审共享阶段已恢复，跳过重复触发"
                                )
                                continue
                            shared_case_resumed.add(case_id)
                            logger.info(
                                f"[EventBus] 恢复刑事二审流程: {case_id}, state={case_state}"
                            )
                            await self.publish(EventType.ENTER_CRIMINAL_APPEAL_TRIAL, {
                                "case_id": case_id,
                                "client_path": client_path,
                            })
                        elif case_state == "刑事终审判决":
                            if case_id in shared_case_resumed:
                                logger.info(
                                    f"[EventBus] 案件 {case_id} 的刑事终审已恢复，跳过重复触发"
                                )
                                continue
                            shared_case_resumed.add(case_id)
                            logger.info(
                                f"[EventBus] 恢复刑事终审流程: {case_id}, state={case_state}"
                            )
                            await self.publish(EventType.CRIMINAL_APPEAL_TRIAL_COMPLETED, {
                                "case_id": case_id,
                                "client_path": client_path,
                            })
                        elif case_state in {"侦查阶段", "审查起诉阶段", "辩护词起草中"}:
                            # 责任角色：INV 由委托人(plaintiff)推进，PR/DS 由被告人(defendant)推进
                            responsible_role = "plaintiff" if case_state == "侦查阶段" else "defendant"
                            if party_role != responsible_role:
                                logger.info(
                                    "[EventBus] 案件 %s 处于 %s，%s 非责任角色，等待对方恢复: %s",
                                    case_id, case_state, party_role, responsible_role,
                                )
                                continue
                            resume_event = {
                                "侦查阶段": EventType.INVESTIGATION_STARTED,
                                "审查起诉阶段": EventType.PROSECUTION_REVIEW_STARTED,
                                "辩护词起草中": EventType.ENTER_DEFENSE_OPINION_DRAFTING,
                            }[case_state]
                            logger.info(
                                "[EventBus] 恢复刑事阶段 %s: %s",
                                case_state,
                                case_id,
                            )
                            await self.publish(resume_event, {
                                "case_id": case_id,
                                "client_path": client_path,
                                "party_role": party_role,
                            })
                        else:
                            # 其他状态暂时重置为空闲（未来可以添加更多恢复逻辑）
                            logger.warning(
                                f"[EventBus] 状态 {case_state} 的恢复逻辑尚未实现，重置为空闲"
                            )
                            storage_manager.update_agent_field(
                                client.config_path, "case_state", "空闲"
                            )
            except Exception as e:
                logger.error(
                    f"[EventBus] 检查当事人 {client.name} 时出错: {e}"
                )

        # 检查所有律师
        for lawyer in agent_registry.get_agents_by_type("lawyer"):
            if not lawyer.config_path:
                continue

            try:
                config = storage_manager.load_agent_config(lawyer.config_path)
                current_case = config.get("current_handling_case")

                # 如果律师有当前案件，但不在活跃场景中
                if current_case and lawyer.agent_id not in active_participants:
                    logger.warning(
                        f"[EventBus] 检测到卡死的律师: {lawyer.name} "
                        f"(案件: {current_case}, 未参与任何活跃场景)"
                    )
                    # 清空当前案件
                    storage_manager.update_agent_field(
                        lawyer.config_path, "current_handling_case", None
                    )
                    logger.info(
                        f"[EventBus] 已清空律师 {lawyer.name} 的当前案件"
                    )
            except Exception as e:
                logger.error(
                    f"[EventBus] 检查律师 {lawyer.name} 时出错: {e}"
                )

    @property
    def event_log(self) -> list[dict]:
        """返回事件日志副本。"""
        return list(self._event_log)
