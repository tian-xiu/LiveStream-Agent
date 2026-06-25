"""
LLM 抽象基类 — 定义统一的大模型调用接口。

所有 LLM 适配器（DeepSeek、GPT、GLM 等）均需实现此接口，
确保上层 Agent 无需关心底层模型差异。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Emotion:
    """情感标签。"""
    category: str = "calm"           # happy | excited | calm | sympathetic | funny | serious | warm
    intensity: float = 0.5           # 0.0 ~ 1.0


@dataclass
class LLMResponse:
    """LLM 统一响应结构。

    所有适配器必须输出此格式，无论底层模型原始输出如何。
    """
    content: str                     # 回复文本
    emotion: Emotion = field(default_factory=Emotion)
    action: str = "reply"            # reply | greet | thank_gift | ignore | question
    inner_thought: str = ""          # LLM 内心独白（不入库，仅日志）

    @classmethod
    def empty(cls, reason: str = "") -> "LLMResponse":
        """创建空响应（用于过滤/跳过的情况）。"""
        return cls(
            content="",
            action="ignore",
            inner_thought=reason,
        )

    def should_reply(self) -> bool:
        """是否需要对外输出回复。"""
        return self.action != "ignore" and bool(self.content.strip())


class BaseLLMAdapter(ABC):
    """LLM 适配器抽象基类。"""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> LLMResponse:
        """
        发起一次对话请求，返回结构化响应。

        参数：
            messages: 标准 OpenAI 消息列表 [{"role":"system","content":"..."}, ...]
            **kwargs: 传递给底层 API 的额外参数（temperature 等）

        返回：
            LLMResponse: 统一结构化响应
        """
        ...

    @abstractmethod
    async def chat_raw(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        """
        发起一次对话请求，返回原始文本（不经过结构化解析）。

        用于不需要结构化输出的场景（如生成摘要、标题等）。
        """
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """返回当前使用的模型名称。"""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """返回当前使用的提供商名称。"""
        ...
