"""
Agent Brain — 核心决策引擎。

功能：
- 接收弹幕消息，编排完整的 感知→思考→行动 循环
- 协调 LLM、记忆、人设、情感引擎 协同工作
- 输出结构化响应（文本 + 情感 + 动作）
"""

import asyncio
import json
import re
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
            long_term_memories=context.get("long_term_memories", ""),
        )

        # 4. 组装 messages（OpenAI 格式）
        messages = [
            {"role": "system", "content": system_prompt},
        ]
        # 加入近期聊天上下文
        messages.extend(self._memory.short_term.to_chat_format())

        # 加入当前弹幕消息（让 LLM 知道用户说了什么）
        messages.append({"role": "user", "content": msg.raw_content})

        # 5. 调用 LLM
        logger.info(
            f"Brain 处理消息：user={msg.display_name}, content={msg.raw_content}"
        )
        response = await self._llm.chat(messages)

        # 6. 保存对话到短期记忆（供后续上下文使用）
        self._memory.short_term.add("user", msg.raw_content, msg.display_name)
        if response.should_reply():
            self._memory.short_term.add("assistant", response.content)

        # 6.5 保存消息到数据库
        sid = self._memory.persistent.session_id
        if sid:
            await self._memory.save_message(sid, "user", msg.raw_content, user_id)
            if response.should_reply():
                await self._memory.save_message(
                    sid, "assistant", response.content, user_id,
                    emotion=response.emotion.category,
                    action=response.action,
                    inner_thought=response.inner_thought,
                )
            # 定期提取用户洞察（fire-and-forget，不阻塞主循环）
            asyncio.create_task(self._maybe_extract_insights(user_id))

        # 7. 验证情感标签（如果 LLM 返回非法值，用规则推测）
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
                f"content={response.content}"
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
        response = await self._llm.chat(messages)

        # 保存欢迎语到数据库
        sid = self._memory.persistent.session_id
        if sid and response.should_reply():
            await self._memory.save_message(
                sid, "assistant", response.content,
                emotion=response.emotion.category,
                action="greet",
            )

        return response

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
        response = await self._llm.chat(messages)

        # 保存感谢语到数据库
        sid = self._memory.persistent.session_id
        if sid and response.should_reply():
            await self._memory.save_message(
                sid, "assistant", response.content,
                emotion=response.emotion.category,
                action="thank_gift",
            )

        return response

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

    # ── 用户洞察提取（fire-and-forget）────────────

    async def _maybe_extract_insights(self, user_id: int) -> None:
        """根据互动次数阈值，异步提取用户标签和长期记忆（fire-and-forget）。"""
        try:
            interval_tags = self._config.get("tag_extraction_interval", 10)
            interval_memory = self._config.get("memory_extraction_interval", 15)

            row = await self._memory.persistent._db.fetch_one(
                "SELECT interaction_count, tags FROM users WHERE id = ?", (user_id,)
            )
            if not row:
                return
            count = row["interaction_count"]

            if count > 0 and count % interval_tags == 0:
                asyncio.create_task(self._extract_tags(user_id, row["tags"] or ""))

            if count > 0 and count % interval_memory == 0:
                asyncio.create_task(self._extract_memories(user_id))
        except Exception as e:
            logger.warning(f"用户洞察触发失败：{e}")

    async def _extract_tags(self, user_id: int, existing_tags: str) -> None:
        """用 LLM 分析近期对话，提取用户兴趣标签。"""
        try:
            rows = await self._memory.persistent._db.fetch_all(
                """SELECT role, content FROM messages
                   WHERE user_id = ? AND role = 'user'
                   ORDER BY created_at DESC LIMIT 20""",
                (user_id,),
            )
            if not rows:
                return

            user_messages = "\n".join(f"- {r['content']}" for r in reversed(rows))

            prompt = f"""根据以下用户的历史弹幕，提取ta的兴趣标签（逗号分隔，最多5个）。
已有标签：{existing_tags or "无"}
用户弹幕：
{user_messages}

请直接输出标签列表（纯文本，逗号分隔），不要JSON或其他格式。"""

            messages = [
                {"role": "system", "content": "你是一个用户画像分析助手。根据弹幕内容提取兴趣标签。"},
                {"role": "user", "content": prompt},
            ]
            result = await self._llm.chat_raw(messages)
            result = result.strip()

            # 合并新旧标签，去重
            new_tags = [t.strip() for t in result.split(",") if t.strip()]
            old_tags = [t.strip() for t in existing_tags.split(",") if t.strip()] if existing_tags else []
            all_tags = list(dict.fromkeys(new_tags + old_tags))[:10]  # 最多保留10个

            await self._memory.persistent.update_user_tags(user_id, ",".join(all_tags))
            logger.info(f"用户标签更新：user_id={user_id}, tags={all_tags}")
        except Exception as e:
            logger.warning(f"标签提取失败：{e}")

    async def _extract_memories(self, user_id: int) -> None:
        """用 LLM 从对话中提取关键信息存入长期记忆。"""
        try:
            rows = await self._memory.persistent._db.fetch_all(
                """SELECT role, content FROM messages
                   WHERE user_id = ?
                   ORDER BY created_at DESC LIMIT 30""",
                (user_id,),
            )
            if not rows:
                return

            conversation = "\n".join(
                f"[{r['role']}] {r['content'][:100]}" for r in reversed(rows)
            )

            prompt = f"""从以下对话中提取该观众提到的关键信息。提取内容包括：
- 个人喜好/偏好（喜欢的游戏、音乐、食物等）
- 生活习惯（职业、作息、地区等）
- 明确表达的观点或态度
- 与主播的关系（老粉、新观众等）

对话：
{conversation}

请以 JSON 数组格式输出，每个条目包含 key 和 value：
[{{"key": "偏好/习惯/身份等", "value": "具体内容"}}]
只输出真正有信息量的内容，不确定的不要编造。如果没有可提取的信息，输出空数组 []。"""

            messages = [
                {"role": "system", "content": "你是一个信息提取助手。从对话中提取有价值的长期记忆。"},
                {"role": "user", "content": prompt},
            ]
            result = await self._llm.chat_raw(messages)

            # 解析 JSON
            match = re.search(r"\[.*\]", result, re.DOTALL)
            if not match:
                return

            items = json.loads(match.group())
            for item in items:
                await self._memory.persistent.remember(
                    key=item["key"],
                    value=item["value"],
                    user_id=user_id,
                    importance=0.7,
                )
            logger.info(f"长期记忆更新：user_id={user_id}, 新增{len(items)}条")
        except Exception as e:
            logger.warning(f"长期记忆提取失败：{e}")
