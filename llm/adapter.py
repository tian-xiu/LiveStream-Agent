"""
OpenAI 兼容适配器 — 统一封装所有兼容 OpenAI SDK 的大模型。

支持：DeepSeek、OpenAI GPT、智谱 GLM 等所有使用 OpenAI 格式的 API。
通过配置文件的 base_url 和 api_key 区分不同平台。
"""

import json
import re
from typing import Any, Optional

from openai import AsyncOpenAI

from llm.base import BaseLLMAdapter, Emotion, LLMResponse
from utils.logger import logger


# ── 结构化 JSON 解析 ──────────────────────────

# 合法的 emotion category 值
_VALID_EMOTIONS = {"happy", "excited", "calm", "sympathetic", "funny", "serious", "warm"}

# 合法的 action 值
_VALID_ACTIONS = {"reply", "greet", "thank_gift", "ignore", "question"}


def _extract_json(text: str) -> dict[str, Any]:
    """
    从 LLM 原始输出中提取 JSON 对象。

    处理多种常见格式：
    - 裸 JSON: {"content":"..."}
    - Markdown 代码块: ```json ... ```
    - 带前后文字的 JSON
    """
    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试匹配 ```json ... ``` 代码块
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试匹配最外层的 {...}
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"无法从 LLM 输出中提取 JSON：{text[:200]}")


def _parse_llm_response(raw_text: str) -> LLMResponse:
    """
    将 LLM 原始 JSON 输出解析为 LLMResponse。

    具备容错能力：
    - 缺少字段时使用默认值
    - emotion.category 非法时回退为 "calm"
    - action 非法时回退为 "reply"
    - intensity 越界时钳制到 0~1
    """
    data = _extract_json(raw_text)

    content = str(data.get("content", "")).strip()

    # 解析 emotion
    emo = data.get("emotion", {})
    if not isinstance(emo, dict):
        emo = {}
    category = emo.get("category", "calm")
    if category not in _VALID_EMOTIONS:
        logger.warning(f"非法 emotion.category '{category}'，回退为 'calm'")
        category = "calm"
    intensity = float(emo.get("intensity", 0.5))
    intensity = max(0.0, min(1.0, intensity))  # 钳制

    # 解析 action
    action = str(data.get("action", "reply"))
    if action not in _VALID_ACTIONS:
        logger.warning(f"非法 action '{action}'，回退为 'reply'")
        action = "reply"

    inner_thought = str(data.get("inner_thought", "")).strip()

    # ignore 动作强制 content 为空
    if action == "ignore":
        content = ""
        inner_thought = inner_thought or "已忽略（无意义消息）"

    return LLMResponse(
        content=content,
        emotion=Emotion(category=category, intensity=intensity),
        action=action,
        inner_thought=inner_thought,
    )


# ── 适配器实现 ────────────────────────────────

class OpenAICompatibleAdapter(BaseLLMAdapter):
    """
    通用 OpenAI 兼容接口适配器。

    使用方式：
        adapter = OpenAICompatibleAdapter(
            api_key="sk-xxx",
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
        )
        response = await adapter.chat([{"role":"user","content":"你好"}])
    """

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: str = "deepseek-chat",
        temperature: float = 0.8,
        max_tokens: int = 512,
        timeout: float = 15.0,
    ):
        """
        参数：
            api_key: API 密钥
            base_url: API 地址（None 则使用 OpenAI 默认）
            model: 模型名称
            temperature: 生成温度 0~2
            max_tokens: 最大输出 token 数
            timeout: 请求超时（秒）
        """
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout

        client_kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": timeout,
            "max_retries": 2,
        }
        if base_url:
            client_kwargs["base_url"] = base_url

        self._client = AsyncOpenAI(**client_kwargs)
        logger.info(f"LLM 适配器初始化：model={model}, base_url={base_url or 'default'}")

    # ── 公开接口 ────────────────────────────────

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider_name(self) -> str:
        # 从 base_url 猜测提供商
        base = str(self._client.base_url)
        if "deepseek" in base:
            return "DeepSeek"
        elif "bigmodel" in base or "zhipu" in base:
            return "智谱GLM"
        elif "openai" in base:
            return "OpenAI"
        return "Unknown"

    async def chat(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> LLMResponse:
        """
        结构化对话：返回 LLMResponse。

        内部先调用 chat_raw 获取原始文本，再解析为结构化 JSON。
        如果解析失败（模型未按 JSON 格式回复），尝试降级为纯文本回复。
        """
        raw = await self.chat_raw(messages, **kwargs)
        try:
            return _parse_llm_response(raw)
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning(f"LLM JSON 解析失败，降级为纯文本回复：{e}")
            # 降级：把整个原始文本作为 content
            return LLMResponse(
                content=raw.strip(),
                action="reply",
                inner_thought=f"(非结构化降级) {e}",
            )

    async def chat_raw(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        """
        原始对话：返回纯文本字符串。
        """
        temperature = kwargs.pop("temperature", self._temperature)
        max_tokens = kwargs.pop("max_tokens", self._max_tokens)

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
            content = response.choices[0].message.content
            if content is None:
                raise ValueError("LLM 返回空内容")
            return content.strip()

        except Exception as e:
            logger.error(f"LLM 调用失败：{e}")
            raise


# ── 工厂函数 ──────────────────────────────────

def create_adapter_from_config(config: dict[str, Any]) -> OpenAICompatibleAdapter:
    """
    从配置字典创建适配器实例。

    参数：
        config: 配置字典，包含 api_key, base_url, model, temperature, max_tokens, timeout
    """
    api_key = config.get("api_key", "")
    if api_key.startswith("${") and api_key.endswith("}"):
        # 环境变量引用（如 ${LLM_API_KEY}）— 实际使用时由 config loader 解析
        import os
        env_var = api_key[2:-1]
        api_key = os.getenv(env_var, "")
        if not api_key:
            logger.warning(f"环境变量 {env_var} 未设置，LLM 调用将失败")

    return OpenAICompatibleAdapter(
        api_key=api_key,
        base_url=config.get("base_url"),
        model=config.get("model", "deepseek-chat"),
        temperature=float(config.get("temperature", 0.8)),
        max_tokens=int(config.get("max_tokens", 512)),
        timeout=float(config.get("timeout", 15.0)),
    )
