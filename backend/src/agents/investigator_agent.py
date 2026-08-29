"""Investigator agent — 公安侦查员（可选，轻量实现）。

仿 lawyer_agent.py 结构。
侦查阶段的互动较为有限（主要是律师会见和程序性告知），
因此此 Agent 可从简实现——大部分场景数据从数据集读取，
必要时用 AI 模拟侦查员的基本回复。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_POLICE_ORG = "XX公安局"


class InvestigatorAgent:
    """公安侦查员 Agent —— 轻量实现。

    职责：
    1. 告知律师涉嫌罪名和案件基本情况
    2. 安排会见（模拟）
    3. 处理取保候审申请（模拟）
    """

    agent_type = "client"  # 复用 client type，或设为 "investigator"

    def __init__(
        self,
        agent_id: str,
        name: str,
        police_org: str = DEFAULT_POLICE_ORG,
        scenario_type: str | None = None,
        scenario_data: dict[str, Any] | None = None,
        system_prompt: str = "",
        tools: list[Any] | None = None,
        model_type: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.agent_id = agent_id
        self.name = name
        self.police_org = police_org
        self.scenario_type = scenario_type
        self.scenario_data = scenario_data or {}
        self.tools = list(tools or [])
        self.model_type = model_type

        self.config_path: str | None = kwargs.get("config_path")
        self.storage: Any = kwargs.get("storage")
        self.chat_agent = None
        self._last_tool_call_records: list[Any] = []
        self._is_active = False

        if scenario_type and not system_prompt:
            system_prompt = self._build_pipeline_prompt()

        self.system_prompt = system_prompt

    # ── 协议表面 ──────────────────────────────────────────────
    @property
    def is_active(self) -> bool:
        return self._is_active

    def activate(self, **kwargs: Any) -> None:
        self._is_active = True
        logger.info("[InvestigatorAgent %s] Activated", self.name)

    def deactivate(self) -> None:
        self._is_active = False
        logger.info("[InvestigatorAgent %s] Deactivated", self.name)

    def step(self, instruction: str, **kwargs: Any) -> str:
        chat = getattr(self, "chat_agent", None)
        if chat is None:
            return ""
        response = chat.step(instruction)
        self._last_tool_call_records = list(
            getattr(chat, "_last_tool_call_records", []) or []
        )
        return response

    def get_prompt_info(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_name": self.name,
            "agent_class": "InvestigatorAgent",
            "system_prompt": self.system_prompt,
        }

    def _build_pipeline_prompt(self) -> str:
        return (
            f"你是{self.police_org}的侦查员 {self.name}。\n\n"
            "[核心职责]\n"
            "你负责刑事案件的侦查工作。对待律师的合法询问，"
            "应依法告知涉嫌罪名和案件基本情况，但不得泄露侦查秘密。\n\n"
            "[互动准则]\n"
            "1. 回复应简明扼要，不透露案件详细证据。\n"
            "2. 涉及程序性问题（如取保候审）应依法回复。\n"
        )

    def add_runtime_tools(self, tools: list[Any]) -> None:
        existing_names = {
            t.get_function_name()
            for t in self.tools
            if t is not None and hasattr(t, "get_function_name")
        }
        for tool in tools:
            if tool is None or not hasattr(tool, "get_function_name"):
                continue
            if tool.get_function_name() not in existing_names:
                self.tools.append(tool)

    @property
    def current_handling_case(self) -> str | None:
        return self.scenario_data.get("case_id") if self.scenario_data else None

    @property
    def case_queue(self) -> list[str]:
        return []


__all__ = [
    "DEFAULT_POLICE_ORG",
    "InvestigatorAgent",
]
