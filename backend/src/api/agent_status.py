"""Agent serialisation, sandbox runtime context & status builders."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException

from src.core.file_storage_manager import FileStorageManager
from src.core.models import Sandbox
from src.core.sandbox_manager import SandboxManager, SandboxRuntimeContext
from src.orchestration.agent_registry import AgentRegistry
from src.pipeline.stage_tool_resolver import (
    infer_stage_role_name,
    resolve_agent_type,
    resolve_configured_tool_names,
)
from src.player_lawyer.agent import is_player_defendant_mode as _is_player_defendant_mode
from src.simulation.location_registry import load_registry_from_map
from src.simulation.ws_frontend_engine import WebSocketFrontendEngine
from src.tools.common.skill_loader_tool import _FlatSkillToolkit
from src.utils.case_progress import normalize_case_state

from . import app_state
from .case_catalog import (
    _get_context_selected_case_id,
    _load_yaml_mapping,
    _normalize_case_identifier,
    _set_context_case_selection,
)

logger = logging.getLogger("ws_server")


def _normalize_firm_building_type(firm_id: str) -> str:
    normalized = "".join(ch for ch in str(firm_id or "").lower() if ch.isalnum())
    if normalized in {"lawfirma", "lawfirma1"}:
        return "LawfirmA"
    if normalized in {"lawfirmb", "lawfirmb1"}:
        return "LawfirmB"
    return "LawfirmA"


def _build_agent_building_type(agent: Any) -> str:
    agent_type = str(getattr(agent, "agent_type", "") or "").strip().lower()
    if agent_type in {"lawyer", "receptionist"}:
        firm_id = str(getattr(agent, "firm_id", "") or "").strip()
        if not firm_id:
            config_path = Path(str(getattr(agent, "config_path", "") or ""))
            if config_path.parts:
                with contextlib.suppress(ValueError):
                    parts = list(config_path.parts)
                    idx = parts.index("law_firms")
                    firm_id = parts[idx + 1]
        return _normalize_firm_building_type(firm_id)
    if agent_type == "judge":
        return "Court"
    if agent_type == "client":
        return "community"
    return "community"


def _load_agent_shell_config(agent: Any, storage: FileStorageManager | None) -> dict[str, Any]:
    if storage is None:
        return {}

    config_path = str(getattr(agent, "config_path", "") or "").strip()
    if not config_path:
        return {}

    with contextlib.suppress(FileNotFoundError, OSError, ValueError, yaml.YAMLError):
        config = storage.load_agent_config(config_path)
        if isinstance(config, dict):
            return config
    return {}


def _build_agent_character_payload(agent: Any, storage: FileStorageManager | None) -> dict[str, Any]:
    config = _load_agent_shell_config(agent, storage)
    profile = config.get("profile", {})
    if not isinstance(profile, dict):
        profile = {}
    map_state = config.get("map_state", {})
    if not isinstance(map_state, dict):
        map_state = {}

    agent_type = str(getattr(agent, "agent_type", "") or "").strip().lower()
    building_type = _build_agent_building_type(agent)
    character_name = (
        str(profile.get("character_name", "") or "").strip()
        or str(config.get("character_name", "") or "").strip()
        or str(map_state.get("character_name", "") or "").strip()
    )
    law_firm = (
        str(profile.get("law_firm", "") or "").strip()
        or str(getattr(agent, "law_firm", "") or "").strip()
        or (
            "金杜律师事务所"
            if building_type == "LawfirmA"
            else "君合律师事务所" if building_type == "LawfirmB" else ""
        )
    )
    court_name = (
        str(profile.get("court_name", "") or "").strip()
        or str(getattr(agent, "court_name", "") or "").strip()
    )
    specialty_areas = profile.get("specialty") or getattr(agent, "specialty_areas", []) or []
    if not isinstance(specialty_areas, list):
        specialty_areas = []

    occupation = str(profile.get("occupation", "") or "").strip()
    if not occupation:
        if agent_type == "lawyer":
            occupation = "律师"
        elif agent_type == "judge":
            occupation = "法官"
        elif agent_type == "receptionist":
            occupation = "前台"
        else:
            occupation = "居民"

    character_info = {
        "name": str(profile.get("name", "") or getattr(agent, "name", "") or "").strip(),
        "gender": str(profile.get("gender", "") or getattr(agent, "gender", "") or "未知").strip() or "未知",
        "age": profile.get("age"),
        "occupation": occupation,
        "personality": str(profile.get("personality", "") or getattr(agent, "personality", "") or "").strip(),
        "speaking_style": str(profile.get("speaking_style", "") or getattr(agent, "speaking_style", "") or "").strip(),
        "background": str(profile.get("background", "") or "").strip(),
        "description": str(profile.get("description", "") or "").strip(),
        "character_name": character_name,
        "sprite_index": profile.get("sprite_index"),
        "law_firm": law_firm or None,
        "court": court_name or None,
        "specialty_areas": specialty_areas,
        "years_of_experience": (
            profile.get("years_of_experience")
            or getattr(agent, "years_of_experience", None)
        ),
        "legal_big_five": profile.get("legal_big_five"),
    }

    return {
        "npc_id": str(getattr(agent, "agent_id", "") or "").strip(),
        "npc_name": str(getattr(agent, "name", "") or "").strip(),
        "agent_type": agent_type,
        "building_type": building_type,
        "law_firm": law_firm or None,
        "court": court_name or None,
        "specialty_areas": specialty_areas,
        "years_of_experience": character_info["years_of_experience"],
        "character_name": character_name or None,
        "sprite_index": character_info["sprite_index"],
        "gender": character_info["gender"],
        "occupation": occupation,
        "personality": character_info["personality"],
        "speaking_style": character_info["speaking_style"],
        "background": character_info["background"],
        "description": character_info["description"],
        "character_info": character_info,
    }


def _serialize_registry_agents(
    agent_registry: AgentRegistry | None,
    storage: FileStorageManager | None,
) -> dict[str, Any]:
    if agent_registry is None:
        return {"agents": {}, "items": []}

    agent_map: dict[str, dict[str, Any]] = {}
    items: list[dict[str, Any]] = []
    for agent in agent_registry.get_all_agents():
        meta = _build_agent_character_payload(agent, storage)
        agent_id = str(meta.get("npc_id", "") or "").strip()
        if not agent_id:
            continue
        agent_map[agent_id] = meta
        items.append(
            {
                "id": agent_id,
                "name": meta.get("npc_name"),
                "type": meta.get("agent_type"),
                "law_firm": meta.get("law_firm"),
                "building_type": meta.get("building_type"),
            }
        )
    return {"agents": agent_map, "items": items}


def _build_sandbox_runtime_context(sandbox: Sandbox, storage_root: Path) -> SandboxRuntimeContext:
    runtime_engine = WebSocketFrontendEngine(
        load_registry_from_map(app_state.MAP_JSON_PATH),
        fallback_speed=0.5,
        backend_authoritative=True,
        move_speed_px_per_second=150.0,
        map_json_path=app_state.MAP_JSON_PATH,
        frontend_mode=app_state.SIMLAW_FRONTEND_MODE,
        turn_mode=app_state.SIMLAW_TURN_MODE,
    )
    # 教学技能卡：把当前学生（登录用户）的技能卡目录注入律师 agent 的技能搜索路径
    try:
        from src.agents.lawyer_agent import set_student_skill_card_dir
        from src.teaching.skill_card import student_skill_dir

        _card_dir = student_skill_dir(str(sandbox.user_id))
        if _card_dir.is_dir():
            set_student_skill_card_dir(str(_card_dir))
            logger.info("[SkillCard] injecting student skill cards for user %s from %s", sandbox.user_id, _card_dir)
        else:
            set_student_skill_card_dir(None)
    except Exception as exc:
        logger.debug("[SkillCard] skill card injection skipped: %s", exc)

    runtime_event_bus, runtime_registry, runtime_checkpoint_mgr, runtime_storage, runtime_case_fsm = (
        _initialize_runtime_state_lazily(
            existing_engine=runtime_engine,
            sandbox_data_dir=storage_root,
            set_global_engine=False,
        )
    )
    context = SandboxRuntimeContext(
        sandbox_id=sandbox.id,
        user_id=sandbox.user_id,
        sandbox_key=sandbox.sandbox_key,
        storage_root=storage_root,
        engine=runtime_engine,
        event_bus=runtime_event_bus,
        registry=runtime_registry,
        checkpoint_mgr=runtime_checkpoint_mgr,
        storage_manager=runtime_storage,
        case_fsm=runtime_case_fsm,
    )
    context.orchestrator = getattr(runtime_engine, "orchestrator", None)
    if _is_player_defendant_mode():
        from .player_gateway_admin import get_or_create_player_gateway  # deferred: cycle break

        player_gateway = get_or_create_player_gateway(sandbox.id, storage_root)
        setattr(context, "player_gateway", player_gateway)

        try:
            player_event_loop = asyncio.get_running_loop()
        except RuntimeError:
            player_event_loop = None

        def _broadcast_player_lawyer_event(event_type: str, data: dict) -> None:
            payload = {"type": event_type, "event": event_type, "data": data}
            if player_event_loop is None or not player_event_loop.is_running():
                return
            asyncio.run_coroutine_threadsafe(
                _broadcast_sandbox_event_lazily(str(sandbox.id), payload),
                player_event_loop,
            )

        if context.orchestrator is not None:
            setattr(context.orchestrator, "_player_gateway", player_gateway)
            setattr(context.orchestrator, "_player_broadcast_fn", _broadcast_player_lawyer_event)
            setattr(context.orchestrator, "_sandbox_id", sandbox.id)
            setattr(context.orchestrator, "_teaching_student_id", str(sandbox.user_id))

    async def _runtime_issue_reporter(
        *,
        case_id: str,
        scenario_type: str,
        exc: Exception,
        stage_label: str = "",
        event_type: str = "",
        handler_name: str = "",
    ) -> bool:
        payload = _normalize_runtime_issue_from_exception(
            case_id=case_id,
            scenario_type=scenario_type,
            exc=exc,
            stage_label=stage_label,
            event_type=event_type,
            handler_name=handler_name,
        )
        if payload is None:
            return False
        return await _report_sandbox_runtime_issue(context, payload)

    setattr(runtime_engine, "runtime_issue_reporter", _runtime_issue_reporter)
    setattr(runtime_event_bus, "runtime_issue_reporter", _runtime_issue_reporter)
    if context.orchestrator is not None:
        setattr(context.orchestrator, "runtime_issue_reporter", _runtime_issue_reporter)

    get_agents_by_type = getattr(runtime_registry, "get_agents_by_type", None)
    if callable(get_agents_by_type):
        for receptionist in get_agents_by_type("receptionist"):
            setattr(receptionist, "runtime_issue_reporter", _runtime_issue_reporter)

    return context


def _set_runtime_engine_paused(runtime_engine: WebSocketFrontendEngine | None, paused: bool) -> None:
    if runtime_engine is None:
        return
    runtime_engine._paused = paused
    if paused:
        runtime_engine._resumed_event.clear()
    else:
        runtime_engine._resumed_event.set()


def _tool_name_from_record(record: Any) -> str:
    if isinstance(record, dict):
        for key in ("name", "tool_name", "function_name"):
            value = str(record.get(key) or "").strip()
            if value:
                return value
        function_payload = record.get("function")
        if isinstance(function_payload, dict):
            return str(function_payload.get("name") or "").strip()

    for attr in ("name", "tool_name", "function_name"):
        value = str(getattr(record, attr, "") or "").strip()
        if value:
            return value
    function_payload = getattr(record, "function", None)
    return str(getattr(function_payload, "name", "") or "").strip()


def _tool_names_for_agent(agent: Any) -> list[str]:
    names: list[str] = []
    for tool in list(getattr(agent, "tools", []) or []):
        name = ""
        if hasattr(tool, "get_function_name"):
            with contextlib.suppress(Exception):
                name = str(tool.get_function_name() or "").strip()
        if not name:
            name = str(getattr(tool, "name", "") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _role_name_for_agent(agent: Any, stage_code: str) -> str:
    explicit = str(getattr(agent, "_simlaw_stage_role", "") or "").strip()
    if explicit:
        return explicit
    if not stage_code:
        return ""
    with contextlib.suppress(Exception):
        return infer_stage_role_name(stage_code, agent)
    return ""


def _agent_type_for_status(agent: Any) -> str:
    with contextlib.suppress(Exception):
        return str(resolve_agent_type(agent)).strip()
    class_name = type(agent).__name__
    if class_name == "PlayerPlaintiffLawyerAgent":
        return "player_lawyer"
    return class_name.replace("Agent", "").lower() or "agent"


def _configured_tool_names_for_agent(agent: Any, stage_code: str, role_name: str) -> list[str]:
    explicit = [
        str(name).strip()
        for name in list(getattr(agent, "_simlaw_configured_tool_names", []) or [])
        if str(name).strip()
    ]
    if explicit:
        return explicit
    if not stage_code or not role_name:
        return []
    with contextlib.suppress(Exception):
        return resolve_configured_tool_names(stage_code, role_name, resolve_agent_type(agent))
    return []


def _skill_usage_for_agent(agent: Any) -> dict[str, Any]:
    reporter = getattr(agent, "get_skill_usage_report", None)
    if not callable(reporter):
        return {}
    with contextlib.suppress(Exception):
        payload = reporter()
        if isinstance(payload, dict):
            return payload
    return {}


def _available_skill_names_for_agent(agent: Any, stage_code: str = "") -> list[str]:
    skill_dirs = list(getattr(agent, "skill_dirs", []) or [])
    if not skill_dirs:
        return []
    with contextlib.suppress(Exception):
        toolkit = _FlatSkillToolkit([str(item) for item in skill_dirs if str(item or "").strip()])
        names = [
            str(item.get("name") or "").strip()
            for item in toolkit.list_skills()
            if str(item.get("name") or "").strip()
        ]
        agent_type = _agent_type_for_status(agent)
        if agent_type == "client":
            return [name for name in names if name.startswith("client-")]
        if agent_type == "lawyer":
            stage_skill_map = {
                "CD": "lawyer-complaint-drafting",
                "DD": "lawyer-defense-drafting",
                "AD": "lawyer-appeal-drafting",
                "AR": "lawyer-appeal-response-drafting",
            }
            preferred = ["lawyer-memory-writing"]
            stage_skill = stage_skill_map.get(str(stage_code or "").strip().upper())
            if stage_skill:
                preferred.append(stage_skill)
            filtered = [name for name in names if name in preferred]
            return filtered or [name for name in names if name.startswith("lawyer-")]
        return names
    return []


def _stage_code_from_case_state(raw_state: Any) -> str:
    state = normalize_case_state(str(raw_state or "").strip())
    if not state or state in {"空闲", "已结案"}:
        return ""
    exact_map = {
        "委托洽谈中": "LC",
        "侦查阶段": "INV",
        "审查起诉阶段": "PR",
        "辩护词起草中": "DS",
        "辩护词已递交": "DS",
        "起诉书已递交": "PR",
        "刑事一审庭审中": "CR",
        "刑事二审庭审中": "CRA",
    }
    if state in exact_map:
        return exact_map[state]
    if "刑事二审" in state or "刑事上诉" in state or "等待刑事二审" in state:
        return "CRA"
    if "刑事一审" in state or "等待刑事一审" in state:
        return "CR"
    if "辩护词" in state:
        return "DS"
    if "审查起诉" in state or "起诉书" in state:
        return "PR"
    if "侦查" in state:
        return "INV"
    if "委托洽谈" in state:
        return "LC"
    return ""


def _load_status_agent_config(storage: Any, agent: Any) -> dict[str, Any]:
    config_path = getattr(agent, "config_path", None)
    if storage is None or not config_path:
        return {}
    with contextlib.suppress(Exception):
        config = storage.load_agent_config(config_path)
        if isinstance(config, dict):
            return config
    return {}


def _infer_case_stage_from_storage(storage: Any, case_id: str, fallback_config: dict[str, Any] | None = None) -> str:
    normalized_case_id = _normalize_case_identifier(case_id)
    if not normalized_case_id:
        return ""

    with contextlib.suppress(Exception):
        case_runtime = storage.load_case_runtime(normalized_case_id)
        stage_code = _stage_code_from_case_state(case_runtime.get("overall_state"))
        if stage_code:
            return stage_code

    config = fallback_config or {}
    stage_code = _stage_code_from_case_state(config.get("case_state"))
    if stage_code:
        return stage_code

    case_dir = Path(getattr(storage, "base_dir", "")) / "cases" / normalized_case_id
    for party_role in ("plaintiff", "defendant"):
        party_config = _load_yaml_mapping(case_dir / party_role / "config.yaml")
        stage_code = _stage_code_from_case_state(party_config.get("case_state"))
        if stage_code:
            return stage_code
    return ""


def _case_context_by_agent_id(context: SandboxRuntimeContext) -> dict[str, dict[str, str]]:
    storage = getattr(context, "storage_manager", None)
    registry = getattr(context, "registry", None)
    if storage is None or registry is None:
        return {}

    get_agents_by_type = getattr(registry, "get_agents_by_type", None)
    if not callable(get_agents_by_type):
        return {}

    context_by_agent: dict[str, dict[str, str]] = {}
    case_stage_by_id: dict[str, str] = {}

    for client in list(get_agents_by_type("client") or []):
        config = _load_status_agent_config(storage, client)
        case_id = _normalize_case_identifier(config.get("case_id"))
        if not case_id:
            continue
        stage_code = _infer_case_stage_from_storage(storage, case_id, config)
        if stage_code:
            case_stage_by_id[case_id] = stage_code
        client_stage_code = _stage_code_from_case_state(config.get("case_state"))
        agent_id = str(getattr(client, "agent_id", "") or "").strip()
        if agent_id:
            context_by_agent[agent_id] = {
                "case_id": case_id,
                "stage_code": client_stage_code,
                "party_role": str(config.get("party_role") or "").strip(),
            }
        assigned_lawyer_id = str(config.get("assigned_lawyer_id") or "").strip()
        if assigned_lawyer_id:
            context_by_agent.setdefault(
                assigned_lawyer_id,
                {
                    "case_id": case_id,
                    "stage_code": stage_code,
                    "party_role": str(config.get("party_role") or "").strip(),
                },
            )

    for lawyer in list(get_agents_by_type("lawyer") or []):
        agent_id = str(getattr(lawyer, "agent_id", "") or "").strip()
        if not agent_id:
            continue
        config = _load_status_agent_config(storage, lawyer)
        candidate_case_ids = [
            str(config.get("current_handling_case") or "").strip(),
            *[str(item or "").strip() for item in list(config.get("case_queue") or [])],
        ]
        for candidate_case_id in candidate_case_ids:
            case_id = _normalize_case_identifier(candidate_case_id)
            if not case_id:
                continue
            stage_code = case_stage_by_id.get(case_id) or _infer_case_stage_from_storage(storage, case_id)
            context_by_agent[agent_id] = {
                "case_id": case_id,
                "stage_code": stage_code,
                "party_role": context_by_agent.get(agent_id, {}).get("party_role", ""),
            }
            break

    return context_by_agent


def _active_agent_context(context: SandboxRuntimeContext) -> dict[str, dict[str, str]]:
    event_bus = getattr(context, "event_bus", None)
    if event_bus is None or not hasattr(event_bus, "get_active_scenarios_snapshot"):
        return {}

    active_by_agent: dict[str, dict[str, str]] = {}
    with contextlib.suppress(Exception):
        active_scenarios = event_bus.get_active_scenarios_snapshot()
        for case_id, scenario_info in dict(active_scenarios or {}).items():
            scenario_type = str(scenario_info.get("scenario_type") or "").strip()
            for agent_id in list(scenario_info.get("participants") or []):
                normalized_agent_id = str(agent_id or "").strip()
                if not normalized_agent_id:
                    continue
                active_by_agent[normalized_agent_id] = {
                    "case_id": str(case_id or "").strip(),
                    "scenario_type": scenario_type,
                }
    return active_by_agent


def _serialize_agent_capabilities(context: SandboxRuntimeContext) -> list[dict[str, Any]]:
    registry = getattr(context, "registry", None)
    if registry is None or not hasattr(registry, "get_all_agents"):
        return []

    active_by_agent = _active_agent_context(context)
    persisted_context_by_agent = _case_context_by_agent_id(context)
    capabilities: list[dict[str, Any]] = []
    for agent in list(registry.get_all_agents() or []):
        agent_id = str(getattr(agent, "agent_id", "") or "").strip()
        if not agent_id:
            continue

        active_context = active_by_agent.get(agent_id, {})
        persisted_context = persisted_context_by_agent.get(agent_id, {})
        stage_code = str(
            getattr(agent, "_simlaw_stage_code", "")
            or active_context.get("scenario_type")
            or persisted_context.get("stage_code")
            or ""
        ).strip().upper()
        role_name = _role_name_for_agent(agent, stage_code) or persisted_context.get("party_role", "")
        tool_names = _tool_names_for_agent(agent)
        available_tool_names = [
            str(name).strip()
            for name in list(getattr(agent, "_simlaw_available_tool_names", []) or tool_names)
            if str(name).strip()
        ]
        configured_tool_names = _configured_tool_names_for_agent(agent, stage_code, role_name)
        actual_tool_calls = []
        for record in list(getattr(agent, "_last_tool_call_records", []) or []):
            tool_name = _tool_name_from_record(record)
            if tool_name and tool_name not in actual_tool_calls:
                actual_tool_calls.append(tool_name)
        skill_usage = _skill_usage_for_agent(agent)
        skills = [
            {
                "name": str(item.get("name") or "").strip(),
                "load_count": int(item.get("load_count") or 0),
            }
            for item in list(skill_usage.get("skills") or [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]

        capabilities.append(
            {
                "agent_id": agent_id,
                "agent_name": str(getattr(agent, "name", "") or agent_id).strip(),
                "agent_type": _agent_type_for_status(agent),
                "agent_class": type(agent).__name__,
                "agent_role": role_name,
                "stage_code": stage_code,
                "case_id": active_context.get("case_id", "") or persisted_context.get("case_id", ""),
                "is_active": agent_id in active_by_agent or bool(getattr(agent, "is_active", False)),
                "configured_tool_names": configured_tool_names,
                "available_tool_names": available_tool_names,
                "actual_tool_calls": actual_tool_calls,
                "actual_tool_call_count": len(list(getattr(agent, "_last_tool_call_records", []) or [])),
                "skill_load_count": int(skill_usage.get("skill_load_count") or 0),
                "skill_names": [item["name"] for item in skills],
                "available_skill_names": _available_skill_names_for_agent(agent, stage_code),
                "has_skill_tool": "load_skill" in available_tool_names or bool(getattr(agent, "skill_dirs", []) or []),
                "is_player_agent": type(agent).__name__ == "PlayerPlaintiffLawyerAgent",
            }
        )

    capabilities.sort(
        key=lambda item: (
            0 if item.get("is_active") else 1,
            str(item.get("stage_code") or ""),
            str(item.get("agent_role") or ""),
            str(item.get("agent_id") or ""),
        )
    )
    return capabilities


def _build_sandbox_runtime_status(context: SandboxRuntimeContext) -> dict[str, Any]:
    task = context.simulation_task
    task_running = bool(task is not None and not task.done())
    paused = bool(context.engine and getattr(context.engine, "_paused", False))
    session_state = None
    if context.checkpoint_mgr is not None and hasattr(context.checkpoint_mgr, "load_session_state"):
        session_state = context.checkpoint_mgr.load_session_state()
    persisted_status = str((session_state or {}).get("simulation_status") or "").strip().lower()
    last_error = context.last_error

    if last_error:
        status = "error"
    elif task_running:
        status = "paused" if paused else "running"
    elif persisted_status in {"paused", "running"}:
        status = "paused"
    elif persisted_status == "completed" and getattr(context, "single_case_mode", False):
        status = "idle"
    elif persisted_status == "completed":
        status = "completed"
    else:
        status = "idle"

    clients_connected = len(context.connected_clients)
    if not clients_connected and getattr(context.engine, "clients", None) is not None:
        clients_connected = len(context.engine.clients)

    return {
        "status": status,
        "session_id": (session_state or {}).get("session_id"),
        "selected_case_id": _get_context_selected_case_id(context),
        "paused": status == "paused",
        "simulation_running": False if last_error else task_running,
        "clients_connected": clients_connected,
        "active_cases": 0 if last_error else _count_active_cases_lazily(context.storage_manager, context.registry),
        "last_error": last_error,
        "agent_capabilities": _serialize_agent_capabilities(context),
    }


def _start_or_resume_sandbox_context(
    context: SandboxRuntimeContext,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # deferred import: simulation_runtime imports this module at top level
    from .simulation_runtime import resume_simulation, run_simulation

    if (
        context.engine is None
        or context.event_bus is None
        or context.registry is None
        or context.checkpoint_mgr is None
        or context.storage_manager is None
        or context.case_fsm is None
    ):
        raise HTTPException(status_code=503, detail="Sandbox runtime not ready")

    payload = payload or {}
    requested_case_id = _normalize_case_identifier(payload.get("case_id"))
    existing_case_id = _get_context_selected_case_id(context)
    if requested_case_id:
        if existing_case_id and existing_case_id != requested_case_id and context.simulation_task is not None and not context.simulation_task.done():
            raise HTTPException(status_code=409, detail="当前已有其他案件在运行，请先等待其结束或重新开始")
        _set_context_case_selection(context, requested_case_id)
    elif not existing_case_id:
        raise HTTPException(status_code=400, detail="缺少 case_id")

    selected_case_id = _get_context_selected_case_id(context)
    context.last_error = None

    if context.simulation_task is not None and not context.simulation_task.done():
        _set_runtime_engine_paused(context.engine, False)
        context.checkpoint_mgr.mark_session_running()
        return _build_sandbox_runtime_status(context)

    session_state = context.checkpoint_mgr.load_session_state()
    _set_runtime_engine_paused(context.engine, False)

    if session_state and session_state.get("simulation_status") in {"running", "paused"}:
        context.checkpoint_mgr.mark_session_running()
        context.simulation_task = asyncio.create_task(
            resume_simulation(
                context.engine,
                context.event_bus,
                context.registry,
                context.storage_manager,
                context.case_fsm,
                context.checkpoint_mgr,
                selected_case_id=selected_case_id,
            )
        )
    else:
        context.checkpoint_mgr.create_new_session()
        _set_context_case_selection(context, selected_case_id)
        context.simulation_task = asyncio.create_task(
            run_simulation(
                context.engine,
                context.event_bus,
                context.registry,
                context.storage_manager,
                context.case_fsm,
                context.checkpoint_mgr,
                selected_case_id=selected_case_id,
            )
        )
        context.simulation_task.add_done_callback(_log_task_result_lazily)
    return _build_sandbox_runtime_status(context)
def _serialize_sandbox_state(
    sandbox: Sandbox | None,
    *,
    runtime_status: dict | None = None,
) -> dict:
    if sandbox is None:
        return {
            "status": "not_created",
            "selected_case_id": "",
            "active_cases": 0,
            "clients_connected": 0,
            "can_start": True,
            "can_pause": False,
            "can_restart": False,
            "last_error": None,
            "agent_capabilities": [],
        }

    state = runtime_status or {}
    status = str(state.get("status") or sandbox.status)
    return {
        "id": sandbox.id,
        "user_id": sandbox.user_id,
        "sandbox_key": sandbox.sandbox_key,
        "storage_root": sandbox.storage_root,
        "status": status,
        "session_id": state.get("session_id"),
        "selected_case_id": state.get("selected_case_id") or "",
        "active_cases": int(state.get("active_cases", 0) or 0),
        "clients_connected": int(state.get("clients_connected", 0) or 0),
        "can_start": status in {"idle", "paused", "completed"},
        "can_pause": status == "running",
        "can_restart": sandbox is not None,
        "last_error": state.get("last_error"),
        "agent_capabilities": list(state.get("agent_capabilities") or []),
    }
def _sandbox_storage_has_seed_data(storage_root: Path) -> bool:
    return (
        (storage_root / "case_data_extracted.json").exists()
        and any(storage_root.glob("cases/case_*/plaintiff/config.yaml"))
        and any(storage_root.glob("law_firms/*/lawyer_roster.yaml"))
        and any(storage_root.glob("court_system/*/judges/*/config.yaml"))
    )


def _sandbox_context_needs_rebuild(context: SandboxRuntimeContext, storage_root: Path) -> bool:
    task = getattr(context, "simulation_task", None)
    task_running = bool(task is not None and not task.done())
    registry = getattr(context, "registry", None)
    get_all_agents = getattr(registry, "get_all_agents", None)
    agents = get_all_agents() if callable(get_all_agents) else []
    return (not task_running) and not agents and _sandbox_storage_has_seed_data(storage_root)


# bottom import to break the cycle agent_status <-> runtime_issues
from .runtime_issues import (  # noqa: E402, F401
    _normalize_runtime_issue_from_exception,
    _report_sandbox_runtime_issue,
)


# lazy trampolines to break the cycle agent_status <-> simulation_runtime
# (simulation_runtime imports agent_status at module top level)
def _initialize_runtime_state_lazily(**kwargs):
    from .simulation_runtime import _initialize_runtime_state

    return _initialize_runtime_state(**kwargs)


async def _broadcast_sandbox_event_lazily(*args, **kwargs):
    from .simulation_runtime import _broadcast_sandbox_event

    return await _broadcast_sandbox_event(*args, **kwargs)


def _count_active_cases_lazily(*args, **kwargs):
    from .simulation_runtime import _count_active_cases

    return _count_active_cases(*args, **kwargs)


def _log_task_result_lazily(*args, **kwargs):
    from .simulation_runtime import _log_task_result

    return _log_task_result(*args, **kwargs)


def _get_sandbox_manager() -> SandboxManager:
    if app_state.sandbox_manager is None:
        app_state.sandbox_manager = SandboxManager(
            base_dir=app_state.SANDBOX_DATA_DIR,
            runtime_factory=_build_sandbox_runtime_context,
            start_handler=_start_or_resume_sandbox_context,
            status_handler=_build_sandbox_runtime_status,
        )
    return app_state.sandbox_manager
