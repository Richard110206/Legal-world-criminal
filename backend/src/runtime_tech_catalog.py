"""Runtime Tool/Skill catalog for frontend display."""

from __future__ import annotations

import os
from copy import deepcopy
from typing import Any


TRUE_ENV_VALUES = {"1", "true", "yes", "on", "enabled"}


def _law_retrieval_enabled() -> bool:
    return str(os.environ.get("SIMLAW_ENABLE_LAW_RETRIEVAL", "") or "").strip().lower() in TRUE_ENV_VALUES


CORE_TOOLS: tuple[dict[str, str], ...] = (
    {
        "id": "search_laws",
        "display_name": "法条检索",
        "category": "运行工具",
        "description": "检索与当前法律问题相关的本地法条；公开版默认关闭，需下载向量索引后启用。",
        "runtime_status": "disabled_by_default",
    },
    {
        "id": "save_client_memory",
        "display_name": "写入当事人记忆",
        "category": "运行工具",
        "description": "按字段级操作更新当事人的案件长期记忆。",
        "runtime_status": "core",
    },
    {
        "id": "save_lawyer_memory",
        "display_name": "写入律师记忆",
        "category": "运行工具",
        "description": "按字段级操作更新律师的案件长期记忆。",
        "runtime_status": "core",
    },
    {
        "id": "draft_indictment_document",
        "display_name": "生成起诉书",
        "category": "运行工具",
        "description": "接收完整起诉书正文并导出案件 PDF。",
        "runtime_status": "core",
    },
    {
        "id": "draft_defense_opinion_document",
        "display_name": "生成辩护词",
        "category": "运行工具",
        "description": "接收完整辩护词正文并导出案件 PDF。",
        "runtime_status": "core",
    },
    {
        "id": "draft_public_prosecution_document",
        "display_name": "生成公诉词",
        "category": "运行工具",
        "description": "接收完整公诉词正文并导出案件 PDF。",
        "runtime_status": "core",
    },
    {
        "id": "draft_first_instance_criminal_judgment_document",
        "display_name": "生成刑事一审判决书",
        "category": "运行工具",
        "description": "接收完整刑事一审判决书正文并导出 PDF。",
        "runtime_status": "core",
    },
    {
        "id": "draft_second_instance_criminal_judgment_document",
        "display_name": "生成刑事二审判决书",
        "category": "运行工具",
        "description": "接收完整刑事二审判决书正文并导出 PDF。",
        "runtime_status": "core",
    },
    {
        "id": "load_skill",
        "display_name": "加载专业技能",
        "category": "运行工具",
        "description": "把指定 SKILL.md 规则加载到当前 Agent 上下文。",
        "runtime_status": "core",
    },
)

EXTENSION_TOOLS: tuple[dict[str, str], ...] = (
    {
        "id": "search_cases",
        "display_name": "类案检索",
        "category": "扩展能力",
        "description": "检索与当前案情相似的本地案例。",
        "runtime_status": "extension",
    },
    {
        "id": "check_citations",
        "display_name": "法条引用校验",
        "category": "扩展能力",
        "description": "校验文书中引用法条是否存在于本地法律语料。",
        "runtime_status": "extension",
    },
    {
        "id": "compare_documents",
        "display_name": "文书差异比较",
        "category": "扩展能力",
        "description": "比较两份法律文书的共同点、差异和争点变化。",
        "runtime_status": "extension",
    },
    {
        "id": "run_case_benchmark_evaluation",
        "display_name": "单案评测",
        "category": "扩展能力",
        "description": "对指定案件输出目录执行 Benchmark/Eval 评测。",
        "runtime_status": "extension",
    },
    {
        "id": "read_case_artifact",
        "display_name": "读取案件产物",
        "category": "扩展能力",
        "description": "读取当前案件目录下白名单运行产物。",
        "runtime_status": "debug",
    },
    {
        "id": "load_client_memory",
        "display_name": "读取当事人记忆",
        "category": "扩展能力",
        "description": "读取当前案件下当事人的 memory.yaml。",
        "runtime_status": "implemented_not_default",
    },
    {
        "id": "load_lawyer_memory",
        "display_name": "读取律师记忆",
        "category": "扩展能力",
        "description": "读取当前案件下律师的 memory.yaml。",
        "runtime_status": "implemented_not_default",
    },
)

RUNTIME_SKILLS: tuple[dict[str, str], ...] = (
    {
        "id": "lawyer-defense-opinion-drafting",
        "display_name": "辩护词起草规则",
        "category": "专业技能",
        "description": "刑事辩护词正文结构、格式和质量规范。",
        "runtime_status": "runtime",
    },
    {
        "id": "lawyer-criminal-appeal-drafting",
        "display_name": "刑事上诉状起草规则",
        "category": "专业技能",
        "description": "刑事上诉状正文结构、格式和质量规范。",
        "runtime_status": "runtime",
    },
    {
        "id": "lawyer-memory-writing",
        "display_name": "律师记忆写入规则",
        "category": "专业技能",
        "description": "指导律师按字段级 JSON operations 更新案件长期记忆。",
        "runtime_status": "runtime",
    },
    {
        "id": "client-memory-writing",
        "display_name": "当事人记忆写入规则",
        "category": "专业技能",
        "description": "指导当事人按字段级 JSON operations 更新案件长期记忆。",
        "runtime_status": "runtime",
    },
)


def build_runtime_tech_catalog() -> dict[str, Any]:
    """Return the frontend-facing runtime Tool/Skill catalog."""
    core_tools = deepcopy(list(CORE_TOOLS))
    for tool in core_tools:
        if tool.get("id") == "search_laws" and _law_retrieval_enabled():
            tool["runtime_status"] = "core"
    return {
        "tools": {
            "core": core_tools,
            "extension": deepcopy(list(EXTENSION_TOOLS)),
        },
        "skills": {
            "runtime": deepcopy(list(RUNTIME_SKILLS)),
        },
    }


__all__ = [
    "CORE_TOOLS",
    "EXTENSION_TOOLS",
    "RUNTIME_SKILLS",
    "build_runtime_tech_catalog",
]
