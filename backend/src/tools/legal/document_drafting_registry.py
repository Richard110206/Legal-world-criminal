"""Registry and payload helpers for legal document drafting tools — 纯刑事。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from camel.toolkits import FunctionTool

from .defense_opinion_drafting_tool import (
    DEFENSE_OPINION_DOCUMENT_TYPE,
    DEFENSE_OPINION_RESULT_FIELD,
    DEFENSE_OPINION_TOOL_NAME,
    DefenseOpinionDraftingTool,
    create_defense_opinion_drafting_tool,
)
from .document_drafting_support import extract_json_payload

SCENARIO_TO_DOCUMENT_TYPE = {
    "DS": DEFENSE_OPINION_DOCUMENT_TYPE,
}

DOCUMENT_TYPE_TO_TOOL_NAME = {
    DEFENSE_OPINION_DOCUMENT_TYPE: DEFENSE_OPINION_TOOL_NAME,
}

DOCUMENT_TYPE_TO_RESULT_FIELD = {
    DEFENSE_OPINION_DOCUMENT_TYPE: DEFENSE_OPINION_RESULT_FIELD,
}

DOCUMENT_TYPE_TO_FACTORY = {
    DEFENSE_OPINION_DOCUMENT_TYPE: create_defense_opinion_drafting_tool,
}

DOCUMENT_TYPE_TO_RENDERER = {
    DEFENSE_OPINION_DOCUMENT_TYPE: lambda agent, text: DefenseOpinionDraftingTool(agent).draft_defense_opinion_document(text),
}


def normalize_document_drafting_type(document_type: str) -> str:
    value = str(document_type or "").strip().lower()
    if value in DOCUMENT_TYPE_TO_TOOL_NAME:
        return value

    scenario_type = str(document_type or "").strip().upper()
    if scenario_type in SCENARIO_TO_DOCUMENT_TYPE:
        return SCENARIO_TO_DOCUMENT_TYPE[scenario_type]

    raise ValueError(f"Unsupported document drafting type: {document_type}")


def get_document_drafting_tool_name(document_type: str) -> str:
    normalized = normalize_document_drafting_type(document_type)
    return DOCUMENT_TYPE_TO_TOOL_NAME[normalized]


def get_document_drafting_result_field(document_type: str) -> str:
    normalized = normalize_document_drafting_type(document_type)
    return DOCUMENT_TYPE_TO_RESULT_FIELD[normalized]


def get_document_type_for_scenario(scenario_type: str) -> str:
    return normalize_document_drafting_type(str(scenario_type or "").upper())


def normalize_document_drafting_payload(
    payload: Any,
    *,
    document_type: str,
) -> dict[str, str]:
    normalized_document_type = normalize_document_drafting_type(document_type)
    source = payload if isinstance(payload, dict) else {}
    normalized_from_payload = normalize_document_drafting_type(
        source.get("document_type", normalized_document_type)
    )
    return {
        "document_type": normalized_from_payload,
        "document_text": str(source.get("document_text", "") or "").strip(),
        "pdf_path": str(source.get("pdf_path", "") or "").strip(),
    }


def extract_document_drafting_tool_payload(
    records: list[Any],
    *,
    document_type: str,
) -> dict[str, str]:
    tool_name = get_document_drafting_tool_name(document_type)

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
        return normalize_document_drafting_payload(
            payload,
            document_type=document_type,
        )

    raise RuntimeError(f"Tool result not found for {tool_name}.")


def create_document_drafting_tool_for_scenario(
    agent: Any,
    scenario_type: str,
) -> FunctionTool:
    document_type = get_document_type_for_scenario(scenario_type)
    return DOCUMENT_TYPE_TO_FACTORY[document_type](agent)


def render_document_drafting_payload(
    agent: Any,
    *,
    document_type: str,
    document_text: str,
) -> dict[str, str]:
    normalized_document_type = normalize_document_drafting_type(document_type)
    raw_result = DOCUMENT_TYPE_TO_RENDERER[normalized_document_type](agent, str(document_text or ""))
    payload = extract_json_payload(raw_result)
    return normalize_document_drafting_payload(
        payload,
        document_type=normalized_document_type,
    )


def render_document_drafting_payload_for_output_dir(
    *,
    document_type: str,
    document_text: str,
    case_output_dir: str | Path,
) -> dict[str, str]:
    """Render a drafting payload without requiring a live AI lawyer agent."""

    class _RenderAgent:
        def __init__(self, output_dir: str | Path) -> None:
            self.scenario_data = {"case_output_dir": str(Path(output_dir).resolve())}

    return render_document_drafting_payload(
        _RenderAgent(case_output_dir),
        document_type=document_type,
        document_text=document_text,
    )


__all__ = [
    "DOCUMENT_TYPE_TO_RESULT_FIELD",
    "DOCUMENT_TYPE_TO_TOOL_NAME",
    "SCENARIO_TO_DOCUMENT_TYPE",
    "create_document_drafting_tool_for_scenario",
    "extract_document_drafting_tool_payload",
    "get_document_drafting_result_field",
    "get_document_drafting_tool_name",
    "get_document_type_for_scenario",
    "normalize_document_drafting_payload",
    "normalize_document_drafting_type",
    "render_document_drafting_payload",
    "render_document_drafting_payload_for_output_dir",
]
