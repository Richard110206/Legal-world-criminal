"""场景编排器 (ScenarioOrchestrator)。

监听 FSM 触发的场景进入事件，从 Registry 查找参与 Agent，
加载案件数据，构建 Prompt，激活 Agent，执行场景，保存输出。

替代 sandbox_main.py 中硬编码的闭包编排逻辑。
"""

import asyncio
import concurrent.futures
import json
import logging
import random
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ..prompts.prompt_assembler import PromptAssembler
from ..data.data_loader import DataLoader
from ..player_lawyer.responsibility_marker import build_player_responsibility_marker
from ..pipeline.stage_tool_resolver import apply_stage_tool_permissions
from ..runtime_tech_strategy import RuntimeTechStrategy
from ..utils.drafted_document_sections import (
    resolve_stage_document_text,
)
from ..utils.prompt_profile import resolve_prompt_profile_max_turns
from ..utils.runtime_flags import (
    player_lawyer_ai_surrogate_enabled,
    player_lawyer_mode_for_frontend,
    scenario_verbose_enabled,
)
from ..utils.live_card_memory import (
    CLIENT_LOAD_TOOL_NAME,
    CLIENT_MEMORY_OWNER,
    CLIENT_SAVE_TOOL_NAME,
    LAWYER_LOAD_TOOL_NAME,
    LAWYER_MEMORY_OWNER,
    LAWYER_SAVE_TOOL_NAME,
    flatten_memory_payload,
    get_empty_memory_payload,
    has_meaningful_memory,
    load_memory_for_agent,
)
from ..utils.agent_trace import CaseAgentTraceRecorder, bind_agent_trace_context
from ..core.event_bus import EventType

if TYPE_CHECKING:
    from .agent_registry import AgentRegistry
    from ..core.event_bus import EventBus
    from .case_fsm import CaseStateMachine
    from ..core.file_storage_manager import FileStorageManager
    from ..simulation.map_engine import TownAvatarInterface

logger = logging.getLogger(__name__)
SCENARIO_VERBOSE = scenario_verbose_enabled()

DEFAULT_CLIENT_INTERACTION_GUIDELINES = (
    "请像真实当事人一样自然说话，默认单次发言不要过长，尽量控制在一段内说清当前最相关的内容；"
    "通常用2到4句口语化短句表达即可，不要一次性铺陈太多事实、问题或情绪，其余内容留到律师追问后再继续补充。"
)

CHARACTER_POOL = [
    "Adam",
    "Alex",
    "Amelia",
    "Ash",
    "Bob",
    "Bruce",
    "Conference_man",
    "Conference_woman",
    "Dan",
    "Edward",
    "Lucy",
    "Molly",
    "Pier",
    "Rob",
    "Roki",
    "Samuel",
]


class ScenarioOrchestrator:
    """Bridges FSM state transitions to scenario execution."""

    STAGE_DISPLAY_NAMES = {
        "LC": "委托洽谈",
        "INV": "侦查阶段",
        "PR": "审查起诉阶段",
        "DS": "辩护词起草",
        "CR": "刑事一审庭审",
        "CRA": "刑事二审庭审",
        "CRIMINAL_FINAL_VERDICT": "刑事终审判决",
    }

    def __init__(
        self,
        registry: "AgentRegistry",
        event_bus: "EventBus",
        fsm: "CaseStateMachine",
        storage: "FileStorageManager",
        output_dir: Path,
        map_engine: "TownAvatarInterface" = None,
    ):
        self.registry = registry
        self.event_bus = event_bus
        self.fsm = fsm
        self.storage = storage
        self.output_dir = output_dir
        self.map_engine = map_engine

        # Import Path for type checking
        from pathlib import Path as PathType
        self.Path = PathType

    def __init__(
        self,
        registry: "AgentRegistry",
        event_bus: "EventBus",
        fsm: "CaseStateMachine",
        storage: "FileStorageManager",
        sandbox_data_dir: Path,
        map_engine: "TownAvatarInterface | None" = None,
        checkpoint_manager: "Any | None" = None,
    ):
        self.registry = registry
        self.event_bus = event_bus
        self.fsm = fsm
        self.storage = storage
        self.map_engine = map_engine
        self.checkpoint_manager = checkpoint_manager
        self.sandbox_data_dir = Path(sandbox_data_dir)
        self.output_dir = self.sandbox_data_dir / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 位置占用追踪
        self._occupied_locations: dict[str, str] = {}  # {loc_id: agent_id}

        # 等候队列 (每个律所一个队列)
        self._waiting_queues: dict[str, list[dict]] = {}  # {firm_id: [{client_id, case_id, sofa_id}]}
        self._resource_lock = asyncio.Lock()
        self._trial_queues: dict[str, deque[dict[str, Any]]] = {
            "courtA": deque(),
            "courtB": deque(),
        }
        self._court_reservations: dict[str, str] = {}
        self._judge_reservations: dict[str, str] = {}
        self.runtime_issue_reporter: Callable[..., Any] | None = None
        self._case_trace_recorders: dict[str, CaseAgentTraceRecorder] = {}
        self._runtime_tech_loop: asyncio.AbstractEventLoop | None = None
        self._player_document_followup_sessions: dict[str, dict[str, Any]] = {}

        self._register_hooks()

    async def _report_runtime_issue(
        self,
        *,
        case_id: str,
        scenario_type: str,
        exc: Exception,
        stage_label: str = "",
    ) -> bool:
        reporter = getattr(self, "runtime_issue_reporter", None)
        if not callable(reporter):
            return False
        try:
            return bool(
                await reporter(
                    case_id=case_id,
                    scenario_type=scenario_type,
                    exc=exc,
                    stage_label=stage_label,
                )
            )
        except Exception as report_exc:
            logger.warning(
                "[Orchestrator] failed to report runtime issue for %s/%s: %s",
                case_id,
                scenario_type,
                report_exc,
            )
            return False

    def _register_hooks(self) -> None:
        """Subscribe to scenario-entry events (纯刑事流程)."""
        from ..core.event_bus import EventType
        self.event_bus.subscribe(EventType.ENTER_PLAINTIFF_CONSULTATION, self._run_consultation)
        self.event_bus.subscribe(EventType.PLAINTIFF_CONSULTATION_COMPLETED, self._auto_close_case)
        self.event_bus.subscribe(EventType.CASE_ASSIGNED, self._choreograph_case_assigned)
        self.event_bus.subscribe(EventType.CLIENT_CALLED, self._choreograph_client_called)
        self.event_bus.subscribe(EventType.CASE_CLOSED, self._choreograph_case_closed)

        # ── 刑事流程 ─────────────────────────────────────────
        self.event_bus.subscribe(EventType.INVESTIGATION_STARTED, self._run_investigation)
        self.event_bus.subscribe(EventType.INVESTIGATION_COMPLETED, self._on_investigation_completed)
        self.event_bus.subscribe(EventType.PROSECUTION_REVIEW_STARTED, self._run_prosecution_review)
        self.event_bus.subscribe(EventType.PROSECUTION_REVIEW_COMPLETED, self._on_prosecution_review_completed)
        self.event_bus.subscribe(EventType.ENTER_DEFENSE_OPINION_DRAFTING, self._run_defense_opinion_drafting)
        self.event_bus.subscribe(EventType.DEFENSE_OPINION_DRAFTING_COMPLETED, self._on_defense_opinion_filed)
        self.event_bus.subscribe(EventType.ENTER_CRIMINAL_TRIAL, self._run_criminal_trial)
        self.event_bus.subscribe(EventType.CRIMINAL_TRIAL_COMPLETED, self._on_criminal_trial_completed)
        self.event_bus.subscribe(EventType.CRIMINAL_VERDICT_ISSUED, self._on_criminal_verdict_issued)
        self.event_bus.subscribe(EventType.ENTER_CRIMINAL_APPEAL_TRIAL, self._run_criminal_appeal_trial)
        self.event_bus.subscribe(EventType.CRIMINAL_APPEAL_TRIAL_COMPLETED, self._on_criminal_final_verdict)

    @staticmethod
    def _configure_stage_tools(stage_code: str, role_to_agent: dict[str, Any]) -> dict[str, list[str]]:
        """Apply manifest-declared tool permissions for active scenario participants."""
        return apply_stage_tool_permissions(stage_code, role_to_agent)

    # ── Helper: load case data from client config ──

    def _load_case_data(self, client_config_path: str) -> tuple:
        """Load DataLoader and case dict from a client's config.yaml.

        Returns:
            (data_loader, case_dict, client_config)
        """
        config = self.storage.load_agent_config(client_config_path)
        dataset_path = config.get("dataset_path", "")

        data_loader = DataLoader(dataset_path)
        case = data_loader.resolve_case_for_config(
            config,
            fallback_dataset_paths=self._build_dataset_fallback_candidates(dataset_path),
        )
        return data_loader, case, config

    @staticmethod
    def _build_dataset_fallback_candidates(dataset_path: str) -> list[str]:
        current_path = str(dataset_path or "").strip()
        project_root = Path(__file__).resolve().parents[3]
        data_root = project_root / "data"
        if not data_root.exists():
            return []

        candidates: list[str] = []
        seen: set[str] = set()

        def _add(candidate: Path) -> None:
            resolved = str(candidate.resolve())
            if resolved not in seen:
                seen.add(resolved)
                candidates.append(resolved)

        if current_path:
            candidate_name = Path(current_path.replace("\\", "/")).name
            if candidate_name:
                same_name_candidate = data_root / candidate_name
                if same_name_candidate.exists():
                    _add(same_name_candidate)

        for item in sorted(data_root.glob("*.json")):
            if item.is_file():
                _add(item)

        return candidates

    @staticmethod
    def _build_client_prompt_profile(agent: Any, extracted_profile: dict[str, Any] | None = None) -> dict[str, Any]:
        profile = extracted_profile or {}
        return {
            "name": str(getattr(agent, "name", "") or profile.get("name", "") or "").strip(),
            "party_type": str(
                getattr(agent, "party_type", "")
                or profile.get("party_type", "")
                or profile.get("type", "")
                or ""
            ).strip(),
            "representative": str(
                getattr(agent, "representative", "")
                or profile.get("representative", "")
                or ""
            ).strip(),
            "gender": str(getattr(agent, "gender", "") or profile.get("gender", "") or "").strip(),
            "birth_date": str(getattr(agent, "birth_date", "") or profile.get("birth_date", "") or "").strip(),
            "ethnicity": str(getattr(agent, "ethnicity", "") or profile.get("ethnicity", "") or "").strip(),
            "address": str(getattr(agent, "address", "") or profile.get("address", "") or "").strip(),
            "personality": str(
                getattr(agent, "personality", "") or profile.get("personality", "") or ""
            ).strip(),
            "speaking_style": str(
                getattr(agent, "speaking_style", "") or profile.get("speaking_style", "") or ""
            ).strip(),
            "interaction_guidelines": str(
                getattr(agent, "interaction_guidelines", "")
                or profile.get("interaction_guidelines", "")
                or DEFAULT_CLIENT_INTERACTION_GUIDELINES
            ).strip(),
            "legal_persona_profile": (
                getattr(agent, "legal_persona_profile", None)
                or profile.get("legal_persona_profile", {})
                or {}
            ),
        }

    @staticmethod
    def _build_lawyer_profile(lawyer: Any) -> dict[str, Any]:
        """Build the full lawyer profile dict for PromptAssembler.

        Mirrors the profile used in LawyerAgent._build_pipeline_prompt so that
        sandbox-mode scenarios and pipeline-mode scenarios produce identical
        system prompts (including interaction_guidelines).
        """
        return {
            "name": getattr(lawyer, "name", ""),
            "seniority": "从业十余年的执业律师",
            "personality": "沉稳干练，具备极强的同理心；不仅提供专业建议，更是客户的情绪稳定剂",
            "speaking_style": "坚定温和，口语化表达；引用法条时用白话解释实际影响",
            "law_firm": getattr(lawyer, "law_firm", ""),
            "specialty": getattr(lawyer, "specialty_areas", []),
            "interaction_guidelines": (
                "[核心交互准则]\n"
                "1. 情绪安抚：在法律咨询、文书沟通等非庭审场景，回答核心诉求前可先用1-2句话安抚或认可对方情境；但在庭审场景（CI/CIA）中，不得以安抚当事人情绪作为开头，应直接围绕审判长指令、案件争点和证据发言。\n"
                "2. 拒绝机械宣讲：绝对不要分点1.2.3.4回答、不要列小标题、不要像提纲或模板答案；禁止使用 Markdown 标题、加粗星号样式、星号列表、表格、代码块。\n"
                "3. 信息切块：单次回复控制在200字内，不要一次性输出太多信息。\n"
                "4. 纯文本表达：不要输出括号中的动作、表情、语气描写，如“（起立）”“（沉默）”“（声音越来越小）”。"
            ),
        }

    @staticmethod
    def _normalize_case_id(case_id: str) -> str:
        case_key = str(case_id or "")
        if case_key.startswith("case_"):
            return case_key[5:]
        return case_key

    def _get_case_output_dir(self, case_id: str) -> Path:
        case_output_dir = self.output_dir / case_id
        case_output_dir.mkdir(parents=True, exist_ok=True)
        return case_output_dir

    def _get_case_trace_recorder(self, case_id: str) -> CaseAgentTraceRecorder:
        recorder = self._case_trace_recorders.get(case_id)
        case_output_dir = self._get_case_output_dir(case_id)
        if recorder is None or recorder.case_output_dir != case_output_dir.resolve():
            recorder = CaseAgentTraceRecorder(case_output_dir)
            self._case_trace_recorders[case_id] = recorder
        return recorder

    def _bind_case_stage_trace_agents(
        self,
        case_id: str,
        stage_code: str,
        stage_key: str,
        agents: list[Any],
    ) -> CaseAgentTraceRecorder:
        recorder = self._get_case_trace_recorder(case_id)
        case_output_dir = self._get_case_output_dir(case_id)
        callback = self._build_runtime_tech_callback(case_id)
        for agent in list(agents or []):
            if agent is None:
                continue
            agent_id = str(getattr(agent, "agent_id", "") or "agent").strip() or "agent"
            bind_agent_trace_context(
                agent,
                recorder=recorder,
                output_dir=case_output_dir / "_debug" / "agent_traces" / agent_id,
                stage_code=stage_code,
                stage_key=stage_key,
            )
            if hasattr(agent, "set_runtime_tech_callback"):
                agent.set_runtime_tech_callback(callback, case_id=case_id)
        return recorder

    def _build_runtime_tech_callback(self, case_id: str):
        try:
            self._runtime_tech_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._runtime_tech_loop = None

        def _callback(payload: dict[str, Any]) -> None:
            if not payload or not self.map_engine or not hasattr(self.map_engine, "broadcast_runtime_progress"):
                return
            effective_case_id = str(payload.get("case_id") or case_id or "").strip()
            if not effective_case_id:
                return
            metadata = {
                key: value
                for key, value in dict(payload).items()
                if key not in {"case_id", "phase", "message", "detail", "blocking"}
            }

            async def _broadcast() -> None:
                await self.map_engine.broadcast_runtime_progress(
                    effective_case_id,
                    phase=str(payload.get("phase") or "runtime_tech_used"),
                    message=str(payload.get("message") or "工具/技能已调用"),
                    detail=str(payload.get("detail") or ""),
                    blocking=bool(payload.get("blocking", False)),
                    metadata=metadata,
                )

            loop = self._runtime_tech_loop
            if loop is not None and loop.is_running():
                try:
                    future = asyncio.run_coroutine_threadsafe(_broadcast(), loop)
                    future.add_done_callback(self._log_runtime_tech_broadcast_result)
                    return
                except RuntimeError:
                    logger.warning("[Orchestrator] Runtime tech event loop unavailable")

        return _callback

    @staticmethod
    def _log_runtime_tech_broadcast_result(future: concurrent.futures.Future) -> None:
        try:
            future.result()
        except Exception as exc:
            logger.warning("[Orchestrator] Runtime tech broadcast failed: %s", exc)

    def _runtime_tech_strategy(self, trace_recorder: Any | None = None) -> RuntimeTechStrategy:
        return RuntimeTechStrategy(
            map_engine=self.map_engine,
            trace_recorder=trace_recorder,
        )

    async def _emit_runtime_stage_start(
        self,
        *,
        case_id: str,
        stage_code: str,
        trace_recorder: Any | None = None,
    ) -> None:
        await self._runtime_tech_strategy(trace_recorder).emit_stage_start(
            case_id=case_id,
            stage_code=stage_code,
        )

    async def _emit_runtime_stage_research(
        self,
        *,
        case_id: str,
        stage_code: str,
        case_cause: str = "",
        case_background: str = "",
        trace_recorder: Any | None = None,
    ) -> None:
        await self._runtime_tech_strategy(trace_recorder).emit_stage_research(
            case_id=case_id,
            stage_code=stage_code,
            case_cause=case_cause,
            case_background=case_background,
        )

    async def _emit_runtime_document_complete(
        self,
        *,
        case_id: str,
        stage_code: str,
        document_text: str = "",
        compare_left: str = "",
        compare_right: str = "",
        compare_labels: tuple[str, str] = ("document_a", "document_b"),
        trace_recorder: Any | None = None,
    ) -> None:
        await self._runtime_tech_strategy(trace_recorder).emit_document_complete(
            case_id=case_id,
            stage_code=stage_code,
            document_text=document_text,
            compare_left=compare_left,
            compare_right=compare_right,
            compare_labels=compare_labels,
        )

    @staticmethod
    def _resolve_stage_max_turns(stage_code: str, prod_default: int) -> int:
        return resolve_prompt_profile_max_turns(stage_code, prod_default)

    @staticmethod
    def _resolve_lc_max_turns(question_count: int, *, player_lawyer_enabled: bool = False) -> int:
        from ..scenarios.legal_consultation import LegalConsultationScenario

        base_max_turns = ScenarioOrchestrator._resolve_stage_max_turns(
            "LC",
            LegalConsultationScenario.DEFAULT_MAX_TURNS,
        )
        if not player_lawyer_enabled or question_count <= 0:
            return base_max_turns
        return max(1, min(base_max_turns, question_count, 2))

    @staticmethod
    def _consultation_display_stage_code(party_role: str) -> str:
        return "LC"

    def _get_case_role_bundle(self, case_id: str, party_role: str) -> dict[str, Any]:
        client, client_path = self._find_client_for_case(case_id, party_role=party_role)
        config = self.storage.load_agent_config(client_path) if client_path else {}
        lawyer_id = config.get("assigned_lawyer_id", "")
        lawyer = self.registry.get_agent(lawyer_id) if lawyer_id else None
        return {
            "client": client,
            "client_path": client_path,
            "config": config,
            "lawyer_id": lawyer_id,
            "lawyer": lawyer,
        }

    def _get_case_parties(self, case_id: str) -> dict[str, dict[str, Any]]:
        return {
            "plaintiff": self._get_case_role_bundle(case_id, "plaintiff"),
            "defendant": self._get_case_role_bundle(case_id, "defendant"),
        }

    def _set_shared_case_state(self, case_id: str, state: str) -> None:
        runtime: dict[str, Any] = {}
        try:
            runtime = self.storage.load_case_runtime(case_id)
        except Exception:
            runtime = {}

        for party_role in ("plaintiff", "defendant"):
            agent_path = self.storage.get_case_agent_path(case_id, party_role)
            if (agent_path / "config.yaml").exists():
                try:
                    self.storage.update_agent_field(agent_path, "case_state", state)
                except Exception as exc:
                    logger.warning(
                        "[Orchestrator] 更新%s案件状态失败: case=%s state=%s error=%s",
                        party_role,
                        case_id,
                        state,
                        exc,
                    )

        runtime.update(
            {
                "case_id": self._normalize_case_id(case_id),
                "overall_state": state,
                "plaintiff_state": state,
                "defendant_state": state,
                "active_party_role": "shared",
            }
        )
        try:
            self.storage.save_case_runtime(case_id, runtime)
        except Exception as exc:
            logger.warning("[Orchestrator] 保存共享案件运行态失败: case=%s state=%s error=%s", case_id, state, exc)

    def _get_agent_memory_payload(self, agent: Any | None, memory_owner: str) -> dict[str, Any]:
        if agent is None:
            return get_empty_memory_payload(memory_owner)
        try:
            payload, _paths = load_memory_for_agent(agent, memory_owner)
            return payload
        except Exception as exc:
            logger.warning(
                "[Orchestrator] failed to load %s memory for %s: %s",
                memory_owner,
                getattr(agent, "agent_id", agent),
                exc,
            )
            return get_empty_memory_payload(memory_owner)

    def _get_lawyer_prompt_memory(
        self,
        lawyer: Any | None,
        case_id: str,
    ) -> dict[str, Any]:
        _ = case_id
        return self._get_agent_memory_payload(lawyer, LAWYER_MEMORY_OWNER)

    def _get_client_prompt_memory(
        self,
        client: Any | None,
        case_id: str,
    ) -> dict[str, Any]:
        _ = case_id
        return self._get_agent_memory_payload(client, CLIENT_MEMORY_OWNER)

    @staticmethod
    def _extract_config_profile(config: dict[str, Any] | None) -> dict[str, Any]:
        profile = (config or {}).get("profile", {}) or {}
        return profile if isinstance(profile, dict) else {}

    @staticmethod
    def _extract_memory_text(memory_payload: dict[str, Any] | None, key: str) -> str:
        current: Any = memory_payload if isinstance(memory_payload, dict) else {}
        for part in str(key or "").split("."):
            part = part.strip()
            if not part or not isinstance(current, dict):
                return ""
            current = current.get(part, "")
        return str(current or "").strip()

    @staticmethod
    def _has_meaningful_long_term_memory(memory_payload: Any) -> bool:
        return has_meaningful_memory(memory_payload)

    def _resolve_lawyer_case_background(
        self,
        default_background: Any,
        *,
        long_term_memory: dict[str, Any] | None,
    ) -> str:
        if self._has_meaningful_long_term_memory(long_term_memory or {}):
            return ""
        return self._stringify_prompt_value(default_background, fallback="")

    def _build_case_party_context(
        self,
        case_id: str,
        *,
        party_role: str,
        case: dict[str, Any] | None = None,
        default_case_background: Any = "",
        default_claims: Any = "",
        default_evidence: Any = "",
    ) -> dict[str, str]:
        normalized_case_id = self._normalize_case_id(case_id)
        storage = getattr(self, "storage", None)
        if storage is None:
            extracted_info = (case or {}).get("extracted_info", {}) or {}
            return {
                "plaintiff_name": "",
                "plaintiff_gender": "",
                "plaintiff_birth_date": "",
                "plaintiff_ethnicity": "",
                "plaintiff_address": "",
                "plaintiff_representative": "",
                "defendant_name": "",
                "defendant_gender": "",
                "defendant_birth_date": "",
                "defendant_ethnicity": "",
                "defendant_address": "",
                "defendant_representative": "",
                "case_background": self._stringify_prompt_value(
                    default_case_background or extracted_info.get("case_background", ""),
                    fallback="",
                ),
                "claims": self._stringify_prompt_value(default_claims, fallback=""),
                "evidence": self._stringify_prompt_value(default_evidence, fallback=""),
            }

        plaintiff_path = storage.get_case_agent_path(normalized_case_id, "plaintiff")
        defendant_path = storage.get_case_agent_path(normalized_case_id, "defendant")

        try:
            plaintiff_config = storage.load_agent_config(plaintiff_path)
        except Exception:
            plaintiff_config = {}
        try:
            defendant_config = storage.load_agent_config(defendant_path)
        except Exception:
            defendant_config = {}

        extracted_info = (case or {}).get("extracted_info", {}) or {}
        party_info = extracted_info.get("party_info", {}) or {}
        extracted_plaintiff = DataLoader.normalize_party_profile(party_info.get("plaintiff", {}) or {})
        extracted_defendant = party_info.get("defendant", {}) or {}
        if isinstance(extracted_defendant, list):
            extracted_defendant = extracted_defendant[0] if extracted_defendant else {}
        extracted_defendant = DataLoader.normalize_party_profile(extracted_defendant)

        plaintiff_profile = self._extract_config_profile(plaintiff_config)
        defendant_profile = self._extract_config_profile(defendant_config)
        plaintiff_agent, _ = self._find_client_for_case(normalized_case_id, party_role="plaintiff")
        defendant_agent, _ = self._find_client_for_case(normalized_case_id, party_role="defendant")
        plaintiff_memory = self._get_agent_memory_payload(plaintiff_agent, CLIENT_MEMORY_OWNER)
        defendant_memory = self._get_agent_memory_payload(defendant_agent, CLIENT_MEMORY_OWNER)
        current_memory = plaintiff_memory if party_role == "plaintiff" else defendant_memory

        plaintiff_name = str(
            plaintiff_profile.get("name")
            or extracted_plaintiff.get("name")
            or ""
        ).strip()
        defendant_name = str(
            defendant_profile.get("name")
            or extracted_defendant.get("name")
            or ""
        ).strip()

        case_background = (
            self._extract_memory_text(current_memory, "case_knowledge.self_narrative")
            or self._extract_memory_text(plaintiff_memory, "case_knowledge.self_narrative")
            or self._extract_memory_text(defendant_memory, "case_knowledge.self_narrative")
            or self._stringify_prompt_value(default_case_background, fallback="")
            or str(extracted_info.get("case_background", "") or "").strip()
        )
        claims = (
            self._extract_memory_text(current_memory, "demands.core_demands")
            or self._extract_memory_text(plaintiff_memory, "demands.core_demands")
            or self._extract_memory_text(defendant_memory, "demands.core_demands")
            or self._stringify_prompt_value(default_claims, fallback="")
        )
        evidence = self._stringify_prompt_value(default_evidence, fallback="")

        return {
            "plaintiff_name": plaintiff_name,
            "plaintiff_gender": str(plaintiff_profile.get("gender", "") or extracted_plaintiff.get("gender", "") or "").strip(),
            "plaintiff_birth_date": str(plaintiff_profile.get("birth_date", "") or extracted_plaintiff.get("birth_date", "") or "").strip(),
            "plaintiff_ethnicity": str(plaintiff_profile.get("ethnicity", "") or extracted_plaintiff.get("ethnicity", "") or "").strip(),
            "plaintiff_address": str(plaintiff_profile.get("address", "") or extracted_plaintiff.get("address", "") or "").strip(),
            "plaintiff_representative": str(plaintiff_profile.get("representative", "") or extracted_plaintiff.get("representative", "") or "").strip(),
            "defendant_name": defendant_name,
            "defendant_gender": str(defendant_profile.get("gender", "") or extracted_defendant.get("gender", "") or "").strip(),
            "defendant_birth_date": str(defendant_profile.get("birth_date", "") or extracted_defendant.get("birth_date", "") or "").strip(),
            "defendant_ethnicity": str(defendant_profile.get("ethnicity", "") or extracted_defendant.get("ethnicity", "") or "").strip(),
            "defendant_address": str(defendant_profile.get("address", "") or extracted_defendant.get("address", "") or "").strip(),
            "defendant_representative": str(defendant_profile.get("representative", "") or extracted_defendant.get("representative", "") or "").strip(),
            "case_background": case_background,
            "claims": claims,
            "evidence": evidence,
        }

    def _resolve_map_prefix_from_lawyer(self, lawyer: Any) -> str:
        firm_id = str(getattr(lawyer, "firm_id", "") or "").lower()
        if firm_id in {"law_firm_b", "lawfirmb"}:
            return "lawfirmB"
        return "lawfirmA"

    def _build_player_lawyer_adapter(self, lawyer: Any, *, case_id: str, stage: str) -> Any:
        from ..player_lawyer.agent import PlayerLawyerAgent

        gateway = getattr(self, "_player_gateway", None)
        if gateway is None:
            return lawyer
        adapter = PlayerLawyerAgent(
            agent_id=getattr(lawyer, "agent_id", ""),
            name=getattr(lawyer, "name", "辩护律师"),
            party_role="defendant",   # 刑事玩家模式：玩家扮演辩护律师
            law_firm=getattr(lawyer, "law_firm", ""),
            firm_id=getattr(lawyer, "firm_id", ""),
            gateway=gateway,
            case_id=case_id,
            sandbox_id=getattr(self, "_sandbox_id", 0),
            broadcast_fn=getattr(self, "_player_broadcast_fn", None),
        )
        adapter.config_path = getattr(lawyer, "config_path", None)
        adapter.storage = getattr(lawyer, "storage", None)
        adapter.set_stage(stage)
        return adapter

    def _player_lawyer_mode(self) -> str:
        map_engine = getattr(self, "map_engine", None)
        frontend_mode = getattr(map_engine, "_frontend_mode", None)
        supports_player_v2 = False
        supports_fn = getattr(map_engine, "supports_player_v2_runtime", None)
        if callable(supports_fn):
            supports_player_v2 = bool(supports_fn())
        return player_lawyer_mode_for_frontend(
            frontend_mode=frontend_mode,
            has_player_v2_client=supports_player_v2,
        )

    def _player_defense_lawyer_enabled(self) -> bool:
        # 纯刑事：玩家模式只支持扮演辩护律师
        return self._player_lawyer_mode() == "defendant"

    def _player_ai_surrogate_enabled(self) -> bool:
        return player_lawyer_ai_surrogate_enabled()

    @staticmethod
    def _resolve_birth_location_for_map_prefix(map_prefix: str) -> str:
        return "birth_locationB" if str(map_prefix).lower().endswith("b") else "birth_locationA"

    def _get_birth_location_for_agent(self, agent_id: str) -> str:
        if self.map_engine:
            state = getattr(self.map_engine, "_agent_states", {}).get(agent_id, {})
            birth_loc_id = state.get("birth_loc_id")
            if birth_loc_id:
                return birth_loc_id

        agent = self.registry.get_agent(agent_id)
        if not agent:
            return "birth_locationA"

        agent_type = getattr(agent, "agent_type", "")
        if agent_type == "judge":
            return "birth_locationB"
        if agent_type == "lawyer":
            return self._resolve_birth_location_for_map_prefix(
                self._resolve_map_prefix_from_lawyer(agent)
            )
        if getattr(agent, "config_path", None):
            try:
                config = self.storage.load_agent_config(agent.config_path)
                if config.get("party_role") == "defendant":
                    return "birth_locationB"
            except Exception:
                pass
        return "birth_locationA"

    @staticmethod
    def _match_party_name(target_name: str, candidate_names: list[str]) -> bool:
        normalized_target = str(target_name or "").strip()
        if not normalized_target:
            return False
        return any(
            candidate and (candidate in normalized_target or normalized_target in candidate)
            for candidate in candidate_names
        )

    def _collect_case_participant_ids(self, case_id: str, extra_ids: list[str] | None = None) -> list[str]:
        participant_ids: list[str] = []
        for role_bundle in self._get_case_parties(case_id).values():
            client = role_bundle.get("client")
            lawyer_id = role_bundle.get("lawyer_id", "")
            if client:
                participant_ids.append(client.agent_id)
            if lawyer_id:
                participant_ids.append(lawyer_id)
        if extra_ids:
            participant_ids.extend([agent_id for agent_id in extra_ids if agent_id])

        deduped: list[str] = []
        seen: set[str] = set()
        for agent_id in participant_ids:
            if agent_id and agent_id not in seen:
                seen.add(agent_id)
                deduped.append(agent_id)
        return deduped

    def _get_or_assign_character_name(self, agent: Any) -> str:
        configured = str(getattr(agent, "character_name", "") or "").strip()
        if configured:
            return configured

        config_path = getattr(agent, "config_path", None)
        if config_path:
            try:
                config = self.storage.load_agent_config(config_path)
                configured = str(config.get("character_name", "") or "").strip()
                if configured:
                    setattr(agent, "character_name", configured)
                    return configured
            except Exception:
                pass

        configured = random.choice(CHARACTER_POOL)
        setattr(agent, "character_name", configured)
        if config_path:
            try:
                self.storage.update_agent_field(config_path, "character_name", configured)
            except Exception as exc:
                logger.debug("[Orchestrator] Failed to persist character name for %s: %s", getattr(agent, "agent_id", ""), exc)
        return configured

    def _get_character_name_for_client(self, client: Any, party_role: str) -> str:
        del party_role
        return self._get_or_assign_character_name(client)

    def _get_character_name_for_lawyer(self, lawyer: Any) -> str:
        return self._get_or_assign_character_name(lawyer)

    def _find_client_for_case(self, case_id: str, party_role: str = "plaintiff") -> tuple:
        """Find the client agent for a given case_id and party_role.

        Returns:
            (client_agent, client_config_path) or (None, None)
        """
        from pathlib import Path as PathType

        # Priority 1: Try new case-based structure path
        case_agent_path = self.storage.get_case_agent_path(case_id, party_role)
        if (case_agent_path / "config.yaml").exists():
            # Find agent by path
            for client in self.registry.get_agents_by_type("client"):
                if not client.config_path:
                    continue
                client_path = PathType(client.config_path)
                client_dir = client_path.parent if client_path.name == "config.yaml" else client_path
                if client_dir == case_agent_path:
                    return client, client.config_path

        # Priority 2: Search through all registered clients (legacy support)
        normalized_case_id = self._normalize_case_id(case_id)
        for client in self.registry.get_agents_by_type("client"):
            if not client.config_path:
                continue
            config = self.storage.load_agent_config(client.config_path)
            if config.get("party_role") != party_role:
                continue
            config_case_id = self._normalize_case_id(config.get("case_id", ""))
            if config_case_id and config_case_id == normalized_case_id:
                return client, client.config_path
        return None, None

    @staticmethod
    def _build_consultation_case_summary(self, data_loader, case: dict[str, Any]) -> str:
        """构建委托洽谈阶段的"案情概览"：只含罪名/被羁押人/强制措施/简要背景，
        让当事人做概括陈述，不一次性注入完整案情与证据。"""
        info = (case or {}).get("extracted_info", {}) or {}
        if not isinstance(info, dict):
            info = {}
        charge = str(info.get("charge") or info.get("case_cause") or "").strip()
        defendant = (info.get("party_info", {}) or {}).get("defendant", {}) or {}
        defendant_name = str(defendant.get("name", "") or "").strip()
        measures = info.get("compulsory_measures", {}) or {}
        if not isinstance(measures, dict):
            measures = {}
        custody_parts = []
        for key in ("detention", "arrest", "custody_status"):
            value = str(measures.get(key, "") or "").strip()
            if value:
                custody_parts.append(value)

        parts = []
        if defendant_name:
            parts.append(f"被羁押人：{defendant_name}")
        if charge:
            parts.append(f"涉嫌罪名：{charge}")
        if custody_parts:
            parts.append("强制措施：" + "、".join(custody_parts))
        return "；".join(parts)

    def _stringify_prompt_value(value: Any, fallback: str = "（暂无）") -> str:
        if isinstance(value, list):
            cleaned = [str(item).strip() for item in value if str(item).strip()]
            if not cleaned:
                return fallback
            return "\n".join(f"{idx + 1}. {item}" for idx, item in enumerate(cleaned))

        if isinstance(value, dict):
            cleaned = {
                key: item for key, item in value.items()
                if item not in (None, "", [], {})
            }
            if not cleaned:
                return fallback
            return json.dumps(cleaned, ensure_ascii=False, indent=2)

        text = str(value or "").strip()
        return text or fallback

    def _save_result(self, case_id: str, stage: str, result: dict) -> None:
        """Save scenario result to sandbox_data/output/{case_id}/."""
        case_output = self.output_dir / case_id
        case_output.mkdir(parents=True, exist_ok=True)
        filepath = case_output / f"{stage}_result.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"[Orchestrator] Saved {stage} result to {filepath}")

    def _load_consultation_history(self, case_id: str, stage: str = "PLC") -> list[dict[str, Any]]:
        case_output_dir = self._get_case_output_dir(case_id)
        stage_code = str(stage or "PLC").strip().upper()
        candidate_stages = [stage_code]
        if stage_code == "PLC":
            candidate_stages.append("LC")

        for candidate_stage in candidate_stages:
            result_path = case_output_dir / f"{candidate_stage}_result.json"
            if not result_path.exists():
                continue
            try:
                with result_path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                dialog_history = payload.get("dialog_history", [])
                if isinstance(dialog_history, list):
                    return dialog_history
            except Exception as exc:
                logger.warning("[Orchestrator] 读取咨询记录失败: case=%s stage=%s error=%s", case_id, candidate_stage, exc)
        return []

    def _collect_stage_prompts(
        self,
        case_id: str,
        stage: str,
        *agents: Any,
        reset: bool = False,
    ) -> None:
        """Persist stage-scoped system prompts into one output JSON file."""
        case_output_dir = self._get_case_output_dir(case_id)
        filepath = case_output_dir / "system_prompt.json"
        export_data: dict[str, Any] = {"case_id": case_id, "stages": {}}

        if filepath.exists():
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    export_data.update(loaded)
            except Exception as e:
                logger.warning(f"[Orchestrator] 读取 system_prompt.json 失败，将重建文件: {e}")

        stages = export_data.setdefault("stages", {})
        if reset or stage not in stages or not isinstance(stages.get(stage), dict):
            stages[stage] = {
                "stage_code": stage,
                "stage_name": self.STAGE_DISPLAY_NAMES.get(stage, stage),
                "agents": [],
            }

        stage_entry = stages[stage]
        existing_agents = {
            agent_info.get("agent_id"): agent_info
            for agent_info in stage_entry.get("agents", [])
            if isinstance(agent_info, dict) and agent_info.get("agent_id")
        }

        for agent in agents:
            if not agent or not hasattr(agent, "get_prompt_info"):
                continue
            prompt_info = agent.get_prompt_info()
            if not prompt_info.get("system_prompt"):
                continue
            existing_agents[prompt_info["agent_id"]] = prompt_info

        now = datetime.now().isoformat()
        stage_entry["agents"] = list(existing_agents.values())
        stage_entry["updated_at"] = now
        export_data["updated_at"] = now

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)
        logger.info(f"[Orchestrator] 已更新阶段 system prompt 汇总: {filepath}")

    async def _checkpoint_stage_memories(
        self,
        *,
        case_id: str,
        stage_code: str,
        stage_label: str,
        agents: list[Any],
    ) -> None:
        """Persist stage memories without leaving the frontend in a silent gap."""
        checkpoints = [
            self._build_memory_checkpoint_event(agent)
            for agent in list(agents or [])
            if agent is not None and hasattr(agent, "extract_and_save_long_term_memory")
        ]
        checkpoints = [item for item in checkpoints if item]
        if not checkpoints:
            return

        tool_names = self._memory_checkpoint_tool_names(checkpoints)
        skill_names = self._memory_checkpoint_skill_names(checkpoints)

        if self.map_engine and hasattr(self.map_engine, "broadcast_runtime_progress"):
            try:
                await self.map_engine.broadcast_runtime_progress(
                    case_id,
                    phase="memory_checkpoint",
                    message=f"{stage_label or stage_code}已结束，正在整理阶段材料",
                    detail="整理完成后会自动进入下一阶段",
                    blocking=False,
                    metadata={
                        "stage": stage_code,
                        "scenario_type": stage_code,
                        "agent_count": len(checkpoints),
                        "memory_events": self._public_memory_checkpoint_events(checkpoints),
                        "tool_names": tool_names,
                        "skill_names": skill_names,
                        "active_tool_names": tool_names,
                        "active_skill_names": skill_names,
                    },
                )
            except Exception as exc:
                logger.warning("[Orchestrator] 广播阶段记忆整理进度失败: %s", exc)

        results = await asyncio.gather(
            *[
                asyncio.to_thread(item["agent"].extract_and_save_long_term_memory)
                for item in checkpoints
            ],
            return_exceptions=True,
        )
        for item, result in zip(checkpoints, results):
            if isinstance(result, Exception):
                item["status"] = "failed"
                item["error"] = str(result)
                logger.error(
                    "[Orchestrator] %s 阶段记忆整理失败: agent=%s error=%s",
                    stage_code,
                    item.get("agent_id") or item.get("agent_name"),
                    result,
                )
                continue
            item["status"] = "completed" if isinstance(result, dict) else "skipped"
            after_payload = result if isinstance(result, dict) else {}
            item["changed_fields"] = self._memory_changed_fields(item.get("before_payload"), after_payload)
            item["changed_count"] = len(item["changed_fields"])

        memory_events = self._public_memory_checkpoint_events(checkpoints)
        changed_total = sum(int(item.get("changed_count") or 0) for item in memory_events)
        checked_count = sum(1 for item in memory_events if item.get("status") == "completed")

        if self.map_engine and hasattr(self.map_engine, "broadcast_runtime_progress"):
            try:
                await self.map_engine.broadcast_runtime_progress(
                    case_id,
                    phase="memory_checkpoint_complete",
                    message=f"{stage_label or stage_code}阶段材料整理完成",
                    detail=f"已检查 {checked_count} 个长期记忆槽，更新 {changed_total} 个字段",
                    blocking=False,
                    metadata={
                        "stage": stage_code,
                        "scenario_type": stage_code,
                        "agent_count": len(memory_events),
                        "checked_count": checked_count,
                        "changed_count": changed_total,
                        "memory_events": memory_events,
                        "tool_names": tool_names,
                        "skill_names": skill_names,
                        "active_tool_names": tool_names,
                        "active_skill_names": skill_names,
                    },
                )
            except Exception as exc:
                logger.warning("[Orchestrator] 广播阶段记忆整理完成失败: %s", exc)

    def _build_memory_checkpoint_event(self, agent: Any) -> dict[str, Any]:
        owner = self._memory_owner_for_agent(agent)
        before_payload: dict[str, Any] = {}
        if owner:
            try:
                before_payload, _paths = load_memory_for_agent(agent, owner)
            except Exception as exc:
                logger.warning(
                    "[Orchestrator] 读取记忆 checkpoint 前置状态失败: agent=%s owner=%s error=%s",
                    getattr(agent, "agent_id", getattr(agent, "name", "")),
                    owner,
                    exc,
                )
        return {
            "agent": agent,
            "agent_id": str(getattr(agent, "agent_id", "") or ""),
            "agent_name": str(getattr(agent, "name", "") or getattr(agent, "agent_id", "") or "未知 Agent"),
            "owner": owner,
            "owner_label": self._memory_owner_label(owner),
            "status": "pending",
            "changed_fields": [],
            "changed_count": 0,
            "before_payload": before_payload,
        }

    @staticmethod
    def _public_memory_checkpoint_events(checkpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
        public_keys = {
            "agent_id",
            "agent_name",
            "owner",
            "owner_label",
            "status",
            "changed_fields",
            "changed_count",
            "error",
        }
        return [
            {key: value for key, value in item.items() if key in public_keys and value not in (None, "")}
            for item in checkpoints
        ]

    @staticmethod
    def _memory_owner_for_agent(agent: Any) -> str:
        if hasattr(agent, "legal_profile"):
            return LAWYER_MEMORY_OWNER
        if hasattr(agent, "client_profile"):
            return CLIENT_MEMORY_OWNER
        return ""

    @staticmethod
    def _memory_owner_label(owner: str) -> str:
        if owner == LAWYER_MEMORY_OWNER:
            return "律师长期记忆"
        if owner == CLIENT_MEMORY_OWNER:
            return "当事人长期记忆"
        return "运行代理记忆"

    @staticmethod
    def _memory_changed_fields(before_payload: Any, after_payload: Any) -> list[str]:
        before_flat = flatten_memory_payload(before_payload or {})
        after_flat = flatten_memory_payload(after_payload or {})
        fields = sorted(set(before_flat) | set(after_flat))
        return [field for field in fields if before_flat.get(field, "") != after_flat.get(field, "")]

    @staticmethod
    def _memory_checkpoint_tool_names(checkpoints: list[dict[str, Any]]) -> list[str]:
        tools: set[str] = set()
        owners = {str(item.get("owner") or "") for item in checkpoints}
        if LAWYER_MEMORY_OWNER in owners:
            tools.update({LAWYER_LOAD_TOOL_NAME, LAWYER_SAVE_TOOL_NAME})
        if CLIENT_MEMORY_OWNER in owners:
            tools.update({CLIENT_LOAD_TOOL_NAME, CLIENT_SAVE_TOOL_NAME})
        return sorted(tools)

    @staticmethod
    def _memory_checkpoint_skill_names(checkpoints: list[dict[str, Any]]) -> list[str]:
        skills: set[str] = set()
        owners = {str(item.get("owner") or "") for item in checkpoints}
        if LAWYER_MEMORY_OWNER in owners:
            skills.add("lawyer-memory-writing")
        if CLIENT_MEMORY_OWNER in owners:
            skills.add("client-memory-writing")
        return sorted(skills)

    def _mark_case_stage_active(
        self,
        case_id: str,
        scenario_type: str,
        participant_ids: list[str],
        display_stage_code: str = "",
    ) -> None:
        participant_ids = [pid for pid in participant_ids if pid]
        if not participant_ids:
            return

        self.event_bus.register_active_scenario(case_id, scenario_type, participant_ids)
        if self.checkpoint_manager:
            self.checkpoint_manager.sync_active_scenarios_from_event_bus()
        if self.map_engine and hasattr(self.map_engine, "broadcast_scenario_start"):
            frontend_stage_code = str(display_stage_code or scenario_type or "").strip().upper()
            asyncio.get_running_loop().create_task(
                self.map_engine.broadcast_scenario_start(case_id, frontend_stage_code, participant_ids)
            )

    def _clear_case_stage_active(self, case_id: str) -> None:
        active_snapshot = self.event_bus.get_active_scenarios_snapshot().get(case_id, {})
        scenario_type = str(active_snapshot.get("scenario_type", "") or "")
        self.event_bus.unregister_active_scenario(case_id)
        if self.checkpoint_manager:
            self.checkpoint_manager.sync_active_scenarios_from_event_bus()
        if scenario_type and self.map_engine and hasattr(self.map_engine, "broadcast_scenario_end"):
            asyncio.get_running_loop().create_task(
                self.map_engine.broadcast_scenario_end(case_id, scenario_type)
            )

    @staticmethod
    def _normalize_firm_id(firm_id: str) -> str:
        key = str(firm_id or "").strip().lower()
        if key in {"lawfirma", "law_firm_a"}:
            return "law_firm_A"
        if key in {"lawfirmb", "law_firm_b"}:
            return "law_firm_B"
        return firm_id

    def _get_available_firm_ids(self) -> list[str]:
        firm_ids: list[str] = []
        for firm_id in self.registry._firms.keys():
            normalized = self._normalize_firm_id(str(firm_id))
            if normalized and normalized not in firm_ids:
                firm_ids.append(normalized)
        return firm_ids or ["law_firm_A", "law_firm_B"]

    def _resolve_map_prefix_from_firm(self, firm_id: str) -> str:
        normalized = self._normalize_firm_id(firm_id)
        if normalized == "law_firm_B":
            return "lawfirmB"
        return "lawfirmA"

    def _choose_case_firm(
        self,
        *,
        config_path: str | None = None,
        preferred_firm: str = "",
        force_random: bool = False,
    ) -> tuple[str, str]:
        firms = self._get_available_firm_ids()
        normalized_preferred = self._normalize_firm_id(preferred_firm)
        if force_random or normalized_preferred not in firms:
            target_firm = random.choice(firms)
        else:
            target_firm = normalized_preferred

        if config_path and target_firm:
            try:
                self.storage.update_agent_field(config_path, "assigned_firm", target_firm)
            except Exception as exc:
                logger.warning("[Orchestrator] 更新 assigned_firm 失败: %s", exc)

        return target_firm, self._resolve_map_prefix_from_firm(target_firm)

    def _infer_case_firm_for_defendant(self, payload: dict, plaintiff_config: dict) -> str:
        candidates: list[str] = [
            str(payload.get("firm_id", "") or ""),
            str(payload.get("target_firm", "") or ""),
            str(plaintiff_config.get("assigned_firm", "") or ""),
        ]

        plaintiff_lawyer_id = str(
            plaintiff_config.get("assigned_lawyer_id", "") or payload.get("lawyer_id", "") or ""
        ).strip()
        if plaintiff_lawyer_id:
            lawyer = self.registry.get_agent(plaintiff_lawyer_id) if self.registry else None
            candidates.append(str(getattr(lawyer, "firm_id", "") or ""))

        available_firms = set(self._get_available_firm_ids())
        for candidate in candidates:
            normalized = self._normalize_firm_id(candidate)
            if normalized in available_firms:
                return normalized
        return ""

    def _select_available_judge(self, court_level: str, case_id: str, preferred_judge_id: str = "") -> Any | None:
        judges = [
            judge
            for judge in self.registry.get_agents_by_type("judge")
            if getattr(judge, "court_level", "") == court_level
        ]
        if not judges:
            return None

        if preferred_judge_id:
            preferred = next((judge for judge in judges if judge.agent_id == preferred_judge_id), None)
            if preferred and self._is_judge_available(preferred.agent_id, case_id):
                return preferred

        for judge in judges:
            if self._is_judge_available(judge.agent_id, case_id):
                return judge
        return None

    def _is_judge_available(self, judge_id: str, case_id: str) -> bool:
        reservation_case_id = self._judge_reservations.get(judge_id)
        if reservation_case_id and reservation_case_id != case_id:
            return False
        if self.event_bus.is_agent_busy(judge_id):
            return False

        judge = self.registry.get_agent(judge_id)
        current_case_id = getattr(judge, "current_handling_case", None) if judge else None
        if current_case_id and current_case_id != case_id:
            return False
        return True

    def _reserve_trial_resources(self, court: str, case_id: str, judge_id: str) -> None:
        self._court_reservations[court] = case_id
        self._judge_reservations[judge_id] = case_id
        judge = self.registry.get_agent(judge_id)
        if judge and getattr(judge, "config_path", None):
            try:
                self.storage.update_agent_field(judge.config_path, "current_handling_case", case_id)
            except Exception as exc:
                logger.warning("[Orchestrator] failed to reserve judge %s for %s: %s", judge_id, case_id, exc)

    async def _release_trial_slot(self, court: str, case_id: str) -> None:
        next_dispatch: tuple[EventType, dict[str, Any]] | None = None

        async with self._resource_lock:
            if self._court_reservations.get(court) == case_id:
                self._court_reservations.pop(court, None)

            released_judge_ids = [
                judge_id
                for judge_id, reserved_case_id in list(self._judge_reservations.items())
                if reserved_case_id == case_id
            ]
            for judge_id in released_judge_ids:
                self._judge_reservations.pop(judge_id, None)
                judge = self.registry.get_agent(judge_id)
                if judge and getattr(judge, "config_path", None):
                    try:
                        self.storage.update_agent_field(judge.config_path, "current_handling_case", None)
                    except Exception as exc:
                        logger.warning("[Scheduler] failed to release judge %s: %s", judge_id, exc)

            queue = self._trial_queues[court]
            while queue:
                next_item = queue.popleft()
                judge = self._select_available_judge(
                    next_item["court_level"],
                    next_item["case_id"],
                    str(next_item["payload"].get("judge_id", "") or ""),
                )
                if not judge:
                    queue.appendleft(next_item)
                    break

                self._reserve_trial_resources(court, next_item["case_id"], judge.agent_id)
                next_dispatch = (
                    next_item["event_type"],
                    {**next_item["payload"], "judge_id": judge.agent_id},
                )
                break

        if next_dispatch:
            logger.info("[Scheduler] releasing %s triggered queued case %s", court, next_dispatch[1].get("case_id", ""))
            await self.event_bus.publish(next_dispatch[0], next_dispatch[1])

    @staticmethod
    def _sanitize_bubble_text(content: Any, max_length: int = 80) -> str:
        text = str(content or "").strip()
        if not text:
            return ""

        text = text.replace("【起草结束】", "").replace("【提取结束】", "").strip()
        text = text.replace("```json", "").replace("```", "").strip()
        text = " ".join(text.split())
        if len(text) > max_length:
            text = text[: max_length - 1].rstrip() + "…"
        return text

    async def _play_dialog_bubbles(
        self,
        dialog_history: list[dict[str, Any]],
        role_to_agent_id: dict[str, str],
        role_to_location_id: dict[str, str] | None = None,
        role_to_direction: dict[str, str] | None = None,
        gap: float = 0.9,
    ) -> None:
        if not self.map_engine or not dialog_history:
            return

        await self._prepare_dialogue_agents(
            role_to_agent_id,
            role_to_location_id=role_to_location_id,
            role_to_direction=role_to_direction,
        )

        for entry in dialog_history:
            if not await self._show_dialog_entry_bubble(entry, role_to_agent_id):
                return
            await asyncio.sleep(gap)

    async def _show_dialog_entry_bubble(
        self,
        entry: dict[str, Any],
        role_to_agent_id: dict[str, str],
    ) -> bool:
        if not self.map_engine:
            return False

        agent_id = role_to_agent_id.get(str(entry.get("role", "") or ""))
        if not agent_id:
            return True

        bubble_text = self._sanitize_bubble_text(entry.get("content", ""))
        if not bubble_text:
            return True

        duration = min(2.6, max(1.4, len(bubble_text) * 0.04))
        try:
            await self.map_engine.show_bubble(agent_id, bubble_text, duration)
        except Exception as exc:
            logger.warning("[Orchestrator] 显示气泡失败: agent=%s, error=%s", agent_id, exc)
            return False
        return True

    async def _broadcast_dialog_entry(
        self,
        case_id: str,
        entry: dict[str, Any],
        role_to_agent_id: dict[str, str],
        turn: int,
        scenario_type: str = "",
    ) -> None:
        if not self.map_engine or not hasattr(self.map_engine, "broadcast_dialogue"):
            return

        role = str(entry.get("role", "") or "")
        content = str(entry.get("content", "") or "").strip()
        agent_id = role_to_agent_id.get(role, "")
        if not case_id or not agent_id or not content:
            return

        speaker_name = role
        agent = self.registry.get_agent(agent_id) if self.registry else None
        if agent and getattr(agent, "name", ""):
            speaker_name = str(agent.name)

        marker = build_player_responsibility_marker(
            role=role,
            stage=scenario_type or entry.get("scenario_type", ""),
            player_lawyer_enabled=self._player_defense_lawyer_enabled(),
            ai_surrogate_enabled=self._player_ai_surrogate_enabled(),
            content=content,
        )

        await self.map_engine.broadcast_dialogue(
            case_id,
            agent_id,
            speaker_name,
            content,
            turn,
            scenario_type=scenario_type,
            generation_duration_seconds=entry.get("generation_duration_seconds"),
            generation_total_tokens=entry.get("generation_total_tokens"),
            **(marker or {}),
        )


    async def _ensure_agent_visualized(
        self,
        agent_id: str,
        role_hint: str = "",
        location_id: str = "",
        direction: str = "down",
    ) -> None:
        if not self.map_engine or not agent_id:
            return

        agent = self.registry.get_agent(agent_id)
        if not agent:
            return

        if agent_id not in getattr(self.map_engine, "_agent_states", {}):
            if role_hint in {"plaintiff", "defendant"}:
                character_name = self._get_character_name_for_client(agent, role_hint)
            else:
                character_name = self._get_character_name_for_lawyer(agent)
            await self.map_engine.spawn_agent(
                agent_id=agent_id,
                name=agent.name,
                character_name=character_name,
                birth_loc_id=self._get_birth_location_for_agent(agent_id),
                role=role_hint,
            )

        if location_id:
            await self.map_engine.move_to_location(agent_id, location_id)
            await self._stand_agent_on_location(
                agent_id,
                location_id,
                direction=direction,
            )

    async def _prepare_dialogue_agents(
        self,
        role_to_agent_id: dict[str, str],
        role_to_location_id: dict[str, str] | None = None,
        role_to_direction: dict[str, str] | None = None,
    ) -> None:
        if not self.map_engine:
            return

        role_to_location_id = role_to_location_id or {}
        role_to_direction = role_to_direction or {}

        for role, agent_id in role_to_agent_id.items():
            await self._ensure_agent_visualized(
                agent_id,
                role_hint=role,
                location_id=role_to_location_id.get(role, ""),
                direction=role_to_direction.get(role, "down"),
            )

    async def _run_sync_scenario_with_live_bubbles(
        self,
        case_id: str,
        scenario_factory: Callable[[Callable[[str, str], None] | None], Any],
        role_to_agent_id: dict[str, str],
        role_to_location_id: dict[str, str] | None = None,
        role_to_direction: dict[str, str] | None = None,
        gap: float = 0.9,
        trace_recorder: CaseAgentTraceRecorder | None = None,
        trace_stage_code: str = "",
        trace_stage_key: str = "",
        trace_agents: list[Any] | None = None,
        trace_result_path: str | Path | None = None,
    ) -> dict[str, Any]:
        def _attach_trace(scenario: Any) -> None:
            if scenario is None or trace_recorder is None:
                return
            setattr(scenario, "trace_recorder", trace_recorder)
            setattr(scenario, "trace_stage_code", str(trace_stage_code or "").strip().upper())
            setattr(
                scenario,
                "trace_stage_key",
                str(trace_stage_key or trace_stage_code or "").strip().upper(),
            )

        if not self.map_engine:
            scenario = scenario_factory(None)
            _attach_trace(scenario)
            try:
                result = await asyncio.to_thread(scenario.execute)
            except Exception as exc:
                if trace_recorder is not None and trace_stage_code:
                    trace_recorder.export_stage(
                        stage_code=trace_stage_code,
                        stage_key=trace_stage_key or trace_stage_code,
                        agents=list(trace_agents or []),
                        stage_result=None,
                        stage_result_path=trace_result_path,
                        status="failed",
                        error=repr(exc),
                    )
                raise
            if trace_recorder is not None and trace_stage_code:
                trace_recorder.export_stage(
                    stage_code=trace_stage_code,
                    stage_key=trace_stage_key or trace_stage_code,
                    agents=list(trace_agents or []),
                    stage_result=result,
                    stage_result_path=trace_result_path,
                    status="completed",
                )
            return result

        await self._prepare_dialogue_agents(
            role_to_agent_id,
            role_to_location_id=role_to_location_id,
            role_to_direction=role_to_direction,
        )

        bubble_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        async def consume_bubbles() -> None:
            turn = 0
            while True:
                entry = await bubble_queue.get()
                try:
                    if entry is None:
                        return
                    turn += 1
                    await self._broadcast_dialog_entry(
                        case_id,
                        entry,
                        role_to_agent_id,
                        turn,
                        scenario_type=trace_stage_key or trace_stage_code,
                    )
                    await self._show_dialog_entry_bubble(entry, role_to_agent_id)
                    await asyncio.sleep(gap)
                finally:
                    bubble_queue.task_done()

        consumer_task = asyncio.create_task(consume_bubbles())

        def bubble_publisher(role: str, content: str, entry: dict[str, Any] | None = None) -> None:
            loop.call_soon_threadsafe(
                bubble_queue.put_nowait,
                dict(entry or {"role": role, "content": content}),
            )

        try:
            scenario = scenario_factory(bubble_publisher)
            _attach_trace(scenario)
            try:
                result = await asyncio.to_thread(scenario.execute)
            except Exception as exc:
                if trace_recorder is not None and trace_stage_code:
                    trace_recorder.export_stage(
                        stage_code=trace_stage_code,
                        stage_key=trace_stage_key or trace_stage_code,
                        agents=list(trace_agents or []),
                        stage_result=None,
                        stage_result_path=trace_result_path,
                        status="failed",
                        error=repr(exc),
                    )
                raise
            if trace_recorder is not None and trace_stage_code:
                trace_recorder.export_stage(
                    stage_code=trace_stage_code,
                    stage_key=trace_stage_key or trace_stage_code,
                    agents=list(trace_agents or []),
                    stage_result=result,
                    stage_result_path=trace_result_path,
                    status="completed",
                )
            return result
        finally:
            await bubble_queue.join()
            await bubble_queue.put(None)
            await consumer_task

    async def _stand_agent_on_location(
        self,
        agent_id: str,
        loc_id: str,
        direction: str = "down",
        y_offset: float = 0.0,
    ) -> None:
        if not self.map_engine:
            return

        loc = None
        if getattr(self.map_engine, "registry", None):
            loc = self.map_engine.registry.get(loc_id)

        if loc:
            try:
                await self.map_engine.stand_agent(
                    agent_id,
                    direction_override=direction,
                    x=loc.x,
                    y=loc.y + y_offset,
                )
            except TypeError:
                await self.map_engine.stand_agent(agent_id, direction_override=direction)
            return

        await self.map_engine.stand_agent(agent_id, direction_override=direction)


    # ══════════════════════════════════════════════════════════
    #  位置占用管理
    # ══════════════════════════════════════════════════════════

    def _occupy_location(self, loc_id: str, agent_id: str) -> bool:
        """占用位置，返回是否成功。"""
        if loc_id in self._occupied_locations:
            logger.warning(f"[Location] {loc_id} 已被 {self._occupied_locations[loc_id]} 占用")
            return False
        self._occupied_locations[loc_id] = agent_id
        logger.debug(f"[Location] {agent_id} 占用 {loc_id}")
        return True

    def _release_location(self, loc_id: str) -> None:
        """释放位置。"""
        if loc_id in self._occupied_locations:
            agent_id = self._occupied_locations.pop(loc_id)
            logger.debug(f"[Location] {agent_id} 释放 {loc_id}")

    def _get_live_occupied_location_ids(self) -> set[str]:
        """Infer occupied chairs/sofas from current frontend agent states."""
        if not self.map_engine or not self.map_engine.registry:
            return set()

        tracked_locations = {
            **self.map_engine.registry.lawfirm_chairs,
            **self.map_engine.registry.lawfirm_sofas,
            **self.map_engine.registry.lawfirm_waiting_spots,
        }
        occupied: set[str] = set()
        for state in getattr(self.map_engine, "_agent_states", {}).values():
            sitting = state.get("sitting") or {}
            x = sitting.get("x")
            y = sitting.get("y")
            if x is None or y is None:
                continue
            for loc_id, loc in tracked_locations.items():
                if abs(loc.x - x) < 0.5 and abs(loc.y - y) < 0.5:
                    occupied.add(loc_id)
        return occupied

    def _get_reception_reserved_sofa_ids(self) -> set[str]:
        reserved: set[str] = set()
        for agent in self.registry.get_all_agents():
            queued_client_sofas = getattr(agent, "_queued_client_sofas", None)
            if isinstance(queued_client_sofas, dict):
                reserved.update(str(sofa_id) for sofa_id in queued_client_sofas.values() if sofa_id)
        return reserved

    def _get_reception_reserved_waiting_spot_ids(self) -> set[str]:
        reserved: set[str] = set()
        for agent in self.registry.get_all_agents():
            queued_client_wait_spots = getattr(agent, "_queued_client_wait_spots", None)
            if isinstance(queued_client_wait_spots, dict):
                reserved.update(str(wait_id) for wait_id in queued_client_wait_spots.values() if wait_id)
        return reserved

    def _get_available_sofa(self, firm_id: str) -> str | None:
        """获取律所中第一个空闲沙发。"""
        if not self.map_engine or not self.map_engine.registry:
            return None
        occupied = (
            set(self._occupied_locations.keys())
            | self._get_live_occupied_location_ids()
            | self._get_reception_reserved_sofa_ids()
        )
        return self.map_engine.registry.get_available_sofa(firm_id, occupied)

    def _get_available_chair_pair(self, firm_id: str) -> tuple[str | None, str | None]:
        """获取一对空闲会议椅（客户侧 left + 律师侧 right）。"""
        if not self.map_engine or not self.map_engine.registry:
            return None, None
        occupied = set(self._occupied_locations.keys()) | self._get_live_occupied_location_ids()
        return self.map_engine.registry.get_meeting_chair_pair(firm_id, occupied)

    def _get_available_waiting_spot(self, firm_id: str) -> tuple[str | None, Any]:
        """获取律所内可用的站立候位点。"""
        if not self.map_engine or not self.map_engine.registry:
            return None, None
        occupied = (
            set(self._occupied_locations.keys())
            | self._get_live_occupied_location_ids()
            | self._get_reception_reserved_waiting_spot_ids()
        )
        return self.map_engine.registry.get_available_waiting_spot(firm_id, occupied)

    def _reserve_lawyer_workspace(self, lawyer: Any) -> tuple[str, str, str]:
        """为律师分配一个临时工作位，避免占用正式法庭席位。"""
        map_prefix = self._resolve_map_prefix_from_lawyer(lawyer)

        _client_chair, lawyer_chair = self._get_available_chair_pair(map_prefix)
        if lawyer_chair and self._occupy_location(lawyer_chair, lawyer.agent_id):
            loc = self.map_engine.registry.get(lawyer_chair) if self.map_engine and self.map_engine.registry else None
            return lawyer_chair, getattr(loc, "direction", "") or "right", lawyer_chair

        wait_spot_id, wait_spot = self._get_available_waiting_spot(map_prefix)
        if wait_spot_id and self._occupy_location(wait_spot_id, lawyer.agent_id):
            return wait_spot_id, getattr(wait_spot, "direction", "") or "down", wait_spot_id

        fallback_loc_id = self._resolve_birth_location_for_map_prefix(map_prefix)
        logger.warning(
            "[Choreography] 律师 %s 无可用工作位，回退到出生点 %s",
            getattr(lawyer, "name", getattr(lawyer, "agent_id", "")),
            fallback_loc_id,
        )
        return fallback_loc_id, "down", ""

    async def _cleanup_visualized_agent(self, agent_id: str, reserved_loc_id: str = "") -> None:
        """将临时可视化的 Agent 离场，并释放占用位置。"""
        try:
            await self._return_agents_to_birth_and_despawn([agent_id])
        finally:
            if reserved_loc_id:
                self._release_location(reserved_loc_id)

    # ══════════════════════════════════════════════════════════
    #  等候队列管理
    # ══════════════════════════════════════════════════════════

    async def _add_to_waiting_queue(
        self,
        firm_id: str,
        client_id: str,
        case_id: str,
        wait_loc_id: str,
        party_role: str = "plaintiff",
        wait_mode: str = "sofa",
    ) -> None:
        """将当事人加入等候队列。"""
        if firm_id not in self._waiting_queues:
            self._waiting_queues[firm_id] = []

        self._waiting_queues[firm_id].append({
            "client_id": client_id,
            "case_id": case_id,
            "wait_loc_id": wait_loc_id,
            "wait_mode": wait_mode,
            "party_role": party_role,  # 保存 party_role
        })

        from ..core.event_bus import EventType
        await self.event_bus.publish(EventType.CLIENT_WAITING, {
            "firm_id": firm_id,
            "client_id": client_id,
            "case_id": case_id,
            "queue_position": len(self._waiting_queues[firm_id]),
        })
        logger.info(
            f"[WaitingQueue] {client_id} 加入 {firm_id} 等候队列 "
            f"(位置: {len(self._waiting_queues[firm_id])})"
        )

    async def _notify_next_waiting_client(self, lawyer_id: str, firm_id: str) -> None:
        """律师空闲后通知下一个等候的当事人。"""
        queue = self._waiting_queues.get(firm_id, [])
        if not queue:
            logger.info(f"[WaitingQueue] {firm_id} 等候队列为空，律师 {lawyer_id} 待命")
            return

        next_client_info = queue.pop(0)
        client_id = next_client_info["client_id"]
        case_id = next_client_info["case_id"]
        wait_loc_id = next_client_info.get("wait_loc_id") or next_client_info.get("sofa_id", "")
        wait_mode = next_client_info.get("wait_mode", "sofa")
        party_role = next_client_info.get("party_role", "plaintiff")  # 获取 party_role

        logger.info(f"[WaitingQueue] 通知 {client_id} 从{wait_mode}前往咨询区")

        # 释放等待位置
        if wait_loc_id:
            self._release_location(wait_loc_id)

        # 发布通知事件，重新触发分配流程
        from ..core.event_bus import EventType
        await self.event_bus.publish(EventType.CLIENT_CALLED, {
            "client_id": client_id,
            "case_id": case_id,
            "lawyer_id": lawyer_id,
            "firm_id": firm_id,
            "party_role": party_role,  # 传递 party_role
        })

    def _is_agent_available(self, agent_id: str) -> bool:
        """检查 Agent 是否空闲（不再读取文件，改为查询 EventBus）。

        Args:
            agent_id: Agent ID

        Returns:
            True 如果 Agent 空闲，False 如果正在参与活跃场景
        """
        is_busy = self.event_bus.is_agent_busy(agent_id)
        agent = self.registry.get_agent(agent_id)
        agent_name = agent.name if agent else agent_id
        current_case_id = getattr(agent, "current_handling_case", None) if agent else None
        is_available = not is_busy and not current_case_id

        logger.debug(
            f"[Orchestrator] Agent {agent_name} 状态检查: "
            f"busy={is_busy}, current_case={current_case_id}, available={is_available}"
        )
        return is_available

    # ══════════════════════════════════════════════════════════
    #  Scenario Runners
    # ══════════════════════════════════════════════════════════

    async def _run_consultation(self, payload: dict) -> None:
        """Run Legal Consultation (LC) scenario."""
        from ..scenarios.legal_consultation import LegalConsultationScenario
        from ..core.event_bus import EventType
        from .case_fsm import CaseState

        case_id = payload.get("case_id", "")
        party_role = payload.get("party_role", "plaintiff")
        lawyer_id = payload.get("lawyer_id", "")

        lawyer = self.registry.get_agent(lawyer_id)
        if not lawyer:
            logger.error(f"[Orchestrator] Lawyer {lawyer_id} not found")
            return

        # ── Player-lawyer adapter (feature-gated) — 刑事辩护律师模式 ──
        _player_adapter = None
        if (
            self._player_defense_lawyer_enabled()
            and not self._player_ai_surrogate_enabled()
        ):
            from ..player_lawyer.agent import PlayerLawyerAgent
            _player_gateway = getattr(self, "_player_gateway", None)
            if _player_gateway is not None:
                _player_adapter = PlayerLawyerAgent(
                    agent_id=lawyer.agent_id,
                    name=lawyer.name,
                    party_role="defendant",
                    law_firm=getattr(lawyer, "law_firm", ""),
                    firm_id=getattr(lawyer, "firm_id", ""),
                    gateway=_player_gateway,
                    case_id=case_id,
                    sandbox_id=getattr(self, "_sandbox_id", 0),
                    broadcast_fn=getattr(self, "_player_broadcast_fn", None),
                )
                _player_adapter.config_path = lawyer.config_path
                _player_adapter.storage = lawyer.storage
                _player_adapter.set_stage("LC")
                lawyer = _player_adapter
                logger.info("[Orchestrator] Using player defense-lawyer adapter for LC: %s", lawyer.name)

        # Find the correct client for this role
        client, client_path = self._find_client_for_case(case_id, party_role=party_role)
        if not client:
            # Fallback: search all clients and check role + case_id directly
            clients = self.registry.get_agents_by_type("client")
            for c in clients:
                if c.config_path:
                    cfg = self.storage.load_agent_config(c.config_path)
                    if cfg.get("party_role") == party_role:
                        # Use loose matching for case_id
                        c_case_id = str(cfg.get("case_id", ""))
                        if c_case_id == case_id or f"case_{c_case_id}" == case_id:
                            client = c
                            client_path = c.config_path
                            break

        if not client or not client_path:
            logger.error(f"[Orchestrator] No client found for case {case_id} with role {party_role}")
            return

        logger.info(f"[Orchestrator] Starting LC: lawyer={lawyer.name}, client={client.name}, role={party_role}")

        scenario_id = f"LC_{case_id}_{party_role}"
        case_output_dir = self._get_case_output_dir(case_id)
        display_stage_code = self._consultation_display_stage_code(party_role)
        output_path = str(case_output_dir / f"{display_stage_code}_result.json")
        trace_stage_key = f"{display_stage_code}_{party_role}".upper()
        trace_recorder: CaseAgentTraceRecorder | None = None
        scenario_succeeded = False

        # 注册活跃场景到 EventBus（在激活 Agent 之前）
        self._mark_case_stage_active(case_id, "LC", [client.agent_id, lawyer.agent_id], display_stage_code=display_stage_code)

        # 注册场景到 CheckpointManager
        if self.checkpoint_manager:
            self.checkpoint_manager.register_scenario(
                scenario_id=scenario_id,
                case_id=case_id,
                scenario_type="LC",
                party_role=party_role,
                client_id=client.agent_id,
                lawyer_id=lawyer.agent_id,
            )
            # 同步活跃场景到检查点
            self.checkpoint_manager.sync_active_scenarios_from_event_bus()

        # 发送前台交互消息：当事人移动到前台并显示对话
        if self.map_engine:
            # 获取律所信息
            lawfirm = payload.get("map_prefix", "lawfirmA")

            # 发送当事人移动到前台的消息
            await self.map_engine.send_goto_front_desk(
                agent_id=client.agent_id,
                lawfirm=lawfirm,
                dialogue_text="您好，我想咨询一下法律问题，请问律师在吗？"
            )

        # 注意：不在这里更新状态！状态已经在 CASE_ASSIGNED 事件中更新为 "原告咨询中"
        # 这里只负责执行咨询场景

        try:
            # Load case data
            data_loader, case, client_config = self._load_case_data(client_path)
            del client_config

            # 委托洽谈阶段不注入完整诉求/证据——当事人按案情概览做概括陈述，
            # 具体案件经过、证据、诉求留到后续侦查/审查起诉阶段逐步展开。
            default_claims = ""
            default_evidence = ""
            default_position = ""

            # Extract profile and questions based on role
            if party_role == "plaintiff":
                profile_data = data_loader.extract_plaintiff_profile(case)
            else:
                profile_data = data_loader.extract_defendant_profile(case)

            scenario_data = {
                "case_background": self._build_consultation_case_summary(data_loader, case),
                "questions": profile_data.get("questions", []),
                "claims": "",
                "my_position": self._stringify_prompt_value(default_position, fallback=""),
                "evidence": "",
                "case_cause": data_loader.extract_case_cause(case),
                "current_lawyer_name": lawyer.name,
                "current_lawyer_firm": lawyer.law_firm,
                "case_output_dir": str(case_output_dir.resolve()),
            }

            # Build prompts via PromptAssembler
            lawyer_scenario = PromptAssembler.build_scenario_prompt("lawyer", "LC", scenario_data)
            lawyer_config = self.storage.load_agent_config(lawyer.config_path) if lawyer.config_path else {}
            del lawyer_config
            lawyer_memory = self._get_lawyer_prompt_memory(lawyer, case_id)
            lawyer_prompt = PromptAssembler.build(
                profile=self._build_lawyer_profile(lawyer),
                long_term_memory=lawyer_memory,
                memory_owner=LAWYER_MEMORY_OWNER,
                scenario_prompt=lawyer_scenario,
            )

            client_scenario = PromptAssembler.build_scenario_prompt("client", "LC", scenario_data)
            client_memory = self._get_client_prompt_memory(client, case_id)
            client_prompt = PromptAssembler.build(
                profile=self._build_client_prompt_profile(client, profile_data),
                long_term_memory=client_memory,
                memory_owner=CLIENT_MEMORY_OWNER,
                scenario_prompt=client_scenario,
            )

            # Activate agents
            lawyer.activate(lawyer_prompt)

            # Set scenario_data for client before activation
            client.scenario_data = scenario_data
            client.activate(client_prompt)
            self._configure_stage_tools(
                "LC",
                {
                    "client": client,
                    "lawyer": lawyer,
                },
            )
            trace_recorder = self._bind_case_stage_trace_agents(
                case_id,
                display_stage_code,
                trace_stage_key,
                [client, lawyer],
            )
            self._collect_stage_prompts(case_id, "LC", client, lawyer, reset=True)
            await self._emit_runtime_stage_start(
                case_id=case_id,
                stage_code=display_stage_code,
                trace_recorder=trace_recorder,
            )
            await self._emit_runtime_stage_research(
                case_id=case_id,
                stage_code=display_stage_code,
                case_cause=scenario_data.get("case_cause", ""),
                case_background=scenario_data.get("case_background", ""),
                trace_recorder=trace_recorder,
            )

            # 确保律师已经在地图上生成（防止场景卡死等待律师加入）
            if self.map_engine and lawyer.agent_id not in self.map_engine._agent_states:
                logger.info(f"[Orchestrator] 律师 {lawyer.name} 尚未生成，先生成律师精灵")
                await self.map_engine.spawn_agent(
                    agent_id=lawyer.agent_id,
                    name=lawyer.name,
                    character_name=self._get_character_name_for_lawyer(lawyer),
                    birth_loc_id=self._get_birth_location_for_agent(lawyer.agent_id),
                    role="lawyer",
                )

            scenario = LegalConsultationScenario(
                client_agent=client,
                lawyer_agent=lawyer,
                max_turns=self._resolve_lc_max_turns(
                    len(profile_data.get("questions") or []),
                    player_lawyer_enabled=_player_adapter is not None,
                ),
                output_path=output_path,
                verbose=SCENARIO_VERBOSE,
                map_engine=self.map_engine,
                checkpoint_manager=self.checkpoint_manager,
                scenario_id=scenario_id,
                trace_recorder=trace_recorder,
                trace_stage_code=display_stage_code,
                trace_stage_key=trace_stage_key,
            )
            result = await scenario.execute()
            self._save_result(case_id, display_stage_code, result or {})
            if display_stage_code == "PLC":
                self._save_result(case_id, "LC", result or {})
            if trace_recorder is not None:
                trace_recorder.export_stage(
                    stage_code=display_stage_code,
                    stage_key=trace_stage_key,
                    agents=[client, lawyer],
                    stage_result=result or {},
                    stage_result_path=case_output_dir / f"{display_stage_code}_result.json",
                    status="completed",
                )

            # Persist memory back into agent config before advancing to the drafting stage.
            await self._checkpoint_stage_memories(
                case_id=case_id,
                stage_code=display_stage_code,
                stage_label=self.STAGE_DISPLAY_NAMES.get(display_stage_code, display_stage_code),
                agents=[lawyer, client],
            )

            # Mark scenario as completed in checkpoint
            if self.checkpoint_manager:
                self.checkpoint_manager.mark_scenario_completed(scenario_id)
            scenario_succeeded = True

        except Exception as e:
            logger.exception("[Orchestrator] LC scenario failed")
            if trace_recorder is not None:
                trace_recorder.export_stage(
                    stage_code=display_stage_code,
                    stage_key=trace_stage_key,
                    agents=[client, lawyer],
                    stage_result=None,
                    stage_result_path=case_output_dir / f"{display_stage_code}_result.json",
                    status="failed",
                    error=repr(e),
                )
            if lawyer.is_active:
                lawyer.recover_from_error()
            if client.is_active:
                client.recover_from_error()
            reported = await self._report_runtime_issue(
                case_id=case_id,
                scenario_type="LC",
                exc=e,
                stage_label="法律咨询",
            )
            if reported:
                return
            logger.error(
                "[Orchestrator] LC runtime issue was not escalated to sandbox error state: case=%s reporter=%s",
                case_id,
                callable(getattr(self, "runtime_issue_reporter", None)),
            )
        finally:
            self._clear_case_stage_active(case_id)

            # Deactivate agents only if scenario completed successfully
            if lawyer.is_active:
                lawyer.deactivate()
            if client.is_active:
                client.deactivate()

        if not scenario_succeeded:
            return

        # Advance FSM — 刑事委托洽谈统一发布委托人洽谈完成事件
        await self.event_bus.publish(EventType.PLAINTIFF_CONSULTATION_COMPLETED, {
            "case_id": case_id,
            "client_path": client_path,
            "client_id": client.agent_id,
            "lawyer_id": lawyer_id,
            "party_role": party_role or "plaintiff",
            "firm_id": getattr(lawyer, "firm_id", "law_firm_A"),
            "client_chair": payload.get("client_chair", ""),
            "lawyer_chair": payload.get("lawyer_chair", ""),
        })

    # ══════════════════════════════════════════════════════════
    #  Movement Choreography (map_engine driven)
    # ══════════════════════════════════════════════════════════

    async def _choreograph_case_assigned(self, payload: dict) -> None:
        """CASE_ASSIGNED: 根据律师状态决定流程（空闲→直接咨询，繁忙→沙发等待）。"""
        client_id = payload.get("client_id", "")
        lawyer_id = payload.get("lawyer_id", "")
        case_id = payload.get("case_id", "")
        target_firm = payload.get("map_prefix") or payload.get("target_firm", "lawfirmA")
        party_role = payload.get("party_role", "plaintiff")

        lawyer = self.registry.get_agent(lawyer_id)
        client = self.registry.get_agent(client_id)
        if not lawyer or not client:
            return

        logger.info("[Choreography] CASE_ASSIGNED: %s + %s @ %s", client.name, lawyer.name, target_firm)

        # 判断律师是否空闲（通过 EventBus 查询）
        if self._is_agent_available(lawyer_id):
            logger.info(f"[Choreography] 律师 {lawyer.name} 空闲，立即开始咨询")
            await self._start_consultation_immediately(
                client, lawyer, case_id, target_firm, party_role, payload
            )
        else:
            logger.info(f"[Choreography] 律师 {lawyer.name} 繁忙，当事人 {client.name} 移动到沙发等待")
            await self._move_client_to_waiting_area(
                client, case_id, target_firm, party_role
            )

    async def _start_consultation_immediately(
        self, client, lawyer, case_id, firm_id, party_role, payload
    ) -> None:
        """律师空闲：当事人和律师就座，开始咨询。"""

        # 关键修复：将律师 ID 写入当事人配置
        if client.config_path:
            try:
                self.storage.update_agent_field(client.config_path, "assigned_lawyer_id", lawyer.agent_id)
                logger.info(f"[Orchestrator] 已将律师 {lawyer.agent_id} 分配给当事人 {client.name}")
            except Exception as e:
                logger.error(f"[Orchestrator] 更新 assigned_lawyer_id 失败: {e}")

        # 获取空闲会议椅对
        client_chair, lawyer_chair = self._get_available_chair_pair(firm_id)
        if not client_chair or not lawyer_chair:
            logger.error(f"[Choreography] 无可用会议椅: {firm_id}，将当事人加入等候队列")
            await self._move_client_to_waiting_area(client, case_id, firm_id, party_role)
            return

        # 占用椅子
        client_reserved = self._occupy_location(client_chair, client.agent_id)
        lawyer_reserved = self._occupy_location(lawyer_chair, lawyer.agent_id)
        if not client_reserved or not lawyer_reserved:
            if client_reserved:
                self._release_location(client_chair)
            if lawyer_reserved:
                self._release_location(lawyer_chair)
            logger.error(f"[Choreography] 会议椅占用失败: {client_chair} / {lawyer_chair}")
            await self._move_client_to_waiting_area(client, case_id, firm_id, party_role)
            return

        await lawyer.start_handling_case(case_id)

        if self.map_engine:
            if lawyer.agent_id not in self.map_engine._agent_states:
                logger.info(f"[Choreography] {lawyer.name} 出生并准备前往椅子 {lawyer_chair}")
                await self.map_engine.spawn_agent(
                    agent_id=lawyer.agent_id,
                    name=lawyer.name,
                    character_name=self._get_character_name_for_lawyer(lawyer),
                    birth_loc_id=self._get_birth_location_for_agent(lawyer.agent_id),
                    role="lawyer",
                )
            else:
                logger.info(f"[Choreography] {lawyer.name} 已存在，准备前往椅子 {lawyer_chair}")

            await asyncio.gather(
                self.map_engine.stand_agent(client.agent_id),
                self.map_engine.stand_agent(lawyer.agent_id),
            )
            logger.info(
                f"[Choreography] {client.name} 与 {lawyer.name} 并行前往会议椅 "
                f"{client_chair} / {lawyer_chair}"
            )
            await asyncio.gather(
                self.map_engine.move_to_location(client.agent_id, client_chair),
                self.map_engine.move_to_location(lawyer.agent_id, lawyer_chair),
            )
            await asyncio.gather(
                self.map_engine.sit_agent(client.agent_id, client_chair),
                self.map_engine.sit_agent(lawyer.agent_id, lawyer_chair),
            )
            logger.info(f"[Choreography] ✓ 双方已就座，准备开始咨询")

        # 发布委托洽谈事件（刑事入口统一由委托人推进）
        from ..core.event_bus import EventType
        consultation_event = EventType.ENTER_PLAINTIFF_CONSULTATION

        await self.event_bus.publish(consultation_event, {
            **payload,
            "client_chair": client_chair,
            "lawyer_chair": lawyer_chair,
        })

    async def _move_client_to_waiting_area(
        self, client, case_id, firm_id, party_role: str = "plaintiff"
    ) -> None:
        """律师繁忙：当事人移动到沙发等待。"""

        # 获取空闲沙发
        sofa_id = self._get_available_sofa(firm_id)
        if sofa_id:
            # 占用沙发
            if not self._occupy_location(sofa_id, client.agent_id):
                logger.error(f"[Choreography] 沙发占用失败: {sofa_id}，当事人 {client.name} 继续等待")
                return

            if self.map_engine:
                logger.info(f"[Choreography] {client.name} 移动到沙发 {sofa_id}")
                await self.map_engine.stand_agent(client.agent_id)
                await self.map_engine.move_to_location(client.agent_id, sofa_id)
                sofa_direction = "left" if str(firm_id).lower().endswith("b") else None
                await self.map_engine.sit_agent(client.agent_id, sofa_id, direction_override=sofa_direction)

            await self._add_to_waiting_queue(
                firm_id,
                client.agent_id,
                case_id,
                sofa_id,
                party_role,
                wait_mode="sofa",
            )
            return

        wait_spot_id, wait_spot = self._get_available_waiting_spot(firm_id)
        if not wait_spot_id or not wait_spot:
            logger.error(f"[Choreography] 无可用沙发或站立候位点: {firm_id}，当事人 {client.name} 原地等待")
            return

        if not self._occupy_location(wait_spot_id, client.agent_id):
            logger.error(f"[Choreography] 候位点占用失败: {wait_spot_id}，当事人 {client.name} 继续等待")
            return

        if self.map_engine:
            logger.info(f"[Choreography] {client.name} 移动到站立候位点 {wait_spot_id}")
            await self.map_engine.stand_agent(client.agent_id)
            await self.map_engine.move_to_location(client.agent_id, wait_spot_id)
            await self.map_engine.stand_agent(
                client.agent_id,
                direction_override=getattr(wait_spot, "direction", "") or "down",
            )

        await self._add_to_waiting_queue(
            firm_id,
            client.agent_id,
            case_id,
            wait_spot_id,
            party_role,
            wait_mode="standing_queue",
        )

    async def _ensure_agent_spawned(
        self,
        agent_id: str,
        name: str,
        character_name: str,
        birth_loc_id: str,
        role: str,
    ) -> None:
        if not self.map_engine:
            return
        if agent_id in self.map_engine._agent_states:
            return
        await self.map_engine.spawn_agent(
            agent_id=agent_id,
            name=name,
            character_name=character_name,
            birth_loc_id=birth_loc_id,
            role=role,
        )

    async def _choreograph_case_closed(self, payload: dict) -> None:
        """CASE_CLOSED: everyone stands, moves to birth point, despawns."""
        if not self.map_engine:
            return

        case_id = payload.get("case_id", "")
        agent_ids = payload.get("participant_ids", [])
        logger.info(f"[Choreography] CASE_CLOSED: {case_id}, despawning {len(agent_ids)} agents")
        await self._return_agents_to_birth_and_despawn(agent_ids)

    # ══════════════════════════════════════════════════════════
    #  新增场景处理方法
    # ══════════════════════════════════════════════════════════

    async def _auto_close_case(self, payload: dict) -> None:
        """委托洽谈完成后进入侦查阶段 (INV)。"""
        from ..core.event_bus import EventType

        case_id = payload.get("case_id", "")
        client_path = payload.get("client_path", "")
        client_id = payload.get("client_id", "")
        lawyer_id = payload.get("lawyer_id", "")
        party_role = payload.get("party_role", "plaintiff")
        firm_id = payload.get("firm_id", "law_firm_A")
        client_chair = payload.get("client_chair", "")
        lawyer_chair = payload.get("lawyer_chair", "")

        logger.info(f"[Orchestrator] 委托洽谈完成，进入侦查阶段: {case_id}")

        await self.event_bus.publish(EventType.INVESTIGATION_STARTED, {
            "case_id": case_id,
            "lawyer_id": lawyer_id,
            "client_path": client_path,
            "firm_id": firm_id,
            "client_id": client_id,
            "client_chair": client_chair,
            "lawyer_chair": lawyer_chair,
            "party_role": party_role or "plaintiff",
        })

    # ══════════════════════════════════════════════════════════
    #  刑事流程 (INV → PR → DS → CR → CRA)
    # ══════════════════════════════════════════════════════════

    def _is_criminal_case(self, case: dict) -> bool:
        info = (case or {}).get("extracted_info") or {}
        if not isinstance(info, dict):
            info = {}
        raw_type = str(info.get("case_type") or (case or {}).get("case_type") or "").strip()
        return raw_type.lower() == "criminal"

    def _resolve_criminal_lawyer(self, payload: dict, case_id: str):
        """刑事案的辩护律师：优先 payload.assigned_lawyer_id → config → 任意空闲律师。"""
        lawyer_id = str(payload.get("lawyer_id") or payload.get("assigned_lawyer_id") or "").strip()
        lawyer = self.registry.get_agent(lawyer_id) if lawyer_id else None
        if lawyer:
            return lawyer
        client, client_path = self._find_client_for_case(case_id, party_role="plaintiff")
        if client and client.config_path:
            cfg = self.storage.load_agent_config(client.config_path)
            cfg_lawyer_id = str(cfg.get("assigned_lawyer_id") or "").strip()
            if cfg_lawyer_id:
                lawyer = self.registry.get_agent(cfg_lawyer_id)
                if lawyer:
                    return lawyer
        lawyers = self.registry.get_agents_by_type("lawyer")
        return lawyers[0] if lawyers else None

    def _select_available_prosecutor(self, case_id: str):
        prosecutors = self.registry.get_agents_by_type("prosecutor")
        if not prosecutors:
            return None
        for prosecutor in prosecutors:
            current = getattr(prosecutor, "current_handling_case", None)
            if not current or current == case_id:
                return prosecutor
        return prosecutors[0]

    def _build_criminal_scenario_data(self, case: dict, stage_key: str) -> dict:
        info = case.get("extracted_info", {}) or {}
        stage = info.get(stage_key, {}) or {}
        fi = info.get("first_instance", {}) or {}
        si = info.get("second_instance", {}) or {}
        defendant = (info.get("party_info", {}) or {}).get("defendant", {}) or {}

        data = {
            "case_cause": str(info.get("case_cause") or info.get("charge") or "刑事案件"),
            "charge": str(info.get("charge") or info.get("case_cause") or "刑事案件"),
            "case_background": str(info.get("case_background") or ""),
            "defendant_name": str(defendant.get("name") or "被告人"),
        }
        if stage_key == "investigation_stage":
            compulsory = info.get("compulsory_measures", {}) or {}
            custody_parts = []
            if stage.get("custody_status"):
                custody_parts.append(str(stage["custody_status"]))
            if stage.get("detention_date"):
                custody_parts.append(f"拘留时间：{stage['detention_date']}")
            if stage.get("bail_status"):
                custody_parts.append(str(stage["bail_status"]))
            data.update({
                "suspected_charge": stage.get("suspected_charge") or data["charge"],
                "custody_info": "；".join(custody_parts) or "强制措施情况待核实",
                "case_summary": stage.get("case_summary") or data["case_background"],
                "bail_facts": self._stringify_prompt_value(stage.get("key_facts_for_bail"), fallback="可评估社会危险性后申请"),
                "lawyer_actions": self._stringify_prompt_value(stage.get("lawyer_actions"), fallback="会见、了解罪名、申请取保候审"),
            })
        elif stage_key == "prosecution_stage":
            data.update({
                "indictment_summary": stage.get("indictment_summary") or fi.get("court_finding") or data["case_background"],
                "evidence_catalog": self._stringify_prompt_value(stage.get("evidence_catalog"), fallback="以在案证据为准"),
                "factors_overview": stage.get("factors_overview") or "以在案量刑情节为准",
                "mitigating_factors": self._stringify_prompt_value(stage.get("mitigating_factors"), fallback="以查明的从宽情节为准"),
                "aggravating_factors": self._stringify_prompt_value(stage.get("aggravating_factors"), fallback="无"),
                "defense_opportunities": self._stringify_prompt_value(stage.get("defense_opportunities"), fallback="围绕量刑情节辩护"),
                "non_prosecution_arguments": self._stringify_prompt_value(stage.get("non_prosecution_arguments"), fallback="以罪轻辩护为主"),
            })
        elif stage_key == "defense_stage":
            data.update({
                "facts_agreed": self._stringify_prompt_value(stage.get("facts_agreed"), fallback="指控事实主体部分"),
                "facts_disputed": self._stringify_prompt_value(stage.get("facts_disputed"), fallback="围绕量刑情节展开"),
                "mitigating_factors": self._stringify_prompt_value(stage.get("mitigating_factors"), fallback="坦白、认罪悔罪"),
                "defense_positions": self._stringify_prompt_value(stage.get("defense_positions"), fallback="罪轻辩护与程序辩护"),
                "reference_judgment": stage.get("reference_judgment") or fi.get("main_sentence") or "",
            })
        elif stage_key == "trial_stage":
            data.update({
                "prosecution_claims": stage.get("prosecution_claims") or data["case_background"],
                "contested_issues": self._stringify_prompt_value(stage.get("contested_issues"), fallback="量刑情节认定"),
                "evidence_confrontation_points": self._stringify_prompt_value(stage.get("evidence_confrontation_points"), fallback="对指控证据三性发表质证意见"),
                "evidence_catalog": self._stringify_prompt_value(stage.get("evidence_catalog"), fallback="以在案证据为准"),
                "sentencing_factors": self._stringify_prompt_value(info.get("sentencing_factors"), fallback="法定与酌定量刑情节"),
                "reference_judgment": stage.get("reference_judgment") or fi.get("main_sentence") or "",
                "case_number": fi.get("case_number") or "",
                "court_name": fi.get("court") or "人民法院",
            })
        elif stage_key == "appeal_stage":
            data.update({
                "appeal_reasons": self._stringify_prompt_value(stage.get("appeal_reasons"), fallback="就量刑适当性提出上诉"),
                "first_verdict_summary": stage.get("first_verdict_summary") or fi.get("main_sentence") or "",
                "first_court_opinion": stage.get("first_court_opinion") or fi.get("court_opinion") or "",
                "reference_judgment": stage.get("reference_judgment") or fi.get("main_sentence") or "",
                "case_number": (fi.get("case_number") or "") + "（二审）",
            })
        return data

    async def _activate_criminal_agents(
        self,
        case: dict,
        case_id: str,
        stage_code: str,
        role_prompts: dict[str, tuple],
    ) -> dict[str, Any]:
        """按 (agent, profile, scenario_data, template_key) 激活刑事场景参与人。"""
        activated: dict[str, Any] = {}
        info = case.get("extracted_info", {}) or {}
        for role, (agent, profile, scenario_data, template_key) in role_prompts.items():
            agent_type = getattr(agent, "agent_type", "")
            scenario_prompt = PromptAssembler.build_scenario_prompt(
                agent_type if agent_type in {"lawyer", "client", "judge", "resecptionist", "prosecutor"} else "lawyer",
                stage_code,
                scenario_data,
                template_key=template_key,
            )
            prompt = PromptAssembler.build(
                profile=profile,
                long_term_memory=self._get_agent_memory_payload(agent, LAWYER_MEMORY_OWNER if agent_type == "lawyer" else CLIENT_MEMORY_OWNER),
                memory_owner=LAWYER_MEMORY_OWNER if agent_type == "lawyer" else CLIENT_MEMORY_OWNER,
                scenario_prompt=scenario_prompt,
            )
            agent.activate(prompt)
            activated[role] = agent
        del info
        return activated

    async def _run_investigation(self, payload: dict) -> None:
        """INV 侦查阶段：律师与当事人家属（plaintiff client）就强制措施与会见进行咨询式对话。"""
        from ..scenarios.legal_consultation import LegalConsultationScenario
        from ..core.event_bus import EventType
        from .case_fsm import CaseState

        case_id = str(payload.get("case_id") or "")
        client_path = payload.get("client_path") or ""
        client_id = payload.get("client_id") or ""

        client, resolved_client_path = self._find_client_for_case(case_id, party_role="plaintiff")
        if not client and client_id:
            client = self.registry.get_agent(client_id)
            resolved_client_path = client.config_path if client else ""
        if not client:
            logger.error("[Orchestrator][INV] 找不到委托人 client: %s", case_id)
            return
        client_path = client_path or resolved_client_path

        lawyer = self._resolve_criminal_lawyer(payload, case_id)
        if not lawyer:
            logger.error("[Orchestrator][INV] 找不到辩护律师: %s", case_id)
            return

        logger.info("[Orchestrator] INV 开始: lawyer=%s client=%s case=%s", lawyer.name, client.name, case_id)

        scenario_id = f"INV_{case_id}"
        case_output_dir = self._get_case_output_dir(case_id)
        output_path = str(case_output_dir / "INV_result.json")
        trace_stage_key = "INV"
        trace_recorder: CaseAgentTraceRecorder | None = None
        scenario_succeeded = False

        self._mark_case_stage_active(case_id, "INV", [client.agent_id, lawyer.agent_id], display_stage_code="INV")
        if self.checkpoint_manager:
            self.checkpoint_manager.register_scenario(
                scenario_id=scenario_id,
                case_id=case_id,
                scenario_type="INV",
                party_role="plaintiff",
                client_id=client.agent_id,
                lawyer_id=lawyer.agent_id,
            )
            self.checkpoint_manager.sync_active_scenarios_from_event_bus()

        try:
            data_loader, case, _ = self._load_case_data(client_path or lawyer.config_path)
            if not self._is_criminal_case(case):
                logger.info("[Orchestrator][INV] %s 非刑事案，跳过", case_id)
                return
            del data_loader

            scenario_data = self._build_criminal_scenario_data(case, "investigation_stage")
            scenario_data["current_lawyer_name"] = lawyer.name
            scenario_data["current_lawyer_firm"] = getattr(lawyer, "law_firm", "")
            scenario_data["case_output_dir"] = str(case_output_dir.resolve())

            lawyer_prompt = PromptAssembler.build(
                profile=self._build_lawyer_profile(lawyer),
                long_term_memory=self._get_lawyer_prompt_memory(lawyer, case_id),
                memory_owner=LAWYER_MEMORY_OWNER,
                scenario_prompt=PromptAssembler.build_scenario_prompt("lawyer", "INV", scenario_data),
            )
            client_profile = self._build_client_prompt_profile(client)
            client_prompt = PromptAssembler.build(
                profile=client_profile,
                long_term_memory=self._get_client_prompt_memory(client, case_id),
                memory_owner=CLIENT_MEMORY_OWNER,
                scenario_prompt=PromptAssembler.build_scenario_prompt("client", "INV", scenario_data),
            )

            lawyer.activate(lawyer_prompt)
            client.scenario_data = scenario_data
            client.activate(client_prompt)
            self._configure_stage_tools("INV", {"client": client, "lawyer": lawyer})
            trace_recorder = self._bind_case_stage_trace_agents(case_id, "INV", trace_stage_key, [client, lawyer])
            self._collect_stage_prompts(case_id, "INV", client, lawyer, reset=True)
            await self._emit_runtime_stage_start(case_id=case_id, stage_code="INV", trace_recorder=trace_recorder)
            await self._emit_runtime_stage_research(
                case_id=case_id,
                stage_code="INV",
                case_cause=scenario_data.get("case_cause", ""),
                case_background=scenario_data.get("case_summary", ""),
                trace_recorder=trace_recorder,
            )

            if self.map_engine and lawyer.agent_id not in self.map_engine._agent_states:
                await self.map_engine.spawn_agent(
                    agent_id=lawyer.agent_id,
                    name=lawyer.name,
                    character_name=self._get_character_name_for_lawyer(lawyer),
                    birth_loc_id=self._get_birth_location_for_agent(lawyer.agent_id),
                    role="lawyer",
                )

            scenario = LegalConsultationScenario(
                client_agent=client,
                lawyer_agent=lawyer,
                max_turns=self._resolve_stage_max_turns("INV", 10),
                output_path=output_path,
                verbose=SCENARIO_VERBOSE,
                map_engine=self.map_engine,
                checkpoint_manager=self.checkpoint_manager,
                scenario_id=scenario_id,
                trace_recorder=trace_recorder,
                trace_stage_code="INV",
                trace_stage_key=trace_stage_key,
            )
            result = await scenario.execute()
            self._save_result(case_id, "INV", result or {})
            if trace_recorder is not None:
                trace_recorder.export_stage(
                    stage_code="INV",
                    stage_key=trace_stage_key,
                    agents=[client, lawyer],
                    stage_result=result or {},
                    stage_result_path=case_output_dir / "INV_result.json",
                    status="completed",
                )
            await self._checkpoint_stage_memories(
                case_id=case_id,
                stage_code="INV",
                stage_label="侦查阶段",
                agents=[lawyer, client],
            )
            if self.checkpoint_manager:
                self.checkpoint_manager.mark_scenario_completed(scenario_id)
            scenario_succeeded = True

        except Exception as e:
            logger.exception("[Orchestrator] INV scenario failed")
            if trace_recorder is not None:
                trace_recorder.export_stage(
                    stage_code="INV",
                    stage_key=trace_stage_key,
                    agents=[client, lawyer],
                    stage_result=None,
                    stage_result_path=case_output_dir / "INV_result.json",
                    status="failed",
                    error=repr(e),
                )
            if lawyer.is_active:
                lawyer.recover_from_error()
            if client.is_active:
                client.recover_from_error()
            await self._report_runtime_issue(case_id=case_id, scenario_type="INV", exc=e, stage_label="侦查阶段")
        finally:
            self._clear_case_stage_active(case_id)
            if lawyer.is_active:
                lawyer.deactivate()
            if client.is_active:
                client.deactivate()

        if not scenario_succeeded:
            return

        await self.event_bus.publish(EventType.INVESTIGATION_COMPLETED, {
            "case_id": case_id,
            "client_path": client_path,
            "client_id": client.agent_id,
            "lawyer_id": lawyer.agent_id,
            "party_role": "plaintiff",
        })

    async def _on_investigation_completed(self, payload: dict) -> None:
        """INV完成 → 进入PR审查起诉。"""
        await self.event_bus.publish(EventType.PROSECUTION_REVIEW_STARTED, {
            **payload,
            "party_role": "plaintiff",
        })

    async def _run_prosecution_review(self, payload: dict) -> None:
        """PR 审查起诉：律师（阅卷后）与被告人会见 + 向检察官提交辩护意见。"""
        from ..scenarios.prosecution_review import ProsecutionReviewScenario
        from ..core.event_bus import EventType

        case_id = str(payload.get("case_id") or "")
        client_path = payload.get("client_path") or ""

        defendant, defendant_path = self._find_client_for_case(case_id, party_role="defendant")
        client, client_path = self._find_client_for_case(case_id, party_role="plaintiff")
        if not defendant:
            defendant, defendant_path = client, client_path
        if not defendant:
            logger.error("[Orchestrator][PR] 找不到被告人 client: %s", case_id)
            return

        lawyer = self._resolve_criminal_lawyer(payload, case_id)
        if not lawyer:
            logger.error("[Orchestrator][PR] 找不到辩护律师: %s", case_id)
            return
        prosecutor = self._select_available_prosecutor(case_id)
        if prosecutor is None:
            logger.error("[Orchestrator][PR] 找不到检察官 agent: %s", case_id)
            return

        logger.info("[Orchestrator] PR 开始: lawyer=%s prosecutor=%s defendant=%s case=%s",
                    lawyer.name, prosecutor.name, defendant.name, case_id)

        scenario_id = f"PR_{case_id}"
        case_output_dir = self._get_case_output_dir(case_id)
        output_path = str(case_output_dir / "PR_result.json")
        trace_recorder: CaseAgentTraceRecorder | None = None
        scenario_succeeded = False

        self._mark_case_stage_active(
            case_id, "PR",
            [lawyer.agent_id, prosecutor.agent_id, defendant.agent_id],
            display_stage_code="PR",
        )
        if self.checkpoint_manager:
            self.checkpoint_manager.register_scenario(
                scenario_id=scenario_id,
                case_id=case_id,
                scenario_type="PR",
                party_role="defendant",
                client_id=defendant.agent_id,
                lawyer_id=lawyer.agent_id,
            )
            self.checkpoint_manager.sync_active_scenarios_from_event_bus()

        try:
            data_loader, case, _ = self._load_case_data(defendant_path or client_path)
            if not self._is_criminal_case(case):
                logger.info("[Orchestrator][PR] %s 非刑事案，跳过", case_id)
                return
            del data_loader

            scenario_data = self._build_criminal_scenario_data(case, "prosecution_stage")
            scenario_data["current_lawyer_name"] = lawyer.name
            scenario_data["current_lawyer_firm"] = getattr(lawyer, "law_firm", "")
            scenario_data["case_output_dir"] = str(case_output_dir.resolve())

            lawyer_prompt = PromptAssembler.build(
                profile=self._build_lawyer_profile(lawyer),
                long_term_memory=self._get_lawyer_prompt_memory(lawyer, case_id),
                memory_owner=LAWYER_MEMORY_OWNER,
                scenario_prompt=PromptAssembler.build_scenario_prompt("lawyer", "PR", scenario_data),
            )
            defendant_profile = self._build_client_prompt_profile(
                defendant,
                self._build_criminal_defendant_profile(case),
            )
            defendant_prompt = PromptAssembler.build(
                profile=defendant_profile,
                long_term_memory=self._get_client_prompt_memory(defendant, case_id),
                memory_owner=CLIENT_MEMORY_OWNER,
                scenario_prompt=PromptAssembler.build_scenario_prompt("client", "PR", scenario_data),
            )
            prosecutor_prompt = PromptAssembler.build(
                profile=self._build_prosecutor_profile(prosecutor),
                scenario_prompt=PromptAssembler.build_scenario_prompt("prosecutor", "PR", scenario_data),
            )

            lawyer.activate(lawyer_prompt)
            defendant.scenario_data = scenario_data
            defendant.activate(defendant_prompt)
            prosecutor.activate(prosecutor_prompt)
            self._configure_stage_tools("PR", {
                "lawyer": lawyer,
                "prosecutor": prosecutor,
                "defendant": defendant,
            })
            trace_recorder = self._bind_case_stage_trace_agents(
                case_id, "PR", "PR", [lawyer, prosecutor, defendant],
            )
            self._collect_stage_prompts(case_id, "PR", lawyer, prosecutor, defendant, reset=True)
            await self._emit_runtime_stage_start(case_id=case_id, stage_code="PR", trace_recorder=trace_recorder)
            await self._emit_runtime_stage_research(
                case_id=case_id,
                stage_code="PR",
                case_cause=scenario_data.get("case_cause", ""),
                case_background=scenario_data.get("indictment_summary", ""),
                trace_recorder=trace_recorder,
            )

            role_to_agent_id = {
                "lawyer": lawyer.agent_id,
                "prosecutor": prosecutor.agent_id,
                "defendant": defendant.agent_id,
            }

            def factory(bubble_publisher):
                return ProsecutionReviewScenario(
                    lawyer_agent=lawyer,
                    prosecutor_agent=prosecutor,
                    defendant_agent=defendant,
                    max_turns=self._resolve_stage_max_turns("PR", 12),
                    output_path=output_path,
                    verbose=SCENARIO_VERBOSE,
                    bubble_publisher=bubble_publisher,
                    trace_recorder=trace_recorder,
                    trace_stage_code="PR",
                    trace_stage_key="PR",
                )

            result = await self._run_sync_scenario_with_live_bubbles(
                case_id=case_id,
                scenario_factory=factory,
                role_to_agent_id=role_to_agent_id,
                gap=0.9,
                trace_recorder=trace_recorder,
                trace_stage_code="PR",
                trace_stage_key="PR",
                trace_agents=[lawyer, prosecutor, defendant],
                trace_result_path=case_output_dir / "PR_result.json",
            )
            self._save_result(case_id, "PR", result or {})
            await self._checkpoint_stage_memories(
                case_id=case_id,
                stage_code="PR",
                stage_label="审查起诉阶段",
                agents=[lawyer, defendant],
            )
            if self.checkpoint_manager:
                self.checkpoint_manager.mark_scenario_completed(scenario_id)
            scenario_succeeded = True

        except Exception as e:
            logger.exception("[Orchestrator] PR scenario failed")
            if trace_recorder is not None:
                trace_recorder.export_stage(
                    stage_code="PR",
                    stage_key="PR",
                    agents=[lawyer, prosecutor, defendant],
                    stage_result=None,
                    stage_result_path=case_output_dir / "PR_result.json",
                    status="failed",
                    error=repr(e),
                )
            for agent in (lawyer, prosecutor, defendant):
                if getattr(agent, "is_active", False):
                    agent.recover_from_error()
            await self._report_runtime_issue(case_id=case_id, scenario_type="PR", exc=e, stage_label="审查起诉阶段")
        finally:
            self._clear_case_stage_active(case_id)
            for agent in (lawyer, prosecutor, defendant):
                if getattr(agent, "is_active", False):
                    agent.deactivate()

        if not scenario_succeeded:
            return

        await self.event_bus.publish(EventType.PROSECUTION_REVIEW_COMPLETED, {
            "case_id": case_id,
            "client_path": defendant_path or client_path,
            "client_id": defendant.agent_id,
            "lawyer_id": lawyer.agent_id,
            "party_role": "defendant",
        })

    def _build_criminal_defendant_profile(self, case: dict) -> dict:
        info = case.get("extracted_info", {}) or {}
        defendant = (info.get("party_info", {}) or {}).get("defendant", {}) or {}
        profile = dict(defendant)
        profile.setdefault("name", defendant.get("name") or "被告人")
        profile.setdefault("party_type", "自然人")
        return profile

    def _build_prosecutor_profile(self, prosecutor) -> dict:
        return {
            "name": getattr(prosecutor, "name", "检察官"),
            "party_type": "natural_person",
            "occupation": "检察官",
            "interaction_guidelines": (
                "你是国家公诉人，客观公正执业。发言简洁专业，每轮2到4句。"
            ),
        }

    async def _on_prosecution_review_completed(self, payload: dict) -> None:
        """PR完成 → 检察官提起公诉 → 进入DS辩护词起草。"""
        await self.event_bus.publish(EventType.INDICTMENT_DRAFTED, {**payload, "party_role": "defendant"})
        await self.event_bus.publish(EventType.ENTER_DEFENSE_OPINION_DRAFTING, {**payload, "party_role": "defendant"})

    async def _run_defense_opinion_drafting(self, payload: dict) -> None:
        """DS 辩护词起草：律师与被告人沟通后起草《辩护词》。"""
        from ..scenarios.defense_opinion_drafting import DefenseOpinionDraftingScenario
        from ..tools.legal import get_document_drafting_tool_name
        from ..core.event_bus import EventType

        case_id = str(payload.get("case_id") or "")
        defendant, defendant_path = self._find_client_for_case(case_id, party_role="defendant")
        if not defendant:
            defendant, defendant_path = self._find_client_for_case(case_id, party_role="plaintiff")
        lawyer = self._resolve_criminal_lawyer(payload, case_id)
        if not defendant or not lawyer:
            logger.error("[Orchestrator][DS] 缺少被告人或律师: %s", case_id)
            return

        logger.info("[Orchestrator] DS 开始: lawyer=%s defendant=%s case=%s", lawyer.name, defendant.name, case_id)

        scenario_id = f"DS_{case_id}"
        case_output_dir = self._get_case_output_dir(case_id)
        output_path = str(case_output_dir / "DS_result.json")
        trace_recorder: CaseAgentTraceRecorder | None = None
        scenario_succeeded = False

        self._mark_case_stage_active(case_id, "DS", [defendant.agent_id, lawyer.agent_id], display_stage_code="DS")
        if self.checkpoint_manager:
            self.checkpoint_manager.register_scenario(
                scenario_id=scenario_id,
                case_id=case_id,
                scenario_type="DS",
                party_role="defendant",
                client_id=defendant.agent_id,
                lawyer_id=lawyer.agent_id,
            )
            self.checkpoint_manager.sync_active_scenarios_from_event_bus()

        try:
            data_loader, case, _ = self._load_case_data(defendant_path)
            if not self._is_criminal_case(case):
                logger.info("[Orchestrator][DS] %s 非刑事案，跳过", case_id)
                return
            del data_loader

            scenario_data = self._build_criminal_scenario_data(case, "defense_stage")
            scenario_data["current_lawyer_name"] = lawyer.name
            scenario_data["current_lawyer_firm"] = getattr(lawyer, "law_firm", "")
            scenario_data["case_output_dir"] = str(case_output_dir.resolve())

            lawyer_prompt = PromptAssembler.build(
                profile=self._build_lawyer_profile(lawyer),
                long_term_memory=self._get_lawyer_prompt_memory(lawyer, case_id),
                memory_owner=LAWYER_MEMORY_OWNER,
                scenario_prompt=PromptAssembler.build_scenario_prompt("lawyer", "DS", scenario_data),
            )
            defendant_profile = self._build_client_prompt_profile(
                defendant, self._build_criminal_defendant_profile(case),
            )
            defendant_prompt = PromptAssembler.build(
                profile=defendant_profile,
                long_term_memory=self._get_client_prompt_memory(defendant, case_id),
                memory_owner=CLIENT_MEMORY_OWNER,
                scenario_prompt=PromptAssembler.build_scenario_prompt("client", "DS", scenario_data),
            )

            lawyer.activate(lawyer_prompt)
            defendant.scenario_data = scenario_data
            defendant.activate(defendant_prompt)
            self._configure_stage_tools("DS", {"lawyer": lawyer, "defendant": defendant})
            trace_recorder = self._bind_case_stage_trace_agents(case_id, "DS", "DS", [defendant, lawyer])
            self._collect_stage_prompts(case_id, "DS", lawyer, defendant, reset=True)
            await self._emit_runtime_stage_start(case_id=case_id, stage_code="DS", trace_recorder=trace_recorder)
            await self._emit_runtime_stage_research(
                case_id=case_id,
                stage_code="DS",
                case_cause=scenario_data.get("case_cause", ""),
                case_background=scenario_data.get("facts_disputed", ""),
                trace_recorder=trace_recorder,
            )

            required_tool = get_document_drafting_tool_name("DS")
            if required_tool not in {
                t.get_function_name() for t in list(getattr(lawyer, "tools", []) or [])
                if hasattr(t, "get_function_name")
            }:
                logger.error("[Orchestrator][DS] 律师缺少起草工具 %s", required_tool)

            scenario = DefenseOpinionDraftingScenario(
                defendant_agent=defendant,
                lawyer_agent=lawyer,
                max_turns=self._resolve_stage_max_turns("DS", 14),
                output_path=output_path,
                verbose=SCENARIO_VERBOSE,
                map_engine=self.map_engine,
                checkpoint_manager=self.checkpoint_manager,
                scenario_id=scenario_id,
                trace_recorder=trace_recorder,
                trace_stage_code="DS",
                trace_stage_key="DS",
            )
            result = await asyncio.to_thread(scenario.execute)
            self._save_result(case_id, "DS", result or {})
            if trace_recorder is not None:
                trace_recorder.export_stage(
                    stage_code="DS",
                    stage_key="DS",
                    agents=[defendant, lawyer],
                    stage_result=result or {},
                    stage_result_path=case_output_dir / "DS_result.json",
                    status="completed",
                )
            await self._checkpoint_stage_memories(
                case_id=case_id,
                stage_code="DS",
                stage_label="辩护词起草",
                agents=[lawyer, defendant],
            )
            if self.checkpoint_manager:
                self.checkpoint_manager.mark_scenario_completed(scenario_id)
            scenario_succeeded = True

        except Exception as e:
            logger.exception("[Orchestrator] DS scenario failed")
            if trace_recorder is not None:
                trace_recorder.export_stage(
                    stage_code="DS",
                    stage_key="DS",
                    agents=[defendant, lawyer],
                    stage_result=None,
                    stage_result_path=case_output_dir / "DS_result.json",
                    status="failed",
                    error=repr(e),
                )
            for agent in (lawyer, defendant):
                if getattr(agent, "is_active", False):
                    agent.recover_from_error()
            await self._report_runtime_issue(case_id=case_id, scenario_type="DS", exc=e, stage_label="辩护词起草")
        finally:
            self._clear_case_stage_active(case_id)
            for agent in (lawyer, defendant):
                if getattr(agent, "is_active", False):
                    agent.deactivate()

        if not scenario_succeeded:
            return

        await self.event_bus.publish(EventType.DEFENSE_OPINION_DRAFTING_COMPLETED, {
            "case_id": case_id,
            "client_path": defendant_path,
            "client_id": defendant.agent_id,
            "lawyer_id": lawyer.agent_id,
            "party_role": "defendant",
        })

    async def _on_defense_opinion_filed(self, payload: dict) -> None:
        """DS完成（辩护词已递交）→ 等待开庭 → 进入刑事一审。"""
        from ..core.event_bus import EventType

        case_id = str(payload.get("case_id") or "")
        if self._trial_queue_busy():
            await self._enqueue_criminal_trial(payload)
            return
        await self.event_bus.publish(EventType.ENTER_CRIMINAL_TRIAL, {**payload, "party_role": "defendant"})

    def _trial_queue_busy(self) -> bool:
        return bool(getattr(self, "_court_reservations", {}))

    async def _enqueue_criminal_trial(self, payload: dict) -> None:
        court_queue = self._trial_queues.get("courtA")
        if court_queue is not None:
            court_queue.append({**payload, "_pending_event": "ENTER_CRIMINAL_TRIAL"})
            logger.info("[Orchestrator] 法庭占用中，刑事一审入队: %s", payload.get("case_id"))
            return
        from ..core.event_bus import EventType
        await self.event_bus.publish(EventType.ENTER_CRIMINAL_TRIAL, {**payload, "party_role": "defendant"})

    async def _run_criminal_trial(self, payload: dict) -> None:
        """CR 刑事一审庭审。"""
        from ..scenarios.criminal_trial import CriminalTrialScenario
        from ..core.event_bus import EventType

        case_id = str(payload.get("case_id") or "")
        defendant, defendant_path = self._find_client_for_case(case_id, party_role="defendant")
        if not defendant:
            defendant, defendant_path = self._find_client_for_case(case_id, party_role="plaintiff")
        lawyer = self._resolve_criminal_lawyer(payload, case_id)
        prosecutor = self._select_available_prosecutor(case_id)
        court = "courtA"
        judge = self._select_available_judge("basic", case_id)
        if not defendant or not lawyer or not prosecutor or not judge:
            logger.error(
                "[Orchestrator][CR] 参与人不全 defendant=%s lawyer=%s prosecutor=%s judge=%s (%s)",
                defendant is not None, lawyer is not None, prosecutor is not None, judge is not None, case_id,
            )
            return

        self._reserve_trial_resources(court, case_id, judge.agent_id)

        logger.info(
            "[Orchestrator] CR 开始: judge=%s prosecutor=%s defense=%s defendant=%s case=%s",
            judge.name, prosecutor.name, lawyer.name, defendant.name, case_id,
        )

        scenario_id = f"CR_{case_id}"
        case_output_dir = self._get_case_output_dir(case_id)
        output_path = str(case_output_dir / "CR_result.json")
        trace_recorder: CaseAgentTraceRecorder | None = None
        scenario_succeeded = False

        self._mark_case_stage_active(
            case_id, "CR",
            [judge.agent_id, prosecutor.agent_id, lawyer.agent_id, defendant.agent_id],
            display_stage_code="CR",
        )
        if self.checkpoint_manager:
            self.checkpoint_manager.register_scenario(
                scenario_id=scenario_id,
                case_id=case_id,
                scenario_type="CR",
                party_role="defendant",
                client_id=defendant.agent_id,
                lawyer_id=lawyer.agent_id,
            )
            self.checkpoint_manager.sync_active_scenarios_from_event_bus()

        try:
            data_loader, case, _ = self._load_case_data(defendant_path)
            if not self._is_criminal_case(case):
                logger.info("[Orchestrator][CR] %s 非刑事案，跳过", case_id)
                return
            del data_loader
            info = case.get("extracted_info", {}) or {}
            fi = info.get("first_instance", {}) or {}

            trial_data = self._build_criminal_scenario_data(case, "trial_stage")

            judge_scenario_data = {
                **trial_data,
                "defendant_name": trial_data.get("defendant_name", "被告人"),
            }
            judge_prompt = PromptAssembler.build(
                profile=self._build_judge_profile(judge),
                scenario_prompt=PromptAssembler.build_scenario_prompt("judge", "CR", judge_scenario_data),
            )
            prosecutor_scenario_data = dict(trial_data)
            prosecutor_prompt = PromptAssembler.build(
                profile=self._build_prosecutor_profile(prosecutor),
                scenario_prompt=PromptAssembler.build_scenario_prompt("prosecutor", "CR", prosecutor_scenario_data),
            )
            defense_prompt = PromptAssembler.build(
                profile=self._build_lawyer_profile(lawyer),
                long_term_memory=self._get_lawyer_prompt_memory(lawyer, case_id),
                memory_owner=LAWYER_MEMORY_OWNER,
                scenario_prompt=PromptAssembler.build_scenario_prompt("lawyer", "CR", trial_data, template_key="CR-defense_lawyer"),
            )
            defendant_profile = self._build_client_prompt_profile(
                defendant, self._build_criminal_defendant_profile(case),
            )
            defendant_prompt = PromptAssembler.build(
                profile=defendant_profile,
                long_term_memory=self._get_client_prompt_memory(defendant, case_id),
                memory_owner=CLIENT_MEMORY_OWNER,
                scenario_prompt=PromptAssembler.build_scenario_prompt("client", "CR", trial_data, template_key="CR-defendant"),
            )

            judge.activate(judge_prompt)
            prosecutor.activate(prosecutor_prompt)
            lawyer.activate(defense_prompt)
            defendant.scenario_data = trial_data
            defendant.activate(defendant_prompt)
            self._configure_stage_tools("CR", {
                "judge": judge,
                "prosecutor": prosecutor,
                "defense_lawyer": lawyer,
                "defendant": defendant,
            })
            trace_recorder = self._bind_case_stage_trace_agents(
                case_id, "CR", "CR", [judge, prosecutor, lawyer, defendant],
            )
            self._collect_stage_prompts(case_id, "CR", judge, prosecutor, lawyer, defendant, reset=True)
            await self._emit_runtime_stage_start(case_id=case_id, stage_code="CR", trace_recorder=trace_recorder)
            await self._emit_runtime_stage_research(
                case_id=case_id,
                stage_code="CR",
                case_cause=trial_data.get("case_cause", ""),
                case_background=trial_data.get("prosecution_claims", ""),
                trace_recorder=trace_recorder,
            )

            await self._choreograph_criminal_trial(
                court=court,
                judge=judge,
                prosecutor=prosecutor,
                defense_lawyer=lawyer,
                defendant=defendant,
            )

            scenario = CriminalTrialScenario(
                judge_agent=judge,
                prosecutor_agent=prosecutor,
                defendant_agent=defendant,
                defense_lawyer_agent=lawyer,
                max_debate_rounds=4,
                max_investigation_rounds=5,
                verbose=SCENARIO_VERBOSE,
                court_finding=str(fi.get("court_finding") or ""),
                court_opinion=str(fi.get("court_opinion") or ""),
                output_path=output_path,
                trace_recorder=trace_recorder,
                trace_stage_code="CR",
                trace_stage_key="CR",
            )
            result = await asyncio.to_thread(scenario.execute)
            self._save_result(case_id, "CR", result or {})
            if trace_recorder is not None:
                trace_recorder.export_stage(
                    stage_code="CR",
                    stage_key="CR",
                    agents=[judge, prosecutor, lawyer, defendant],
                    stage_result=result or {},
                    stage_result_path=case_output_dir / "CR_result.json",
                    status="completed",
                )
            await self._checkpoint_stage_memories(
                case_id=case_id,
                stage_code="CR",
                stage_label="刑事一审庭审",
                agents=[lawyer, defendant],
            )
            if self.checkpoint_manager:
                self.checkpoint_manager.mark_scenario_completed(scenario_id)
            scenario_succeeded = True

        except Exception as e:
            logger.exception("[Orchestrator] CR scenario failed")
            if trace_recorder is not None:
                trace_recorder.export_stage(
                    stage_code="CR",
                    stage_key="CR",
                    agents=[judge, prosecutor, lawyer, defendant],
                    stage_result=None,
                    stage_result_path=case_output_dir / "CR_result.json",
                    status="failed",
                    error=repr(e),
                )
            for agent in (judge, prosecutor, lawyer, defendant):
                if getattr(agent, "is_active", False):
                    agent.recover_from_error()
            await self._report_runtime_issue(case_id=case_id, scenario_type="CR", exc=e, stage_label="刑事一审庭审")
        finally:
            self._clear_case_stage_active(case_id)
            for agent in (judge, prosecutor, lawyer, defendant):
                if getattr(agent, "is_active", False):
                    agent.deactivate()
            await self._release_trial_slot(court, case_id)

        if not scenario_succeeded:
            return

        await self.event_bus.publish(EventType.CRIMINAL_TRIAL_COMPLETED, {
            "case_id": case_id,
            "client_path": defendant_path,
            "client_id": defendant.agent_id,
            "lawyer_id": lawyer.agent_id,
            "judge_id": judge.agent_id,
            "prosecutor_id": prosecutor.agent_id,
            "party_role": "defendant",
        })

    async def _choreograph_criminal_trial(
        self,
        *,
        court: str,
        judge,
        prosecutor,
        defense_lawyer,
        defendant,
    ) -> None:
        """刑事庭审地图编排：审判席、公诉人席、辩护席、被告人席。

        使用地图注册表中的真实位置ID（courtBasic_* / courtIntermediate_*）。
        """
        if not self.map_engine:
            return

        building = "courtIntermediate" if court == "courtB" else "courtBasic"
        participants = {
            "judge": (judge, f"{building}_judge_seat"),
            "prosecutor": (prosecutor, f"{building}_plaintiff_chair_1"),
            "defense_lawyer": (defense_lawyer, f"{building}_plaintiff_lawyer_chair_1"),
            "defendant": (defendant, f"{building}_defendant_chair_1"),
        }

        for role, (agent, _) in participants.items():
            if agent.agent_id not in self.map_engine._agent_states:
                if role == "defendant":
                    character_name = self._get_character_name_for_client(agent, "defendant")
                elif role == "defense_lawyer":
                    character_name = self._get_character_name_for_lawyer(agent)
                else:
                    character_name = self._get_or_assign_character_name(agent)
                await self.map_engine.spawn_agent(
                    agent_id=agent.agent_id,
                    name=agent.name,
                    character_name=character_name,
                    birth_loc_id=self._get_birth_location_for_agent(agent.agent_id),
                    role=role,
                )
            else:
                await self.map_engine.stand_agent(agent.agent_id)

        move_tasks = [
            self.map_engine.move_to_location(agent.agent_id, loc_id)
            for role, (agent, loc_id) in participants.items()
        ]
        if move_tasks:
            await asyncio.gather(*move_tasks)

        await self._stand_agent_on_location(
            judge.agent_id, f"{building}_judge_seat", direction="down", y_offset=-16.0,
        )
        seat_directions = {
            "prosecutor": "right",
            "defense_lawyer": "right",
            "defendant": "left",
        }
        for role, (agent, loc_id) in participants.items():
            if role in seat_directions:
                await self.map_engine.sit_agent(
                    agent.agent_id, loc_id, direction_override=seat_directions[role],
                )

    def _build_judge_profile(self, judge) -> dict:
        return {
            "name": getattr(judge, "name", "审判长"),
            "court_name": getattr(judge, "court_name", "人民法院"),
            "court_level": getattr(judge, "court_level", "basic"),
            "years_of_experience": getattr(judge, "years_of_experience", ""),
        }

    async def _on_criminal_trial_completed(self, payload: dict) -> None:
        """CR完成 → 宣判 → 上诉决策（数据集有二审则上诉，否则服判）。"""
        from ..core.event_bus import EventType

        case_id = str(payload.get("case_id") or "")
        will_appeal = bool(payload.get("will_appeal"))
        if not will_appeal:
            client_path = payload.get("client_path") or ""
            if client_path:
                try:
                    _, case, _ = self._load_case_data(client_path)
                    appeal_stage = (case.get("extracted_info", {}) or {}).get("appeal_stage", {}) or {}
                    will_appeal = bool(appeal_stage.get("has_appeal"))
                except Exception as exc:
                    logger.warning("[Orchestrator] 读取上诉信息失败 %s: %s", case_id, exc)

        await self.event_bus.publish(EventType.CRIMINAL_VERDICT_ISSUED, {
            **payload,
            "will_appeal": will_appeal,
        })

    async def _on_criminal_verdict_issued(self, payload: dict) -> None:
        """刑事一审判决后：上诉 → CRA 二审；服判 → 结案。"""
        from ..core.event_bus import EventType

        will_appeal = bool(payload.get("will_appeal"))
        case_id = str(payload.get("case_id") or "")
        if will_appeal:
            await self.event_bus.publish(EventType.ENTER_CRIMINAL_APPEAL_TRIAL, {
                **payload,
                "party_role": "defendant",
            })
            return

        participants = self._collect_case_participant_ids(case_id)
        await self.event_bus.publish(EventType.CASE_CLOSED, {
            "case_id": case_id,
            "client_path": payload.get("client_path") or "",
            "client_id": payload.get("client_id") or "",
            "party_role": "defendant",
            "participant_ids": participants,
        })
        await self.event_bus.publish(EventType.CRIMINAL_FINAL_VERDICT_ISSUED, {
            "case_id": case_id,
            "client_path": payload.get("client_path") or "",
            "client_id": payload.get("client_id") or "",
            "party_role": "defendant",
        })

    async def _run_criminal_appeal_trial(self, payload: dict) -> None:
        """CRA 刑事二审庭审。"""
        from ..scenarios.criminal_appeal_trial import CriminalAppealTrialScenario
        from ..core.event_bus import EventType

        case_id = str(payload.get("case_id") or "")
        appellant, appellant_path = self._find_client_for_case(case_id, party_role="defendant")
        if not appellant:
            appellant, appellant_path = self._find_client_for_case(case_id, party_role="plaintiff")
        lawyer = self._resolve_criminal_lawyer(payload, case_id)
        prosecutor = self._select_available_prosecutor(case_id)
        court = "courtB"
        judge = self._select_available_judge("intermediate", case_id)
        if not appellant or not lawyer or not prosecutor or not judge:
            logger.error("[Orchestrator][CRA] 参与人不全 (%s)", case_id)
            return

        self._reserve_trial_resources(court, case_id, judge.agent_id)
        logger.info("[Orchestrator] CRA 开始: judge=%s prosecutor=%s defense=%s appellant=%s case=%s",
                    judge.name, prosecutor.name, lawyer.name, appellant.name, case_id)

        scenario_id = f"CRA_{case_id}"
        case_output_dir = self._get_case_output_dir(case_id)
        output_path = str(case_output_dir / "CRA_result.json")
        trace_recorder: CaseAgentTraceRecorder | None = None
        scenario_succeeded = False

        self._mark_case_stage_active(
            case_id, "CRA",
            [judge.agent_id, prosecutor.agent_id, lawyer.agent_id, appellant.agent_id],
            display_stage_code="CRA",
        )
        if self.checkpoint_manager:
            self.checkpoint_manager.register_scenario(
                scenario_id=scenario_id,
                case_id=case_id,
                scenario_type="CRA",
                party_role="defendant",
                client_id=appellant.agent_id,
                lawyer_id=lawyer.agent_id,
            )
            self.checkpoint_manager.sync_active_scenarios_from_event_bus()

        try:
            data_loader, case, _ = self._load_case_data(appellant_path)
            if not self._is_criminal_case(case):
                logger.info("[Orchestrator][CRA] %s 非刑事案，跳过", case_id)
                return
            del data_loader
            info = case.get("extracted_info", {}) or {}
            fi = info.get("first_instance", {}) or {}

            appeal_data = self._build_criminal_scenario_data(case, "appeal_stage")

            judge_prompt = PromptAssembler.build(
                profile=self._build_judge_profile(judge),
                scenario_prompt=PromptAssembler.build_scenario_prompt("judge", "CRA", appeal_data),
            )
            prosecutor_prompt = PromptAssembler.build(
                profile=self._build_prosecutor_profile(prosecutor),
                scenario_prompt=PromptAssembler.build_scenario_prompt("prosecutor", "CRA", appeal_data),
            )
            defense_prompt = PromptAssembler.build(
                profile=self._build_lawyer_profile(lawyer),
                long_term_memory=self._get_lawyer_prompt_memory(lawyer, case_id),
                memory_owner=LAWYER_MEMORY_OWNER,
                scenario_prompt=PromptAssembler.build_scenario_prompt("lawyer", "CRA", appeal_data, template_key="CRA-defense_lawyer"),
            )
            appellant_profile = self._build_client_prompt_profile(
                appellant, self._build_criminal_defendant_profile(case),
            )
            appellant_prompt = PromptAssembler.build(
                profile=appellant_profile,
                long_term_memory=self._get_client_prompt_memory(appellant, case_id),
                memory_owner=CLIENT_MEMORY_OWNER,
                scenario_prompt=PromptAssembler.build_scenario_prompt("client", "CRA", appeal_data, template_key="CRA-appellant"),
            )

            judge.activate(judge_prompt)
            prosecutor.activate(prosecutor_prompt)
            lawyer.activate(defense_prompt)
            appellant.scenario_data = appeal_data
            appellant.activate(appellant_prompt)
            self._configure_stage_tools("CRA", {
                "judge": judge,
                "prosecutor": prosecutor,
                "defense_lawyer": lawyer,
                "appellant": appellant,
            })
            trace_recorder = self._bind_case_stage_trace_agents(
                case_id, "CRA", "CRA", [judge, prosecutor, lawyer, appellant],
            )
            self._collect_stage_prompts(case_id, "CRA", judge, prosecutor, lawyer, appellant, reset=True)
            await self._emit_runtime_stage_start(case_id=case_id, stage_code="CRA", trace_recorder=trace_recorder)

            await self._choreograph_criminal_trial(
                court=court,
                judge=judge,
                prosecutor=prosecutor,
                defense_lawyer=lawyer,
                defendant=appellant,
            )

            scenario = CriminalAppealTrialScenario(
                judge_agent=judge,
                prosecutor_agent=prosecutor,
                appellant_agent=appellant,
                defense_lawyer_agent=lawyer,
                max_debate_rounds=4,
                max_investigation_rounds=4,
                verbose=SCENARIO_VERBOSE,
                court_finding=str(fi.get("court_finding") or ""),
                court_opinion=str(fi.get("court_opinion") or ""),
                first_verdict_summary=str(fi.get("main_sentence") or ""),
                first_court_opinion=str(fi.get("court_opinion") or ""),
                output_path=output_path,
                trace_recorder=trace_recorder,
                trace_stage_code="CRA",
                trace_stage_key="CRA",
            )
            result = await asyncio.to_thread(scenario.execute)
            self._save_result(case_id, "CRA", result or {})
            if trace_recorder is not None:
                trace_recorder.export_stage(
                    stage_code="CRA",
                    stage_key="CRA",
                    agents=[judge, prosecutor, lawyer, appellant],
                    stage_result=result or {},
                    stage_result_path=case_output_dir / "CRA_result.json",
                    status="completed",
                )
            if self.checkpoint_manager:
                self.checkpoint_manager.mark_scenario_completed(scenario_id)
            scenario_succeeded = True

        except Exception as e:
            logger.exception("[Orchestrator] CRA scenario failed")
            if trace_recorder is not None:
                trace_recorder.export_stage(
                    stage_code="CRA",
                    stage_key="CRA",
                    agents=[judge, prosecutor, lawyer, appellant],
                    stage_result=None,
                    stage_result_path=case_output_dir / "CRA_result.json",
                    status="failed",
                    error=repr(e),
                )
            for agent in (judge, prosecutor, lawyer, appellant):
                if getattr(agent, "is_active", False):
                    agent.recover_from_error()
            await self._report_runtime_issue(case_id=case_id, scenario_type="CRA", exc=e, stage_label="刑事二审庭审")
        finally:
            self._clear_case_stage_active(case_id)
            for agent in (judge, prosecutor, lawyer, appellant):
                if getattr(agent, "is_active", False):
                    agent.deactivate()
            await self._release_trial_slot(court, case_id)

        if not scenario_succeeded:
            return

        await self.event_bus.publish(EventType.CRIMINAL_APPEAL_TRIAL_COMPLETED, {
            "case_id": case_id,
            "client_path": appellant_path,
            "client_id": appellant.agent_id,
            "lawyer_id": lawyer.agent_id,
            "party_role": "defendant",
        })

    async def _on_criminal_final_verdict(self, payload: dict) -> None:
        """CRA完成 → 终审 → 结案。"""
        from ..core.event_bus import EventType

        case_id = str(payload.get("case_id") or "")
        participants = self._collect_case_participant_ids(case_id)
        await self.event_bus.publish(EventType.CASE_CLOSED, {
            "case_id": case_id,
            "client_path": payload.get("client_path") or "",
            "client_id": payload.get("client_id") or "",
            "party_role": "defendant",
            "participant_ids": participants,
        })
        await self.event_bus.publish(EventType.CRIMINAL_FINAL_VERDICT_ISSUED, {
            "case_id": case_id,
            "client_path": payload.get("client_path") or "",
            "client_id": payload.get("client_id") or "",
            "party_role": "defendant",
        })

    async def _choreograph_client_called(self, payload: dict) -> None:
        """CLIENT_CALLED: 等候的当事人被通知，从沙发移动到咨询椅。"""
        client_id = payload.get("client_id", "")
        lawyer_id = payload.get("lawyer_id", "")
        case_id = payload.get("case_id", "")
        firm_id = payload.get("firm_id", "law_firm_A")
        party_role = payload.get("party_role", "plaintiff")  # 从payload读取party_role

        client = self.registry.get_agent(client_id)
        lawyer = self.registry.get_agent(lawyer_id)
        if not client or not lawyer:
            return

        logger.info(f"[Choreography] CLIENT_CALLED: {client.name} ({party_role}) 被通知前往咨询区")

        # 重新执行咨询流程（从沙发站起 → 移动到椅子）
        await self._start_consultation_immediately(
            client, lawyer, case_id, firm_id, party_role, payload
        )

