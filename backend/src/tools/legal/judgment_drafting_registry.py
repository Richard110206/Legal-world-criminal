"""Registry and payload helpers for judgment PDF tools — 纯刑事。"""

from __future__ import annotations

from typing import Any, Dict

from .document_drafting_support import extract_json_payload
from .criminal_first_instance_judgment_drafting_tool import (
    CRIMINAL_FIRST_INSTANCE_JUDGMENT_DOCUMENT_TYPE,
    CriminalFirstInstanceJudgmentDraftingTool,
    create_first_instance_criminal_judgment_drafting_tool,
)
from .criminal_second_instance_judgment_drafting_tool import (
    CRIMINAL_SECOND_INSTANCE_JUDGMENT_DOCUMENT_TYPE,
    CriminalSecondInstanceJudgmentDraftingTool,
    create_second_instance_criminal_judgment_drafting_tool,
)


SCENARIO_TO_JUDGMENT_DOCUMENT_TYPE = {
    "CR": CRIMINAL_FIRST_INSTANCE_JUDGMENT_DOCUMENT_TYPE,
    "CRA": CRIMINAL_SECOND_INSTANCE_JUDGMENT_DOCUMENT_TYPE,
}

JUDGMENT_DOCUMENT_TYPE_TO_TOOL_NAME = {
    CRIMINAL_FIRST_INSTANCE_JUDGMENT_DOCUMENT_TYPE: "draft_first_instance_criminal_judgment_document",
    CRIMINAL_SECOND_INSTANCE_JUDGMENT_DOCUMENT_TYPE: "draft_second_instance_criminal_judgment_document",
}

JUDGMENT_DOCUMENT_TYPE_TO_RENDERER = {
    CRIMINAL_FIRST_INSTANCE_JUDGMENT_DOCUMENT_TYPE: (
        lambda agent, text: CriminalFirstInstanceJudgmentDraftingTool(
            agent
        ).draft_first_instance_criminal_judgment_document(text)
    ),
    CRIMINAL_SECOND_INSTANCE_JUDGMENT_DOCUMENT_TYPE: (
        lambda agent, text: CriminalSecondInstanceJudgmentDraftingTool(
            agent
        ).draft_second_instance_criminal_judgment_document(text)
    ),
}

JUDGMENT_DOCUMENT_TYPE_TO_FACTORY = {
    CRIMINAL_FIRST_INSTANCE_JUDGMENT_DOCUMENT_TYPE: create_first_instance_criminal_judgment_drafting_tool,
    CRIMINAL_SECOND_INSTANCE_JUDGMENT_DOCUMENT_TYPE: create_second_instance_criminal_judgment_drafting_tool,
}


def normalize_judgment_document_type(document_type: str) -> str:
    value = str(document_type or "").strip().lower()
    if value in JUDGMENT_DOCUMENT_TYPE_TO_TOOL_NAME:
        return value

    scenario_type = str(document_type or "").strip().upper()
    if scenario_type in SCENARIO_TO_JUDGMENT_DOCUMENT_TYPE:
        return SCENARIO_TO_JUDGMENT_DOCUMENT_TYPE[scenario_type]

    raise ValueError(f"Unsupported judgment document type: {document_type}")


def get_judgment_document_tool_name(document_type: str) -> str:
    normalized = normalize_judgment_document_type(document_type)
    return JUDGMENT_DOCUMENT_TYPE_TO_TOOL_NAME[normalized]


def get_judgment_document_type_for_scenario(scenario_type: str) -> str:
    return normalize_judgment_document_type(str(scenario_type or "").upper())


def normalize_judgment_document_payload(
    payload: Any,
    *,
    document_type: str,
) -> Dict[str, str]:
    normalized_document_type = normalize_judgment_document_type(document_type)
    source = payload if isinstance(payload, dict) else {}
    normalized_from_payload = normalize_judgment_document_type(
        source.get("document_type", normalized_document_type)
    )
    return {
        "document_type": normalized_from_payload,
        "pdf_path": str(source.get("pdf_path", "") or "").strip(),
    }


def extract_judgment_document_tool_payload(
    records: list[Any],
    *,
    document_type: str,
) -> Dict[str, str]:
    tool_name = get_judgment_document_tool_name(document_type)

    for record in reversed(list(records or [])):
        if isinstance(record, dict):
            record_tool_name = str(
                record.get("tool_name")
                or record.get("name")
                or record.get("tool")
                or ""
            ).strip()
            record_result = record.get("result")
        else:
            record_tool_name = str(getattr(record, "tool_name", "") or "").strip()
            record_result = getattr(record, "result", None)

        if record_tool_name != tool_name:
            continue
        if isinstance(record_result, str) and record_result.startswith(
            "Tool execution failed:"
        ):
            raise RuntimeError(record_result)

        payload = extract_json_payload(record_result)
        return normalize_judgment_document_payload(
            payload,
            document_type=document_type,
        )

    raise RuntimeError(f"Tool result not found for {tool_name}.")


def create_judgment_document_tool_for_scenario(agent: Any, scenario_type: str):
    document_type = get_judgment_document_type_for_scenario(scenario_type)
    return JUDGMENT_DOCUMENT_TYPE_TO_FACTORY[document_type](agent)


def render_judgment_document_payload(
    agent: Any,
    *,
    document_type: str,
    document_text: str,
) -> Dict[str, str]:
    normalized_document_type = normalize_judgment_document_type(document_type)
    raw_result = JUDGMENT_DOCUMENT_TYPE_TO_RENDERER[normalized_document_type](
        agent,
        str(document_text or ""),
    )
    payload = extract_json_payload(raw_result)
    return normalize_judgment_document_payload(
        payload,
        document_type=normalized_document_type,
    )


__all__ = [
    "JUDGMENT_DOCUMENT_TYPE_TO_TOOL_NAME",
    "SCENARIO_TO_JUDGMENT_DOCUMENT_TYPE",
    "create_judgment_document_tool_for_scenario",
    "extract_judgment_document_tool_payload",
    "get_judgment_document_tool_name",
    "get_judgment_document_type_for_scenario",
    "normalize_judgment_document_payload",
    "normalize_judgment_document_type",
    "render_judgment_document_payload",
]
