"""Public Prosecution Statement PDF tool — 《公诉词》.

仿 complaint_drafting_tool.py 结构，适配刑事庭审场景。
公诉词由检察官在法庭辩论阶段发表，用于阐述指控事实、
证据链条、法律适用和量刑建议。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from camel.toolkits import FunctionTool

try:
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    REPORTLAB_AVAILABLE = True
except ImportError:  # pragma: no cover
    REPORTLAB_AVAILABLE = False


logger = logging.getLogger(__name__)

PUBLIC_PROSECUTION_TOOL_NAME = "draft_public_prosecution_document"
PUBLIC_PROSECUTION_DOCUMENT_TYPE = "public_prosecution"
PUBLIC_PROSECUTION_RESULT_FIELD = "public_prosecution_text"
PUBLIC_PROSECUTION_PDF_FILENAME = "PP_document.pdf"


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _register_pdf_font() -> None:
    if "STSong-Light" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))


def _render_pdf(document_text: str, output_path: Path) -> None:
    if not REPORTLAB_AVAILABLE:
        raise RuntimeError("reportlab is not installed.")

    _register_pdf_font()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )
    title_style = ParagraphStyle(
        "PublicProsecutionTitle",
        fontName="STSong-Light",
        fontSize=16,
        leading=22,
        alignment=TA_CENTER,
    )
    body_style = ParagraphStyle(
        "PublicProsecutionBody",
        fontName="STSong-Light",
        fontSize=11,
        leading=18,
        alignment=TA_LEFT,
    )

    story = []
    for index, raw_line in enumerate(document_text.replace("\r\n", "\n").split("\n")):
        line = raw_line.strip()
        if not line:
            story.append(Spacer(1, 6))
            continue
        style = title_style if index == 0 else body_style
        story.append(Paragraph(escape(line), style))
        story.append(Spacer(1, 2 if index else 10))

    doc.build(story)


def _build_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": PUBLIC_PROSECUTION_TOOL_NAME,
            "description": (
                "接收检察官已经写好的《公诉词》全文，生成 PDF 文件。"
                "工具本身不负责起草正文，只返回 document_type 和 pdf_path。"
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "document_text": {
                        "type": "string",
                        "description": "检察官已经写好的完整《公诉词》正文。",
                    }
                },
                "required": ["document_text"],
                "additionalProperties": False,
            },
        },
    }


class PublicProsecutionTool:
    """Render one public prosecution statement PDF from prosecutor-authored text."""

    def __init__(self, agent: Any) -> None:
        self.agent = agent

    def resolve_case_output_dir(self) -> Path:
        scenario_data = getattr(self.agent, "scenario_data", {}) or {}
        explicit = str(scenario_data.get("case_output_dir", "") or "").strip()
        if explicit:
            path = Path(explicit).resolve()
            path.mkdir(parents=True, exist_ok=True)
            return path
        return Path.cwd().resolve()

    def draft_public_prosecution_document(self, document_text: str) -> str:
        normalized_text = _normalize_text(document_text)
        if not normalized_text:
            raise ValueError("document_text is required.")

        pdf_path = ""
        try:
            resolved_pdf_path = (
                self.resolve_case_output_dir() / PUBLIC_PROSECUTION_PDF_FILENAME
            )
            _render_pdf(normalized_text, resolved_pdf_path)
            pdf_path = str(resolved_pdf_path)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to render public prosecution PDF: %s", exc)

        payload = {
            "document_type": PUBLIC_PROSECUTION_DOCUMENT_TYPE,
            "document_text": normalized_text,
            "pdf_path": pdf_path,
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def create_public_prosecution_drafting_tool(agent: Any) -> FunctionTool:
    impl = PublicProsecutionTool(agent)
    return FunctionTool(
        impl.draft_public_prosecution_document,
        openai_tool_schema=_build_schema(),
    )


__all__ = [
    "PUBLIC_PROSECUTION_DOCUMENT_TYPE",
    "PUBLIC_PROSECUTION_PDF_FILENAME",
    "PUBLIC_PROSECUTION_RESULT_FIELD",
    "PUBLIC_PROSECUTION_TOOL_NAME",
    "PublicProsecutionTool",
    "REPORTLAB_AVAILABLE",
    "create_public_prosecution_drafting_tool",
]
