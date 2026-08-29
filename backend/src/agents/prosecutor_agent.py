"""Prosecutor agent — 检察官 / 公诉人。

仿 lawyer_agent.py 结构，适配刑事公诉场景。
检察官代表国家出庭支持公诉，核心职责：
1. 审查案件事实和证据
2. 提起公诉（出具起诉书）
3. 出庭支持公诉（宣读起诉书、发表公诉词、举证质证）
4. 对判决提出抗诉（如适用）
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PROCURATORATE = "XX人民检察院"


class ProsecutorAgent:
    """检察官 Agent —— 代表国家出庭支持公诉。

    与 LawyerAgent 的关键区别：
    - agent_type = "prosecutor"（新类型）
    - 没有 long-term memory（检察官不维护跨案件连续性）
    - system_prompt 围绕公诉立场构建
    - 工具权限：search_laws + draft_indictment + draft_public_prosecution
    """

    # ── 必须暴露的协议字段 ──────────────────────────────────────
    agent_type = "prosecutor"

    def __init__(
        self,
        agent_id: str,
        name: str,
        procuratorate: str = DEFAULT_PROCURATORATE,
        scenario_type: str | None = None,
        scenario_data: dict[str, Any] | None = None,
        system_prompt: str = "",
        tools: list[Any] | None = None,
        model_type: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.agent_id = agent_id
        self.name = name
        self.procuratorate = procuratorate
        self.scenario_type = scenario_type
        self.scenario_data = scenario_data or {}
        self.tools = list(tools or [])
        self.model_type = model_type

        # 协议表面字段（兼容 BaseAgent 访问模式）
        self.config_path: str | None = kwargs.get("config_path")
        self.storage: Any = kwargs.get("storage")
        self.chat_agent = None
        self._last_tool_call_records: list[Any] = []
        self._is_active = False

        if scenario_type and not system_prompt:
            system_prompt = self._build_pipeline_prompt()

        self.system_prompt = system_prompt

    # ── 协议表面：step / activate / deactivate ──────────────────
    @property
    def is_active(self) -> bool:
        return self._is_active

    def activate(
        self,
        system_prompt: str | None = None,
        model_platform: Any = None,
        model_type: str | None = None,
        tools: list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """激活检察官 agent：创建真实 CAMEL ChatAgent。

        与 BaseAgent.activate 对齐，供 orchestrator 在刑事场景中调用。
        """
        if system_prompt:
            self.system_prompt = system_prompt
        if model_type:
            self.model_type = model_type

        from ..agents.base_agent import (
            _resolve_agent_api_base_url,
            _resolve_agent_api_key,
            _resolve_agent_model_type,
        )
        from ..utils.model_config import (
            build_runtime_openai_chat_config,
        )

        platform = model_platform
        if platform is None:
            from camel.types import ModelPlatformType
            platform = ModelPlatformType.OPENAI

        resolved_model = _resolve_agent_model_type(self, self.model_type)
        api_base_url = _resolve_agent_api_base_url(self)
        api_key = _resolve_agent_api_key(self)
        model_factory_kwargs: dict[str, Any] = {}
        if api_key:
            model_factory_kwargs["api_key"] = api_key
        if api_base_url:
            model_factory_kwargs["url"] = api_base_url

        from camel.agents import ChatAgent
        from camel.models import ModelFactory

        model_config = ModelFactory.create(
            model_platform=platform,
            model_type=resolved_model,
            model_config_dict=build_runtime_openai_chat_config(
                model_name=resolved_model,
                temperature=0.5,
            ),
        )
        self.chat_agent = ChatAgent(
            system_message=self.system_prompt,
            model=model_config,
            tools=list(tools or []),
        )
        if tools is not None:
            self.tools = list(tools)
        self._is_active = True
        logger.info("[ProsecutorAgent %s] Activated with LLM", self.name)

    def deactivate(self) -> None:
        self.chat_agent = None
        self._is_active = False
        logger.info("[ProsecutorAgent %s] Deactivated", self.name)

    def recover_from_error(self) -> None:
        if self.chat_agent:
            try:
                self.chat_agent.reset()
                logger.info("[ProsecutorAgent %s] Recovered from error", self.name)
            except Exception as exc:
                logger.error("[ProsecutorAgent %s] reset failed: %s", self.name, exc)
                self.deactivate()
        else:
            self.deactivate()

    def reset_memory(self) -> None:
        if self.chat_agent:
            try:
                self.chat_agent.reset()
            except Exception as exc:
                logger.warning("[ProsecutorAgent %s] reset_memory failed: %s", self.name, exc)

    def step(self, instruction: str, **kwargs: Any) -> str:
        """执行一步推理 / 发言。实际调用由 CAMEL chat_agent 完成。"""
        chat = getattr(self, "chat_agent", None)
        if chat is None:
            logger.warning(
                "[ProsecutorAgent %s] step() called without chat_agent — returning empty",
                self.name,
            )
            return ""
        import time as _time
        started = _time.perf_counter()
        response = chat.step(instruction)
        content = response.msgs[0].content
        usage = dict(getattr(response, "info", {}) or {}).get("usage") or {}
        self._simlaw_last_step_response_text = content
        self._simlaw_last_step_duration_seconds = _time.perf_counter() - started
        self._simlaw_last_step_total_tokens = int(usage.get("total_tokens") or 0)
        self._last_tool_call_records = list(
            (getattr(response, "info", {}) or {}).get("tool_calls") or []
        )
        return content

    def get_prompt_info(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_name": self.name,
            "agent_class": "ProsecutorAgent",
            "system_prompt": self.system_prompt,
        }

    # ── system_prompt 构建 ──────────────────────────────────────
    def _build_pipeline_prompt(self) -> str:
        """构建检察官系统提示词。"""
        return (
            f"你是{self.procuratorate}的检察官 {self.name}。\n\n"
            "[核心职责]\n"
            "你代表国家出庭支持公诉。你的职责是忠实地履行职责，"
            "准确查明案件事实，确保法律正确实施。\n\n"
            "[工作原则]\n"
            "1. 以事实为根据，以法律为准绳。\n"
            "2. 既要注意对被告人不利的事实和证据，也要注意对被告人有利的事实和证据。\n"
            "3. 客观公正，不偏不倚，严格依法办事。\n"
            "4. 在庭审中，先宣读起诉书，再举证质证，最后发表公诉词。\n\n"
            "[互动准则]\n"
            "1. 发言应围绕指控罪名、犯罪事实、证据链条和法律适用展开。\n"
            "2. 不要输出 Markdown 标题、表格或代码块。\n"
            "3. 仅输出发言文本，不要写括号动作或语气旁白。\n"
        )

    def add_runtime_tools(self, tools: list[Any]) -> None:
        """动态注入运行时工具（由 stage_tool_resolver 调用）。"""
        existing_names = {
            t.get_function_name()
            for t in self.tools
            if t is not None and hasattr(t, "get_function_name")
        }
        additions = [
            tool
            for tool in list(tools or [])
            if tool is not None
            and hasattr(tool, "get_function_name")
            and tool.get_function_name() not in existing_names
        ]
        for tool in additions:
            self.tools.append(tool)
        chat = getattr(self, "chat_agent", None)
        if chat is not None and additions and hasattr(chat, "add_tools"):
            try:
                chat.add_tools(additions)
            except Exception as exc:
                logger.warning(
                    "[ProsecutorAgent %s] chat add_tools failed: %s", self.name, exc
                )

    # ── 以下属性兼容原始项目的访问模式 ──────────────────────────
    @property
    def current_handling_case(self) -> str | None:
        return self.scenario_data.get("case_id") if self.scenario_data else None

    @property
    def case_queue(self) -> list[str]:
        return []


__all__ = [
    "DEFAULT_PROCURATORATE",
    "ProsecutorAgent",
]
