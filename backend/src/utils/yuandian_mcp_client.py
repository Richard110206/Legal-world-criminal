"""Yuandian (元典) MCP client — 法条/案例/公司/证券检索客户端封装。

基于标准 MCP (Model Context Protocol) JSON-RPC over HTTP，适配
元典开放平台 (open.chineselaw.com) 的 stream 端点。

配置项（.env）:
    YUANDIAN_API_KEY          API Key（必填）
    YUANDIAN_BASE_URL         默认 https://open.chineselaw.com
    YUANDIAN_TIMEOUT_SECONDS  请求超时（默认 40）

用法:
    client = YuandianMCPClient()
    result = client.call_tool("yuandian_law_vector_search", {
        "query": "盗窃罪 数额较大", "return_num": 5,
    })
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
import urllib.error
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://open.chineselaw.com"
LAW_ENDPOINT_PATH = "/mcp/law/stream"
CASE_ENDPOINT_PATH = "/mcp/case/stream"
COMPANY_ENDPOINT_PATH = "/mcp/company/stream"
SECURITIES_ENDPOINT_PATH = "/mcp/securities/stream"

DEFAULT_TIMEOUT_SECONDS = 40
DEFAULT_MAX_ATTEMPTS = 3
_REQUEST_ID_LOCK = threading.Lock()
_REQUEST_ID_COUNTER = 0


class YuandianMCPError(RuntimeError):
    """元典 MCP 调用异常。"""


def _next_request_id() -> int:
    global _REQUEST_ID_COUNTER
    with _REQUEST_ID_LOCK:
        _REQUEST_ID_COUNTER += 1
        return _REQUEST_ID_COUNTER


def _resolve_api_key() -> str:
    key = (
        str(os.environ.get("YUANDIAN_API_KEY", "") or "").strip()
        or str(os.environ.get("YUANDIAN_APP_KEY", "") or "").strip()
    )
    if not key:
        raise YuandianMCPError("YUANDIAN_API_KEY 未配置，请检查 .env")
    return key


def _resolve_timeout() -> int:
    try:
        return max(5, int(os.environ.get("YUANDIAN_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS


class YuandianMCPClient:
    """元典 MCP 客户端：初始化 + 工具调用 + 业务检索方法。

    设计为可复用、无状态（每次调用独立连接），关键词全部由调用方传入，不硬编码。
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: Optional[int] = None,
    ) -> None:
        self.api_key = str(api_key or "").strip() or _resolve_api_key()
        self.base_url = str(base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout or _resolve_timeout()

    # ── 底层 JSON-RPC ──────────────────────────────────────────────
    def _request(self, endpoint_path: str, method: str, params: dict[str, Any]) -> Any:
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": _next_request_id(),
                "method": method,
                "params": params,
            },
            ensure_ascii=False,
        ).encode("utf-8")

        url = f"{self.base_url}{endpoint_path}"
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Accept", "application/json, text/event-stream")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", "application/json")

        last_error: Optional[Exception] = None
        for attempt in range(DEFAULT_MAX_ATTEMPTS):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8")
                return self._parse_response(body)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
                last_error = YuandianMCPError(f"元典 HTTP {exc.code}: {detail}")
                logger.warning(
                    "[YuandianMCP] HTTP %s on %s (attempt %d)",
                    exc.code,
                    endpoint_path,
                    attempt + 1,
                )
                time.sleep(0.5 * (attempt + 1))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = YuandianMCPError(f"元典网络错误: {exc}")
                logger.warning("[YuandianMCP] network error on %s: %s", endpoint_path, exc)
                time.sleep(0.5 * (attempt + 1))
            except json.JSONDecodeError as exc:
                raise YuandianMCPError(f"元典响应解析失败: {exc}") from exc

        raise YuandianMCPError(f"元典请求失败: {last_error}")

    @staticmethod
    def _parse_response(body: str) -> Any:
        if not body.strip():
            return {}
        return json.loads(body)

    def _call_tool(
        self,
        endpoint_path: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        response = self._request(
            endpoint_path,
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )
        if "error" in response and response.get("error"):
            error = response["error"]
            raise YuandianMCPError(
                f"元典工具 {tool_name} 调用错误: {error.get('message', error)}"
            )
        result = response.get("result") or {}
        texts = [
            item.get("text", "")
            for item in (result.get("content") or [])
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        raw = texts[0] if texts else json.dumps(result, ensure_ascii=False)
        try:
            return json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return {"raw": raw}

    @staticmethod
    def _extract_list(payload: Any) -> list[dict[str, Any]]:
        """从响应中尽量提取条目列表（兼容 data / data.lst / extra.fatiao / extra.wenshu 等结构）。"""
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []

        # data 可能是列表或 {lst: [...]}
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("lst", "list", "items", "cases", "wenshu", "fatiao", "anli"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]

        extra = payload.get("extra")
        if isinstance(extra, dict):
            for key in ("fatiao", "anli", "guizhang", "wenshu", "cases", "items", "list"):
                value = extra.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    # ── 业务方法：法条 ─────────────────────────────────────────────
    def search_law_semantic(
        self,
        query: str,
        return_num: int = 5,
        fatiao_filter: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """法条语义检索（自然语言 query）。"""
        arguments: dict[str, Any] = {"query": str(query or ""), "return_num": int(return_num or 5)}
        if fatiao_filter:
            arguments["fatiao_filter"] = fatiao_filter
        payload = self._call_tool(LAW_ENDPOINT_PATH, "yuandian_law_vector_search", arguments)
        return self._extract_list(payload)

    def search_law_article(
        self,
        keyword: str,
        law_name: str = "",
        top_k: int = 5,
        search_mode: str = "AND",
    ) -> list[dict[str, Any]]:
        """法条关键词检索（支持按法规名称过滤，关键词由调用方传入）。"""
        arguments: dict[str, Any] = {
            "keyword": str(keyword or ""),
            "top_k": int(top_k or 5),
            "search_mode": str(search_mode or "AND"),
        }
        if law_name:
            arguments["fgmc"] = str(law_name)
        payload = self._call_tool(LAW_ENDPOINT_PATH, "yuandian_rh_ft_search", arguments)
        return self._extract_list(payload)

    def search_law_fg(
        self,
        keyword: str = "",
        law_name: str = "",
        top_k: int = 5,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """法规列表检索（按名称/关键词/效力层级/时效等过滤）。"""
        arguments: dict[str, Any] = {"top_k": int(top_k or 5)}
        if keyword:
            arguments["keyword"] = str(keyword)
        if law_name:
            arguments["fgmc"] = str(law_name)
        arguments.update({k: v for k, v in filters.items() if v not in (None, "")})
        payload = self._call_tool(LAW_ENDPOINT_PATH, "yuandian_rh_fg_search", arguments)
        return self._extract_list(payload)

    # ── 业务方法：案例 ─────────────────────────────────────────────
    def search_case_semantic(
        self,
        query: str,
        return_num: int = 5,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """案例语义检索。"""
        arguments: dict[str, Any] = {"query": str(query or ""), "return_num": int(return_num or 5)}
        arguments.update({k: v for k, v in filters.items() if v not in (None, "")})
        payload = self._call_tool(CASE_ENDPOINT_PATH, "yuandian_case_vector_search", arguments)
        return self._extract_list(payload)

    def search_case(
        self,
        keyword: str = "",
        top_k: int = 5,
        authority: bool = False,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """案例关键词检索（关键词由调用方传入）。

        authority=False → 普通裁判案例 (yuandian_rh_ptal_search)
        authority=True  → 权威/典型/参考案例 (yuandian_rh_qwal_search)
        """
        tool_name = "yuandian_rh_qwal_search" if authority else "yuandian_rh_ptal_search"
        arguments: dict[str, Any] = {"top_k": int(top_k or 5)}
        if keyword:
            arguments["keyword"] = str(keyword)
        arguments.update({k: v for k, v in filters.items() if v not in (None, "")})
        payload = self._call_tool(CASE_ENDPOINT_PATH, tool_name, arguments)
        return self._extract_list(payload)

    # ── 其它 ───────────────────────────────────────────────────────
    def list_apis(self, endpoint_path: str = LAW_ENDPOINT_PATH, **filters: Any) -> list[dict[str, Any]]:
        payload = self._call_tool(endpoint_path, "yuandian_list_apis", dict(filters))
        return self._extract_list(payload)

    def get_balance(self) -> dict[str, Any]:
        payload = self._call_tool(LAW_ENDPOINT_PATH, "yuandian_get_user_balance", {})
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            return payload["data"]
        return payload


_shared_client: Optional[YuandianMCPClient] = None
_shared_client_lock = threading.Lock()


def get_yuandian_client() -> YuandianMCPClient:
    """返回全局共享的元典客户端（懒加载，读 .env 配置）。"""
    global _shared_client
    with _shared_client_lock:
        if _shared_client is None:
            _shared_client = YuandianMCPClient()
        return _shared_client


def reset_yuandian_client() -> None:
    """重置共享客户端（配置变更后调用）。"""
    global _shared_client
    with _shared_client_lock:
        _shared_client = None


__all__ = [
    "YuandianMCPClient",
    "YuandianMCPError",
    "get_yuandian_client",
    "reset_yuandian_client",
]
