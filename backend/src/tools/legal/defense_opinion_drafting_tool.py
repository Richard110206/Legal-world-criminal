"""Defense Opinion PDF rendering tool — 《辩护词》.

仿 defense_drafting_tool.py 结构，适配刑事辩护场景。
工具本身只负责 PDF 渲染，辩护词正文由 LLM（律师 Agent）根据 SKILL.md 指令起草。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict
from xml.sax.saxutils import escape

from camel.toolkits import FunctionTool

# 复用原始项目的 PDF 输出辅助函数
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

# ── 工具元数据 ────────────────────────────────────────────────
DEFENSE_OPINION_TOOL_NAME = "draft_defense_opinion_document"
DEFENSE_OPINION_DOCUMENT_TYPE = "defense_opinion"
DEFENSE_OPINION_RESULT_FIELD = "defense_opinion_text"
DEFENSE_OPINION_PDF_FILENAME = "DO_document.pdf"


# ── 辅助函数（与原始项目一致）─────────────────────────────────
def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _register_pdf_font() -> None:
    if "STSong-Light" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))


def _render_pdf(document_text: str, output_path: Path) -> None:
    """使用 ReportLab 渲染《辩护词》PDF。"""
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
        "DefenseOpinionTitle",
        fontName="STSong-Light",
        fontSize=16,
        leading=22,
        alignment=TA_CENTER,
    )
    body_style = ParagraphStyle(
        "DefenseOpinionBody",
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


# ── OpenAI Function Schema ─────────────────────────────────────
def _build_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": DEFENSE_OPINION_TOOL_NAME,
            "description": (
                "接收辩护律师已经写好的《辩护词》全文，生成 PDF 文件。"
                "工具本身不负责起草正文，只返回 document_type 和 pdf_path。"
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "document_text": {
                        "type": "string",
                        "description": "辩护律师已经写好的完整《辩护词》正文。",
                    }
                },
                "required": ["document_text"],
                "additionalProperties": False,
            },
        },
    }


# ── 工具实现类 ─────────────────────────────────────────────────
class DefenseOpinionDraftingTool:
    """Render one defense opinion PDF from lawyer-authored text."""

    def __init__(self, agent: Any) -> None:
        self.agent = agent

    def resolve_case_output_dir(self) -> Path:
        """解析案件输出目录，优先从 agent.scenario_data 中获取。"""
        scenario_data = getattr(self.agent, "scenario_data", {}) or {}
        explicit = str(scenario_data.get("case_output_dir", "") or "").strip()
        if explicit:
            path = Path(explicit).resolve()
            path.mkdir(parents=True, exist_ok=True)
            return path
        return Path.cwd().resolve()

    def draft_defense_opinion_document(self, document_text: str) -> str:
        """接收辩护词正文，渲染 PDF 并返回 JSON payload。"""
        normalized_text = _normalize_text(document_text)
        if not normalized_text:
            raise ValueError("document_text is required.")

        pdf_path = ""
        try:
            resolved_pdf_path = (
                self.resolve_case_output_dir() / DEFENSE_OPINION_PDF_FILENAME
            )
            _render_pdf(normalized_text, resolved_pdf_path)
            pdf_path = str(resolved_pdf_path)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to render defense opinion PDF: %s", exc)

        payload = {
            "document_type": DEFENSE_OPINION_DOCUMENT_TYPE,
            "document_text": normalized_text,
            "pdf_path": pdf_path,
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


# ── CAMEL FunctionTool 工厂 ────────────────────────────────────
def create_defense_opinion_drafting_tool(agent: Any) -> FunctionTool:
    impl = DefenseOpinionDraftingTool(agent)
    return FunctionTool(
        impl.draft_defense_opinion_document,
        openai_tool_schema=_build_schema(),
    )


__all__ = [
    "DEFENSE_OPINION_DOCUMENT_TYPE",
    "DEFENSE_OPINION_PDF_FILENAME",
    "DEFENSE_OPINION_RESULT_FIELD",
    "DEFENSE_OPINION_TOOL_NAME",
    "DefenseOpinionDraftingTool",
    "REPORTLAB_AVAILABLE",
    "create_defense_opinion_drafting_tool",
]
