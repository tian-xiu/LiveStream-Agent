"""
响应调度器 — 频率控制、优先级排序、用户冷却。

确保 Agent 不会刷屏，同一用户短时间内不被重复回复，
高优先级消息（礼物、新观众）优先处理。
"""

import time
from collections import defaultdict
from typing import Optional

from storage.models import PipelineMessage


class ResponseScheduler:
    """
    响应调度器。

    核心规则：
    - 两次回复之间至少间隔 min_reply_interval 秒
    - 同一用户有 user_cooldown 秒冷却时间
    - 消息队列最大长度为 max_queue_size，溢出时丢弃低优先级
    - 优先级：gift > enter_room > danmaku
    """

    def __init__(
        self,
        min_reply_interval: float = 3.0,
        max_queue_size: int = 5,
        user_cooldown: float = 10.0,
    ):
        """
        参数：
            min_reply_interval: 最小回复间隔（秒）
            max_queue_size: 最大排队消息数
            user_cooldown: 同一用户冷却时间（秒）
        """
        self.min_reply_interval = min_reply_interval
        self.max_queue_size = max_queue_size
        self.user_cooldown = user_cooldown

        # 内部状态
        self._last_reply_time: float = 0.0
        self._user_last_reply: dict[str, float] = {}
        self._queue: list[tuple[int, PipelineMessage]] = []

        # 事件类型优先级（数值越大越优先）
        self._priority_map = {
            "gift": 100,
            "enter_room": 80,
            "danmaku": 50,
            "like": 30,
            "system": 10,
        }

    def can_reply_now(self) -> bool:
        """检查现在是否可以立即回复（间隔检查）。"""
        elapsed = time.time() - self._last_reply_time
        return elapsed >= self.min_reply_interval

    def can_reply_user(self, user_id: str) -> bool:
        """检查指定用户是否已过冷却期。"""
        if user_id not in self._user_last_reply:
            return True
        elapsed = time.time() - self._user_last_reply[user_id]
        return elapsed >= self.user_cooldown

    def time_until_ready(self) -> float:
        """距离可以发送下一条回复还需要多少秒。"""
        elapsed = time.time() - self._last_reply_time
        if elapsed >= self.min_reply_interval:
            return 0.0
        return self.min_reply_interval - elapsed

    def enqueue(self, msg: PipelineMessage) -> bool:
        """
        将消息加入调度队列。

        如果队列已满且新消息优先级低于队列最低优先级，则丢弃。

        返回：
            True 表示入队成功
        """
        priority = self._get_priority(msg)

        # 队列已满：尝试淘汰最低优先级
        if len(self._queue) >= self.max_queue_size:
            min_priority = min(p for p, _ in self._queue)
            if priority <= min_priority:
                return False
            # 移除最低优先级的那条
            self._queue.sort(key=lambda x: x[0])
            self._queue.pop(0)

        self._queue.append((priority, msg))
        # 按优先级降序排列
        self._queue.sort(key=lambda x: x[0], reverse=True)
        return True

    def dequeue(self) -> Optional[PipelineMessage]:
        """
        取出队列中最优先且可回复的消息。

        返回：
            PipelineMessage 或 None（无可回复消息）
        """
        if not self._queue:
            return None

        now = time.time()

        # 按优先级从高到低检查
        for i, (priority, msg) in enumerate(self._queue):
            # 用户冷却检查
            if msg.platform_user_id in self._user_last_reply:
                elapsed = now - self._user_last_reply[msg.platform_user_id]
                if elapsed < self.user_cooldown:
                    continue

            # 取出并移除
            self._queue.pop(i)
            return msg

        return None

    def mark_replied(self, msg: PipelineMessage) -> None:
        """标记消息已回复，更新间隔和用户冷却。"""
        now = time.time()
        self._last_reply_time = now
        if msg.platform_user_id:
            self._user_last_reply[msg.platform_user_id] = now

    def queue_size(self) -> int:
        """当前队列长度。"""
        return len(self._queue)

    def cleanup_stale_cooldowns(self, max_age: float = 3600.0) -> None:
        """清理过期的用户冷却记录。"""
        now = time.time()
        stale = [
            uid for uid, t in self._user_last_reply.items()
            if now - t > max_age
        ]
        for uid in stale:
            del self._user_last_reply[uid]

    # ── 内部方法 ─────────────────────────────────────────────

    def _get_priority(self, msg: PipelineMessage) -> int:
        """获取消息的处理优先级。"""
        return self._priority_map.get(msg.message_type, 50)


def create_scheduler_from_config(config: dict) -> ResponseScheduler:
    """从 YAML 配置创建 ResponseScheduler 实例。"""
    pipeline_cfg = config.get("pipeline", {})
    return ResponseScheduler(
        min_reply_interval=pipeline_cfg.get("min_reply_interval", 3),
        max_queue_size=pipeline_cfg.get("max_queue_size", 5),
        user_cooldown=pipeline_cfg.get("user_cooldown", 10),
    )
