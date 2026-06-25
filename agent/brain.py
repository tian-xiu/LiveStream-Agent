"""
Agent Brain — 核心决策引擎。

功能：
- 接收弹幕消息，编排完整的 感知→思考→行动 循环
- 协调 LLM、记忆、人设、情感引擎 协同工作
- 输出结构化响应（文本 + 情感 + 动作）
"""

from typing import Any, Optional

from agent.emotion import EmotionEngine
from agent.memory import MemoryManager
from agent.persona import Persona, PersonaManager
from llm.base import BaseLLMAdapter, LLMResponse
from storage.models import PipelineMessage
from utils.logger import logger


class AgentBrain:
    """
    Agent 大脑 — 直播间 AI 主播的核心决策模块。

    完整处理链路：
        弹幕 → Context Builder（记忆+人设+Prompt）→ LLM → 情感分析 → 结构化响应

    使用方式：
        brain = AgentBrain(llm_adapter, memory_manager, persona_manager)
        response = await brain.process(message)
    """

    def __init__(
        self,
        llm_adapter: BaseLLMAdapter,
        memory: MemoryManager,
        persona_mgr: PersonaManager,
        config: Optional[dict[str, Any]] = None,
    ):
        self._llm = llm_adapter
        self._memory = memory
        self._persona_mgr = persona_mgr
        self._emotion = EmotionEngine()

        # 当前加载的人设
        persona_name = (config or {}).get("persona", "default")
        self._persona: Persona = persona_mgr.load(persona_name)

        self._config = config or {}

    # ── 公开接口 ────────────────────────────────

    @property
    def persona(self) -> Persona:
        return self._persona

    def switch_persona(self, name: str) -> None:
        """运行时切换人设。"""
        self._persona = self._persona_mgr.load(name)
        logger.info(f"人设已切换为：{self._persona.name}")

    async def process(self, msg: PipelineMessage) -> LLMResponse:
        """
        处理一条管道消息，返回 LLM 结构化响应。

        参数：
            msg: 管道消息（已过滤、已关联用户和会话）

        返回：
            LLMResponse: 结构化响应
        """
        # 1. 获取或创建用户
        user_id = await self._memory.get_or_create_user(
            platform_id=msg.platform_user_id,
            platform=msg.platform,
            nickname=msg.display_name,
        )
        msg.db_user_id = user_id

        # 2. 构建上下文
        context = await self._memory.build_context(user_id)

        # 3. 构建 System Prompt
        system_prompt = self._persona_mgr.build_system_prompt(
            persona=self._persona,
            recent_messages=context["recent_messages"],
            user_profile=context["user_profile"],
        )

        # 4. 组装 messages（OpenAI 格式）
        messages = [
            {"role": "system", "content": system_prompt},
        ]
        # 加入近期聊天上下文
        messages.extend(self._memory.short_term.to_chat_format())

        # 5. 调用 LLM
        logger.info(
            f"Brain 处理消息：user={msg.display_name}, content={msg.raw_content[:50]}"
        )
        response = await self._llm.chat(messages)

        # 6. 验证情感标签（如果 LLM 返回非法值，用规则推测）
        if not EmotionEngine.is_valid(response.emotion.category):
            fallback_emotion = self._emotion.find_best_emotion(response.content)
            logger.info(f"情感标签修正：{response.emotion.category} → {fallback_emotion}")
            response.emotion.category = fallback_emotion

        # 7. 填充到管道消息（供下游使用）
        msg.ai_content = response.content
        msg.ai_emotion = response.emotion.category
        msg.ai_emotion_intensity = response.emotion.intensity
        msg.ai_action = response.action
        msg.ai_inner_thought = response.inner_thought

        # 8. 记录日志
        if not response.should_reply():
            logger.debug(f"Brain 决定忽略：{response.inner_thought}")
        else:
            logger.info(
                f"Brain 响应：action={response.action}, "
                f"emotion={response.emotion.category}({response.emotion.intensity:.1f}), "
                f"content={response.content[:50]}..."
            )

        return response

    async def greet(self, display_name: str = "") -> LLMResponse:
        """
        生成欢迎语（开播或新观众进入时使用）。

        参数：
            display_name: 新观众的昵称（空字符串 = 通用欢迎）
        """
        if display_name:
            prompt = f"请向新观众「{display_name}」打个招呼，用热情但不夸张的语气。"
        else:
            prompt = "直播刚开始，请向所有观众打个招呼，告诉大家今天的状态和心情。"

        messages = [
            {
                "role": "system",
                "content": self._persona_mgr.build_system_prompt(
                    persona=self._persona,
                    recent_messages="（刚开播）",
                    user_profile="直播间观众",
                ),
            },
            {"role": "user", "content": prompt},
        ]
        return await self._llm.chat(messages)

    async def thank_gift(
        self, display_name: str, gift_name: str = "礼物", gift_count: int = 1
    ) -> LLMResponse:
        """
        生成礼物感谢语。

        参数：
            display_name: 送礼观众昵称
            gift_name: 礼物名称
            gift_count: 礼物数量
        """
        if gift_count > 1:
            prompt = f"观众「{display_name}」送出了 {gift_count} 个「{gift_name}」！请热情地感谢ta，强调一下数量，语气可以夸张一点。"
        else:
            prompt = f"观众「{display_name}」送出了「{gift_name}」！请热情地感谢ta，语气可以夸张一点。"
        messages = [
            {
                "role": "system",
                "content": self._persona_mgr.build_system_prompt(
                    persona=self._persona,
                    recent_messages=self._memory.short_term.to_text(),
                    user_profile=display_name,
                ),
            },
            {"role": "user", "content": prompt},
        ]
        return await self._llm.chat(messages)

    async def summarize_session(self) -> str:
        """对本场直播生成摘要（用于结束会话时存档）。"""
        context = self._memory.short_term.to_text()
        if not context.strip():
            return "本场直播无对话记录"

        messages = [
            {
                "role": "system",
                "content": "请用2-3句话总结以下直播间对话的主要内容、氛围和亮点。直接输出总结文本，不要用JSON格式。",
            },
            {"role": "user", "content": f"直播对话记录：\n{context}"},
        ]
        try:
            summary = await self._llm.chat_raw(messages)
            return summary.strip()
        except Exception as e:
            logger.warning(f"生成摘要失败：{e}")
            return "（摘要生成失败）"

    async def end_session(self, summary: str = "") -> None:
        """结束当前数据库会话。"""
        await self._memory.end_session(summary)
