"""刑法适配 — 集成验证脚本（纯刑事）。

验证：
1. 所有刑事模块可正确导入
2. YAML manifest 通过校验
3. 工具注册表与 manifest 一致
4. 刑事阶段码/Agent类型/角色名称正确注册

运行方式:
    cd backend
    python scripts/verify_criminal.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_backend_dir = Path(__file__).resolve().parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))


def _h1(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def _ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def _fail(msg: str) -> None:
    print(f"  ❌ {msg}")


# ────────────────────────────────────────────────────────────
# Test 1: 模块导入
# ────────────────────────────────────────────────────────────
def test_imports() -> bool:
    _h1("Test 1: 模块导入")
    ok = True

    # 刑事工具
    try:
        from src.tools.legal import (
            IndictmentDraftingTool,
            DefenseOpinionDraftingTool,
            PublicProsecutionTool,
            CriminalFirstInstanceJudgmentDraftingTool,
            CriminalSecondInstanceJudgmentDraftingTool,
            create_indictment_drafting_tool,
            create_defense_opinion_drafting_tool,
            create_public_prosecution_drafting_tool,
            create_first_instance_criminal_judgment_drafting_tool,
            create_second_instance_criminal_judgment_drafting_tool,
        )
        _ok("刑事工具导入 (tools.legal)")
    except Exception as e:
        _fail(f"刑事工具导入失败: {e}")
        ok = False

    # 通用工具（保留）
    try:
        from src.tools.common import create_law_retrieval_tool
        from src.tools.client import create_save_client_memory_tool
        from src.tools.legal import create_save_lawyer_memory_tool
        _ok("通用工具导入 (检索/记忆)")
    except Exception as e:
        _fail(f"通用工具导入失败: {e}")
        ok = False

    # 刑事 Agent
    try:
        from src.agents import ProsecutorAgent, InvestigatorAgent
        _ok("刑事 Agent 导入 (ProsecutorAgent, InvestigatorAgent)")
    except Exception as e:
        _fail(f"刑事 Agent 导入失败: {e}")
        ok = False

    # 刑事场景
    try:
        from src.scenarios import (
            InvestigationScenario,
            ProsecutionReviewScenario,
            DefenseOpinionDraftingScenario,
            CriminalTrialScenario,
            CriminalAppealTrialScenario,
        )
        _ok("刑事场景导入 (INV/PR/DS/CR/CRA)")
    except Exception as e:
        _fail(f"刑事场景导入失败: {e}")
        ok = False

    return ok


# ────────────────────────────────────────────────────────────
# Test 2: YAML Manifest
# ────────────────────────────────────────────────────────────
def test_manifest() -> bool:
    _h1("Test 2: YAML Manifest 校验")
    ok = True

    try:
        from src.pipeline.stage_tool_resolver import (
            load_stage_tool_manifest,
            REAL_STAGE_CODES,
        )
        manifest = load_stage_tool_manifest()
        _ok(f"Manifest 加载成功 (version={manifest['version']})")

        # 检查刑事阶段码存在
        criminal_codes = {"INV", "PR", "DS", "CR", "CRA"}
        declared = set(manifest["stages"].keys())
        missing = criminal_codes - declared
        if missing:
            _fail(f"Manifest 缺少刑事阶段: {missing}")
            ok = False
        else:
            _ok(f"刑事阶段已声明: {sorted(criminal_codes)}")

        # 检查所有 REAL_STAGE_CODES 在 manifest 中
        missing_real = set(REAL_STAGE_CODES) - declared
        if missing_real:
            _fail(f"REAL_STAGE_CODES 中有未在 manifest 声明的: {missing_real}")
            ok = False
        else:
            _ok(f"全部 {len(REAL_STAGE_CODES)} 个阶段码通过")

    except Exception as e:
        _fail(f"Manifest 加载失败: {e}")
        ok = False

    return ok


# ────────────────────────────────────────────────────────────
# Test 3: 工具注册表
# ────────────────────────────────────────────────────────────
def test_registry() -> bool:
    _h1("Test 3: 工具注册表")
    ok = True

    try:
        from src.pipeline.stage_tool_registry import (
            REGISTERED_STAGE_TOOL_FACTORIES,
            get_registered_stage_tool_ids,
        )
        from src.pipeline.stage_tool_resolver import load_stage_tool_manifest

        registered = set(get_registered_stage_tool_ids())
        manifest = load_stage_tool_manifest()
        declared = set(manifest["tool_registry_refs"])

        # 刑事工具在注册表中
        criminal_tools = {
            "draft_indictment_document",
            "draft_defense_opinion_document",
            "draft_public_prosecution_document",
            "draft_first_instance_criminal_judgment",
            "draft_second_instance_criminal_judgment",
        }
        missing_registry = criminal_tools - registered
        if missing_registry:
            _fail(f"注册表缺少刑事工具: {missing_registry}")
            ok = False
        else:
            _ok("5 个刑事工具已注册")

        # 通用工具在注册表中
        common_tools = {
            "search_laws", "save_client_memory", "save_lawyer_memory",
        }
        missing_common = common_tools - registered
        if missing_common:
            _fail(f"注册表缺少通用工具: {missing_common}")
            ok = False
        else:
            _ok("3 个通用工具已注册")

        # 注册表与 manifest 一致
        extra_registry = registered - declared
        extra_declared = declared - registered
        if extra_registry:
            _fail(f"注册表中多余的（未在 manifest 声明的）: {extra_registry}")
            ok = False
        if extra_declared:
            _fail(f"Manifest 声明的未在注册表中: {extra_declared}")
            ok = False
        if not extra_registry and not extra_declared:
            _ok("注册表与 manifest 一致")

        _ok(f"总计 {len(registered)} 个工具")

    except Exception as e:
        _fail(f"注册表验证失败: {e}")
        ok = False

    return ok


# ────────────────────────────────────────────────────────────
# Test 4: 类型与角色常量
# ────────────────────────────────────────────────────────────
def test_constants() -> bool:
    _h1("Test 4: 类型与角色常量")
    ok = True

    from src.pipeline.stage_tool_resolver import (
        ALLOWED_AGENT_TYPES,
        ALLOWED_STAGE_ROLE_NAMES,
        REAL_STAGE_CODES,
    )

    # Agent 类型
    expected_types = {"lawyer", "client", "judge", "receptionist", "prosecutor"}
    actual_types = set(ALLOWED_AGENT_TYPES)
    if expected_types <= actual_types:
        _ok(f"Agent 类型: {sorted(actual_types)}")
    else:
        _fail(f"Agent 类型缺少: {expected_types - actual_types}")
        ok = False

    # 角色名称
    criminal_roles = {"prosecutor", "investigator", "defense_lawyer"}
    actual_roles = set(ALLOWED_STAGE_ROLE_NAMES)
    if criminal_roles <= actual_roles:
        _ok(f"刑事角色已注册: {sorted(criminal_roles)}")
    else:
        _fail(f"刑事角色缺少: {criminal_roles - actual_roles}")
        ok = False

    # 阶段码
    criminal_stages = {"INV", "PR", "DS", "CR", "CRA"}
    actual_stages = set(REAL_STAGE_CODES)
    if criminal_stages <= actual_stages:
        _ok(f"刑事阶段码已注册: {sorted(criminal_stages)}")
    else:
        _fail(f"刑事阶段码缺少: {criminal_stages - actual_stages}")
        ok = False

    return ok


# ────────────────────────────────────────────────────────────
# Test 5: 阶段工具矩阵
# ────────────────────────────────────────────────────────────
def test_stage_matrix() -> bool:
    _h1("Test 5: 阶段工具分配矩阵")
    ok = True

    try:
        from src.pipeline.stage_tool_resolver import describe_stage_tool_matrix

        matrix = describe_stage_tool_matrix()

        # 刑事阶段有工具分配
        criminal_stages = {"INV", "PR", "DS", "CR", "CRA"}
        for stage in criminal_stages:
            roles = matrix.get(stage, {})
            _ok(f"  {stage}: {dict(roles)}")

    except Exception as e:
        _fail(f"阶段矩阵生成失败: {e}")
        ok = False

    return ok


# ────────────────────────────────────────────────────────────
# Test 6: EventBus 事件类型
# ────────────────────────────────────────────────────────────
def test_event_bus() -> bool:
    _h1("Test 6: EventBus 事件类型")
    ok = True

    try:
        from src.core.event_bus import EventType, EventBus

        criminal_events = [
            "INVESTIGATION_STARTED",
            "INVESTIGATION_COMPLETED",
            "PROSECUTION_REVIEW_STARTED",
            "PROSECUTION_REVIEW_COMPLETED",
            "INDICTMENT_DRAFTED",
            "INDICTMENT_FILED",
            "ENTER_DEFENSE_OPINION_DRAFTING",
            "DEFENSE_OPINION_DRAFTING_COMPLETED",
            "ENTER_CRIMINAL_TRIAL",
            "CRIMINAL_TRIAL_COMPLETED",
            "CRIMINAL_VERDICT_ISSUED",
            "ENTER_CRIMINAL_APPEAL_TRIAL",
            "CRIMINAL_APPEAL_TRIAL_COMPLETED",
            "CRIMINAL_FINAL_VERDICT_ISSUED",
        ]

        for evt_name in criminal_events:
            if hasattr(EventType, evt_name):
                _ok(f"  EventType.{evt_name}")
            else:
                _fail(f"  EventType.{evt_name} 不存在")
                ok = False

        # 检查 _RUNTIME_ISSUE_STAGE_MAP
        stage_map = EventBus._RUNTIME_ISSUE_STAGE_MAP
        criminal_map_events = [
            "INVESTIGATION_STARTED",
            "PROSECUTION_REVIEW_STARTED",
            "ENTER_DEFENSE_OPINION_DRAFTING",
            "ENTER_CRIMINAL_TRIAL",
            "ENTER_CRIMINAL_APPEAL_TRIAL",
        ]
        for evt_name in criminal_map_events:
            evt_val = getattr(EventType, evt_name)
            if str(evt_val) in stage_map:
                _ok(f"  _RUNTIME_ISSUE_STAGE_MAP: {evt_name} → {stage_map[str(evt_val)]}")
            else:
                _fail(f"  _RUNTIME_ISSUE_STAGE_MAP 缺少 {evt_name}")
                ok = False

    except Exception as e:
        _fail(f"EventBus 验证失败: {e}")
        ok = False

    return ok


# ────────────────────────────────────────────────────────────
# Test 7: CaseFSM 状态机
# ────────────────────────────────────────────────────────────
def test_case_fsm() -> bool:
    _h1("Test 7: CaseFSM 状态机")
    ok = True

    try:
        from src.orchestration.case_fsm import CaseState, VALID_TRANSITIONS

        criminal_states = [
            "INVESTIGATION",
            "PROSECUTION_REVIEW",
            "DEFENSE_OPINION_DRAFTING",
            "DEFENSE_OPINION_FILED",
            "INDICTMENT_FILED",
            "WAITING_FOR_CRIMINAL_TRIAL",
            "CRIMINAL_TRIAL_FIRST_INSTANCE",
            "CRIMINAL_FIRST_INSTANCE_VERDICT",
            "CRIMINAL_APPEAL_DECISION",
            "CRIMINAL_APPEAL_DRAFTING",
            "CRIMINAL_APPEAL_FILED",
            "WAITING_FOR_CRIMINAL_SECOND_TRIAL",
            "CRIMINAL_TRIAL_SECOND_INSTANCE",
            "CRIMINAL_FINAL_VERDICT",
        ]

        for state_name in criminal_states:
            if hasattr(CaseState, state_name):
                state_val = getattr(CaseState, state_name)
                transitions = VALID_TRANSITIONS.get(state_val, set())
                _ok(f"  CaseState.{state_name} → {sorted(transitions) if transitions else '(终态)'}")
            else:
                _fail(f"  CaseState.{state_name} 不存在")
                ok = False

    except Exception as e:
        _fail(f"CaseFSM 验证失败: {e}")
        ok = False

    return ok


# ────────────────────────────────────────────────────────────
# Test 8: Player Lawyer agent
# ────────────────────────────────────────────────────────────
def test_player_lawyer() -> bool:
    _h1("Test 8: Player Lawyer 模式")
    ok = True

    try:
        from src.player_lawyer.agent import (
            PlayerLawyerAgent,
            PlayerPlaintiffLawyerAgent,
            resolve_player_party_role,
            is_player_defendant_mode,
            is_player_mode_enabled,
        )
        _ok("PlayerLawyerAgent 导入成功")

        # 验证 resolve 在没有环境变量时返回 None
        assert PlayerPlaintiffLawyerAgent is PlayerLawyerAgent
        _ok("PlayerPlaintiffLawyerAgent 别名兼容")

        role = resolve_player_party_role()
        _ok(f"当前默认模式: {role or '未启用 (None)'}")
        _ok(f"  is_player_defendant_mode() = {is_player_defendant_mode()}")
        _ok(f"  is_player_mode_enabled() = {is_player_mode_enabled()}")

    except Exception as e:
        _fail(f"Player Lawyer 验证失败: {e}")
        ok = False

    return ok


# ────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────
def main() -> None:
    print("=" * 60)
    print("  LEGALWORLD 刑法适配 — 集成验证")
    print("=" * 60)

    all_ok = True
    all_ok &= test_imports()
    all_ok &= test_constants()
    all_ok &= test_registry()
    all_ok &= test_manifest()
    all_ok &= test_stage_matrix()
    all_ok &= test_event_bus()
    all_ok &= test_case_fsm()
    all_ok &= test_player_lawyer()

    _h1("结果")
    if all_ok:
        print("\n  ✅ 全部验证通过！\n")
    else:
        print("\n  ❌ 部分验证失败，请检查上述输出。\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
