"""Registry for manifest-addressable runtime tools — 纯刑事 + 通用。"""

from __future__ import annotations

from typing import Any, Callable

from camel.toolkits import FunctionTool

from ..tools import (
    create_artifact_reader_tool,
    create_case_retrieval_tool,
    create_citation_check_tool,
    create_document_compare_tool,
    create_law_retrieval_tool,
    create_load_client_memory_tool,
    create_load_lawyer_memory_tool,
    create_save_client_memory_tool,
    create_save_lawyer_memory_tool,
    # 元典法条/案例检索
    create_yuandian_case_tool,
    create_yuandian_law_detail_tool,
    create_yuandian_law_tool,
    # 刑事
    create_indictment_drafting_tool,
    create_defense_opinion_drafting_tool,
    create_public_prosecution_drafting_tool,
    create_first_instance_criminal_judgment_drafting_tool,
    create_second_instance_criminal_judgment_drafting_tool,
)


ToolFactory = Callable[[Any], FunctionTool]


def _create_search_laws(agent: Any) -> FunctionTool:
    return create_law_retrieval_tool(agent=agent)


def _create_search_cases(agent: Any) -> FunctionTool:
    return create_case_retrieval_tool(agent=agent)


REGISTERED_STAGE_TOOL_FACTORIES: dict[str, ToolFactory] = {
    # ── 通用 ──
    "search_laws": _create_search_laws,
    "save_client_memory": create_save_client_memory_tool,
    "save_lawyer_memory": create_save_lawyer_memory_tool,
    "load_client_memory": create_load_client_memory_tool,
    "load_lawyer_memory": create_load_lawyer_memory_tool,
    "read_case_artifact": create_artifact_reader_tool,
    "search_cases": _create_search_cases,
    "check_citations": create_citation_check_tool,
    "compare_documents": create_document_compare_tool,
    # ── 元典法条/案例检索（教学溯源）──
    "search_yuandian_law": create_yuandian_law_tool,
    "search_yuandian_law_detail": create_yuandian_law_detail_tool,
    "search_yuandian_case": create_yuandian_case_tool,
    # ── 刑事 ──
    "draft_indictment_document": create_indictment_drafting_tool,
    "draft_defense_opinion_document": create_defense_opinion_drafting_tool,
    "draft_public_prosecution_document": create_public_prosecution_drafting_tool,
    "draft_first_instance_criminal_judgment_document": create_first_instance_criminal_judgment_drafting_tool,
    "draft_second_instance_criminal_judgment_document": create_second_instance_criminal_judgment_drafting_tool,
}


def get_registered_stage_tool_ids() -> list[str]:
    """Return all manifest-available tool ids."""
    return list(REGISTERED_STAGE_TOOL_FACTORIES.keys())


def is_registered_stage_tool(tool_id: str) -> bool:
    """Check whether a tool id exists in the registry."""
    return str(tool_id or "").strip() in REGISTERED_STAGE_TOOL_FACTORIES


def create_registered_stage_tool(tool_id: str, agent: Any) -> FunctionTool:
    """Instantiate one configured runtime tool for an agent."""
    normalized = str(tool_id or "").strip()
    try:
        factory = REGISTERED_STAGE_TOOL_FACTORIES[normalized]
    except KeyError as exc:
        raise KeyError(f"Unknown stage tool id: {tool_id}") from exc
    return factory(agent)


__all__ = [
    "REGISTERED_STAGE_TOOL_FACTORIES",
    "create_registered_stage_tool",
    "get_registered_stage_tool_ids",
    "is_registered_stage_tool",
]
