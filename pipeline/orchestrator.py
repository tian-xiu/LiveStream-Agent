"""
管道编排器 — 串联整个消息处理流程。

这是整个系统的中枢，负责协调：
  Connector → Filter → Agent Brain → Emotion → TTS → Scheduler

编排器将各模块的输入输出串联，统一管理生命周期和异常处理。
"""

import asyncio
import time
from typing import Optional

from connectors.base import BaseConnector, LiveEvent
from pipeline.filter import MessageFilter
from pipeline.scheduler import ResponseScheduler
from storage.models import PipelineMessage


class PipelineOrchestrator:
    """
    消息处理管道编排器。

    负责：
    - 将 LiveEvent 转换为 PipelineMessage
    - 调度 Filter → Brain → Emotion → TTS 的处理流程
    - 管理连接器和调度器的生命周期
    - 异步处理，不阻塞消息接收线程

    用法：
        orchestrator = PipelineOrchestrator(
            connector=douyin_connector,
            brain=agent_brain,
            emotion=emotion_engine,
            tts=tts_engine,
            filter=msg_filter,
            scheduler=scheduler,
        )
        await orchestrator.start()
        # ... 运行中 ...
        await orchestrator.stop()
    """

    def __init__(
        self,
        connector: BaseConnector,
        brain,          # AgentBrain
        emotion,        # EmotionEngine
        tts,            # TTSEngine
        msg_filter: Optional[MessageFilter] = None,
        scheduler: Optional[ResponseScheduler] = None,
        voice_enabled: bool = True,
        skip_filter_for_events: bool = True,
    ):
        """
        参数：
            connector: 平台连接器
            brain: Agent 大脑
            emotion: 情感引擎
            tts: TTS 引擎
            msg_filter: 消息过滤器（可选，默认创建）
            scheduler: 响应调度器（可选，默认创建）
            voice_enabled: 是否启用 TTS 语音输出
            skip_filter_for_events: 礼物/进房等非弹幕事件是否跳过过滤
        """
        self._connector = connector
        self._brain = brain
        self._emotion = emotion
        self._tts = tts
        self._filter = msg_filter or MessageFilter()
        self._scheduler = scheduler or ResponseScheduler()
        self._voice_enabled = voice_enabled
        self._skip_filter_for_events = skip_filter_for_events

        # 运行时状态
        self._running = False
        self._processing_task: Optional[asyncio.Task] = None
        self._event_queue: asyncio.Queue[LiveEvent] = asyncio.Queue(maxsize=100)
        self._stats = {
            "total_events": 0,
            "filtered_out": 0,
            "replied": 0,
            "ignored": 0,
            "errors": 0,
        }

    async def start(self) -> None:
        """启动管道：注册连接器回调 → 启动处理循环。"""
        if self._running:
            return

        from utils.logger import logger

        # 注册连接器回调 — 将 LiveEvent 放入异步队列
        self._connector.on_message(self._on_live_event)
        self._running = True

        # 启动后台处理循环
        self._processing_task = asyncio.create_task(self._processing_loop())

        logger.info("管道编排器已启动，等待消息...")

    async def stop(self) -> None:
        """停止管道：取消处理循环 → 断开连接器。"""
        from utils.logger import logger
        logger.info("正在停止管道编排器...")

        self._running = False

        # 取消处理任务
        if self._processing_task:
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass

        # 断开连接器
        await self._connector.disconnect()

        logger.info("管道编排器已停止")

    # ── 连接器回调（在 WS 线程 / asyncio 事件循环中）──────────

    def _on_live_event(self, event: LiveEvent) -> None:
        """
        连接器的消息回调。

        注意：此方法可能在 WebSocket 线程中调用（抖音），
        因此使用 call_soon_threadsafe 安全入队。
        """
        try:
            loop = asyncio.get_running_loop()
            # 已在事件循环中 → 直接入队
            self._event_queue.put_nowait(event)
        except RuntimeError:
            # 不在事件循环中（WS 线程）→ 使用线程安全方式
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(
                self._event_queue.put_nowait, event
            )

    # ── 处理循环 ─────────────────────────────────────────────

    async def _processing_loop(self) -> None:
        """后台异步处理循环，从队列拉取事件并流经管道。"""
        from utils.logger import logger

        while self._running:
            try:
                # 等待新事件（带超时，便于检查运行状态）
                event = await asyncio.wait_for(
                    self._event_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                # 每 1 秒检查是否有可以出队的消息
                await self._try_dequeue_and_send()
                continue

            self._stats["total_events"] += 1

            try:
                await self._process_event(event)
            except Exception as e:
                self._stats["errors"] += 1
                logger.error(f"处理事件异常：{e}")

    async def _process_event(self, event: LiveEvent) -> None:
        """处理单个直播间事件。"""
        from utils.logger import logger

        # 1. 转换为内部消息格式
        msg = PipelineMessage(
            raw_content=event.content,
            display_name=event.nickname,
            platform=event.platform,
            platform_user_id=event.user_id,
            message_type=event.event_type,
        )

        # 2. 过滤（非弹幕事件可选跳过）
        if self._skip_filter_for_events and event.event_type != "danmaku":
            pass  # 礼物、进房等不经过垃圾过滤
        else:
            self._filter.filter(msg)
            if msg.filtered:
                self._stats["filtered_out"] += 1
                logger.debug(f"消息被过滤：{msg.filter_reason} | {msg.raw_content[:50]}")
                return

        # 3. 根据事件类型决定是否立即处理
        if event.event_type == "gift":
            await self._handle_gift(event, msg)
        elif event.event_type == "enter_room":
            await self._handle_enter_room(event, msg)
        else:
            # 弹幕 -> 入队等待调度
            accepted = self._scheduler.enqueue(msg)
            if not accepted:
                logger.debug(f"调度队列已满，丢弃消息：{msg.raw_content[:50]}")
                return

            # 尝试出队处理
            await self._try_dequeue_and_send()

        # 定期清理过期冷却记录
        if self._stats["total_events"] % 100 == 0:
            self._scheduler.cleanup_stale_cooldowns()

    async def _handle_gift(self, event: LiveEvent, msg: PipelineMessage) -> None:
        """处理礼物事件（高优先级，立即感谢）。"""
        from utils.logger import logger

        gift_name = event.gift_name or "礼物"
        logger.info(f"收到礼物：{event.nickname} → {gift_name} x{event.gift_count}")

        response = await self._brain.thank_gift(
            display_name=event.nickname,
            gift_name=gift_name,
            gift_count=event.gift_count,
        )

        if response and response.should_reply():
            msg.ai_content = response.content
            msg.ai_emotion = response.emotion.category
            msg.ai_emotion_intensity = response.emotion.intensity
            msg.ai_action = "thank_gift"
            msg.ai_inner_thought = response.inner_thought

            await self._speak(msg)
            self._stats["replied"] += 1
            self._scheduler.mark_replied(msg)

    async def _handle_enter_room(self, event: LiveEvent, msg: PipelineMessage) -> None:
        """处理进房事件。"""
        from utils.logger import logger
        logger.debug(f"观众进房：{event.nickname}")

        response = await self._brain.greet(event.nickname)

        if response and response.should_reply():
            msg.ai_content = response.content
            msg.ai_emotion = response.emotion.category
            msg.ai_emotion_intensity = response.emotion.intensity
            msg.ai_action = "greet"
            msg.ai_inner_thought = response.inner_thought

            await self._speak(msg)
            self._stats["replied"] += 1
            self._scheduler.mark_replied(msg)

    async def _try_dequeue_and_send(self) -> None:
        """尝试从调度队列取出消息并发送。"""
        from utils.logger import logger

        # 检查间隔
        if not self._scheduler.can_reply_now():
            return

        msg = self._scheduler.dequeue()
        if msg is None:
            return

        try:
            # 调用 Agent Brain 生成回复
            response = await self._brain.process(msg)

            if response.should_reply():
                msg.ai_content = response.content
                msg.ai_emotion = response.emotion.category
                msg.ai_emotion_intensity = response.emotion.intensity
                msg.ai_action = response.action
                msg.ai_inner_thought = response.inner_thought

                await self._speak(msg)
                self._stats["replied"] += 1
            else:
                self._stats["ignored"] += 1
                logger.debug(f"Agent 忽略消息：{msg.raw_content[:50]}")

            self._scheduler.mark_replied(msg)

        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"Agent 处理异常：{e}")

    async def _speak(self, msg: PipelineMessage) -> None:
        """根据消息内容和情感生成语音并播放。"""
        if not self._voice_enabled or not msg.ai_content:
            return

        from utils.logger import logger

        # 情感映射到 SSML
        ssml = self._emotion.to_ssml(
            text=msg.ai_content,
            emotion=msg.ai_emotion,
            intensity=msg.ai_emotion_intensity,
        )

        # TTS 合成 + 播放
        try:
            audio_file = await self._tts.synthesize(ssml)
            if audio_file:
                logger.info(f"语音输出：{msg.ai_content[:40]}... ({msg.ai_emotion})")
                await self._tts.play(audio_file)
        except Exception as e:
            logger.error(f"TTS 处理异常：{e}")

    # ── 统计信息 ─────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        """获取管道运行统计。"""
        return {
            **self._stats,
            "queue_size": self._scheduler.queue_size(),
            "time_until_ready": self._scheduler.time_until_ready(),
            "running": self._running,
        }
