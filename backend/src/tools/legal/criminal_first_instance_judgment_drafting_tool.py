"""First-Instance Criminal Judgment PDF rendering tool — 《刑事判决书》(一审).

仿 first_instance_judgment_drafting_tool.py 结构，适配刑事一审判决。
与民事判决书的核心差异：
- 包含「公诉机关」字段而非「原告」
- 判决主文包含刑种、刑期、罚金等
- 引用《刑法》条文而非《民法典》
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict
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

CRIMINAL_FIRST_INSTANCE_JUDGMENT_TOOL_NAME = "draft_first_instance_criminal_judgment_document"
CRIMINAL_FIRST_INSTANCE_JUDGMENT_DOCUMENT_TYPE = "first_instance_criminal_judgment"
CRIMINAL_FIRST_INSTANCE_JUDGMENT_RESULT_FIELD = "criminal_final_judgment"
CRIMINAL_FIRST_INSTANCE_JUDGMENT_PDF_FILENAME = "CI_criminal_document.pdf"


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
        "CriminalFirstInstanceJudgmentTitle",
        fontName="STSong-Light",
        fontSize=16,
        leading=22,
        alignment=TA_CENTER,
    )
    body_style = ParagraphStyle(
        "CriminalFirstInstanceJudgmentBody",
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


def _build_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": CRIMINAL_FIRST_INSTANCE_JUDGMENT_TOOL_NAME,
            "description": (
                "接收审判长已经写好的《刑事判决书》(一审)全文，生成 PDF 文件。"
                "与民事判决书的区别：包含公诉机关、指控罪名、刑种/刑期/罚金等刑事特有字段。"
                "工具本身不负责起草正文，只返回 document_type 和 pdf_path。"
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "document_text": {
                        "type": "string",
                        "description": "审判长已经写好的完整《刑事判决书》(一审)正文。",
                    }
                },
                "required": ["document_text"],
                "additionalProperties": False,
            },
        },
    }


class CriminalFirstInstanceJudgmentDraftingTool:
    """Render one criminal first-instance judgment PDF from judge-authored text."""

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

    def draft_first_instance_criminal_judgment_document(self, document_text: str) -> str:
        normalized_text = _normalize_text(document_text)
        if not normalized_text:
            raise ValueError("document_text is required.")

        pdf_path = ""
        try:
            resolved_pdf_path = (
                self.resolve_case_output_dir()
                / CRIMINAL_FIRST_INSTANCE_JUDGMENT_PDF_FILENAME
            )
            _render_pdf(normalized_text, resolved_pdf_path)
            pdf_path = str(resolved_pdf_path)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to render criminal first-instance judgment PDF: %s", exc)

        payload = {
            "document_type": CRIMINAL_FIRST_INSTANCE_JUDGMENT_DOCUMENT_TYPE,
            "pdf_path": pdf_path,
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def create_first_instance_criminal_judgment_drafting_tool(agent: Any) -> FunctionTool:
    impl = CriminalFirstInstanceJudgmentDraftingTool(agent)
    return FunctionTool(
        impl.draft_first_instance_criminal_judgment_document,
        openai_tool_schema=_build_schema(),
    )


__all__ = [
    "CRIMINAL_FIRST_INSTANCE_JUDGMENT_DOCUMENT_TYPE",
    "CRIMINAL_FIRST_INSTANCE_JUDGMENT_PDF_FILENAME",
    "CRIMINAL_FIRST_INSTANCE_JUDGMENT_RESULT_FIELD",
    "CRIMINAL_FIRST_INSTANCE_JUDGMENT_TOOL_NAME",
    "CriminalFirstInstanceJudgmentDraftingTool",
    "REPORTLAB_AVAILABLE",
    "create_first_instance_criminal_judgment_drafting_tool",
]
