"""元典法律检索工具 — 法条溯源 + 案例检索。

通过元典 MCP 接口提供权威法条/案例检索能力，服务两类角色：
- 学生(辩护律师)：对话中可调用「search_yuandian_law」查法条，「search_yuandian_case」查类案
- 教学裁判：核验学生发言引用的法条是否准确、时效是否有效

关键词全部由调用方（LLM）传入，不硬编码。API Key 从 .env 的 YUANDIAN_API_KEY 读取。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from camel.toolkits import FunctionTool

from ...utils.yuandian_mcp_client import (
    YuandianMCPClient,
    YuandianMCPError,
    get_yuandian_client,
)

logger = logging.getLogger(__name__)

YUANDIAN_LAW_TOOL_NAME = "search_yuandian_law"
YUANDIAN_CASE_TOOL_NAME = "search_yuandian_case"


def _get_client() -> YuandianMCPClient:
    return get_yuandian_client()


def _summarize_law_items(items: list[dict[str, Any]], limit: int = 5) -> str:
    if not items:
        return "未检索到相关法条。"
    lines: list[str] = []
    for idx, item in enumerate(items[:limit], start=1):
        title = str(item.get("fgtitle") or item.get("title") or item.get("ftmc") or "").strip()
        num = str(item.get("num") or item.get("ft_num") or "").strip()
        content = str(item.get("content") or "").strip()
        status = str(item.get("sxx") or item.get("status") or "").strip()
        effect = str(item.get("effect1") or item.get("xljb_1") or "").strip()
        parts = []
        if title:
            parts.append(f"法规：{title}")
        if num:
            parts.append(f"条：{num}")
        if effect:
            parts.append(f"效力：{effect}")
        if status:
            parts.append(f"状态：{status}")
        lines.append(f"{idx}. " + "；".join(parts))
        if content:
            lines.append(f"   内容：{content[:300]}")
    return "\n".join(lines)


def _summarize_case_items(items: list[dict[str, Any]], limit: int = 5) -> str:
    if not items:
        return "未检索到相关案例。"
    lines: list[str] = []
    for idx, item in enumerate(items[:limit], start=1):
        title = str(item.get("title") or item.get("ftmc") or "").strip()
        case_no = str(item.get("ah") or item.get("case_no") or "").strip()
        court = str(item.get("jbdw") or item.get("court") or "").strip()
        content = str(item.get("content") or item.get("llm_content") or "").strip()
        parts = []
        if title:
            parts.append(title)
        if case_no:
            parts.append(f"案号：{case_no}")
        if court:
            parts.append(f"法院：{court}")
        lines.append(f"{idx}. " + "；".join(parts))
        if content:
            lines.append(f"   摘要：{content[:280]}")
    return "\n".join(lines)


def search_yuandian_law(
    query: str = "",
    keyword: str = "",
    law_name: str = "",
    top_k: int = 5,
    semantic: bool = True,
) -> str:
    """检索权威法条原文。

    Args:
        query: 自然语言检索短语（语义检索用），如"盗窃罪数额较大的构成要件"。
        keyword: 精确关键词（按条号/罪名/术语匹配），如"第二百六十四条"或"盗窃罪"。
        law_name: 法规名称过滤（可选），如"中华人民共和国刑法"。
        top_k: 返回条数，默认 5。
        semantic: True 用语义向量检索；False 用关键词精确检索。

    Returns:
        结构化法条文本（含法规名、条号、内容、效力、时效）。
    """
    try:
        client = _get_client()
        if semantic and query:
            items = client.search_law_semantic(query, return_num=top_k)
            if items:
                return _summarize_law_items(items, top_k)
        if keyword:
            items = client.search_law_article(keyword, law_name=law_name, top_k=top_k)
            if items:
                return _summarize_law_items(items, top_k)
        if query:
            items = client.search_law_semantic(query, return_num=top_k)
            return _summarize_law_items(items, top_k)
        return "请提供 query 或 keyword 至少一项。"
    except YuandianMCPError as exc:
        logger.warning("[YuandianTool] law search failed: %s", exc)
        return f"法条检索失败：{exc}"
    except Exception as exc:
        logger.exception("[YuandianTool] law search unexpected error")
        return f"法条检索异常：{exc}"


def search_yuandian_case(
    keyword: str = "",
    query: str = "",
    top_k: int = 5,
    semantic: bool = True,
) -> str:
    """检索类似案例（权威/精选案例库）。

    Args:
        keyword: 关键词（罪名/案由等），如"盗窃罪"。
        query: 语义检索短语（可选），如"以危险方法危害公共安全罪 量刑"。
        top_k: 返回条数，默认 5。
        semantic: True 优先用精选案例库语义检索；False 用普通案例关键词检索。

    Returns:
        结构化案例文本（含标题、案号、法院、摘要）。
    """
    try:
        client = _get_client()
        if semantic and query:
            items = client.search_case_semantic(query, return_num=top_k)
            if items:
                return _summarize_case_items(items, top_k)
        if keyword:
            items = client.search_case(keyword, top_k=top_k, ajlb="刑事案件")
            if items:
                return _summarize_case_items(items, top_k)
        if query:
            items = client.search_case_semantic(query, return_num=top_k)
            return _summarize_case_items(items, top_k)
        return "请提供 keyword 或 query 至少一项。"
    except YuandianMCPError as exc:
        logger.warning("[YuandianTool] case search failed: %s", exc)
        return f"案例检索失败：{exc}"
    except Exception as exc:
        logger.exception("[YuandianTool] case search unexpected error")
        return f"案例检索异常：{exc}"


def _build_law_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": YUANDIAN_LAW_TOOL_NAME,
            "description": (
                "检索权威法条原文（元典法律库）。用于查证罪名的构成要件、法定刑、"
                "以及学生发言中引用的法条是否准确、是否现行有效。"
                "优先用 keyword 精确检索（如'第二百六十四条'或'盗窃罪'），"
                "也可用 query 做语义检索（如'正当防卫的时间条件'）。"
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "精确关键词：条号（第二百六十四条）或罪名/术语（盗窃罪）。",
                    },
                    "query": {
                        "type": "string",
                        "description": "自然语言语义检索短语。",
                    },
                    "law_name": {
                        "type": "string",
                        "description": "法规名称过滤，如'中华人民共和国刑法'（可选）。",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回条数，默认 5。",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    }


def _build_case_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": YUANDIAN_CASE_TOOL_NAME,
            "description": (
                "检索类似刑事案例（元典精选/权威案例库）。用于查找同类案件的裁判规则、"
                "量刑参考，帮助学生建立'事实-证据-规范-结论'的论证参考。"
                "keyword 用罪名/案由（如'盗窃罪'），也可用 query 做语义检索。"
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "关键词：罪名/案由（如'盗窃罪'、'故意伤害'）。",
                    },
                    "query": {
                        "type": "string",
                        "description": "自然语言语义检索短语（可选）。",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回条数，默认 5。",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    }


def create_yuandian_law_tool(agent: Any = None) -> FunctionTool:
    """Create FunctionTool for authoritative law retrieval."""
    del agent
    return FunctionTool(search_yuandian_law, openai_tool_schema=_build_law_schema())


def create_yuandian_case_tool(agent: Any = None) -> FunctionTool:
    """Create FunctionTool for similar-case retrieval."""
    del agent
    return FunctionTool(search_yuandian_case, openai_tool_schema=_build_case_schema())


__all__ = [
    "YUANDIAN_CASE_TOOL_NAME",
    "YUANDIAN_LAW_TOOL_NAME",
    "create_yuandian_case_tool",
    "create_yuandian_law_tool",
    "search_yuandian_case",
    "search_yuandian_law",
]
