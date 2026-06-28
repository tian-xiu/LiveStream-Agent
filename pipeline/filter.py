"""
消息过滤器 — 去重、垃圾检测、格式校验。

作为 Pipeline 的第一道关卡，决定一条消息是否值得进入后续处理。
不需要的外部依赖，所有逻辑纯 Python 实现。
"""

import hashlib
import re
import time
from collections import OrderedDict
from typing import Optional

from storage.models import PipelineMessage


class MessageFilter:
    """
    消息过滤器。

    功能：
    - 内容去重（基于哈希的滑动窗口，同样内容短期内不重复处理）
    - 垃圾关键词检测
    - 最短消息长度校验
    - 纯表情/无意义消息识别
    """

    def __init__(
        self,
        min_length: int = 2,
        spam_keywords: Optional[list[str]] = None,
        dedup_window: int = 60,
        dedup_max_size: int = 200,
    ):
        """
        参数：
            min_length: 最短消息长度（字符），低于此值视为无意义
            spam_keywords: 垃圾关键词列表，包含任一即过滤
            dedup_window: 去重窗口（秒），同样内容在此时间内只处理一次
            dedup_max_size: 去重缓存最大条目数
        """
        self.min_length = min_length
        self.spam_keywords = spam_keywords or []
        self.dedup_window = dedup_window
        self.dedup_max_size = dedup_max_size

        # 去重缓存：{content_hash: timestamp}
        self._seen: OrderedDict[str, float] = OrderedDict()
        # 纯表情正则（常见颜文字、emoji 连用等）
        self._emoji_only_pattern = re.compile(
            r"^[\U0001F600-\U0001F64F"     # Emoticons
            r"\U0001F300-\U0001F5FF"       # Symbols & Pictographs
            r"\U0001F680-\U0001F6FF"       # Transport & Map
            r"\U0001F1E0-\U0001F1FF"       # Enclosed supplement
            r"\U00002702-\U000027B0"       # Dingbats
            r"\U00002460-\U000024FF"       # Enclosed Alphanumerics (⓪ - 🉑)
            r"\U0001F100-\U0001F1FF"       # Enclosed Alphanumeric Supplement
            r"\U0001F200-\U0001F2FF"       # Enclosed Ideographic Supplement
            r"\s\d]+$"
        )

    def should_process(self, msg: PipelineMessage) -> tuple[bool, str]:
        """
        判断消息是否应该进入处理流程。

        返回：
            (是否处理, 原因)
        """
        # 1. 非弹幕类型消息直接跳过过滤（礼物、进房等由调度器决定）
        if msg.message_type != "danmaku":
            return True, ""

        content = msg.raw_content.strip()

        # 2. 长度校验
        if len(content) < self.min_length:
            return False, f"消息过短 (len={len(content)} < {self.min_length})"

        # 3. 垃圾关键词检测
        for keyword in self.spam_keywords:
            if keyword in content:
                return False, f"包含垃圾关键词: {keyword}"

        # 4. 纯表情检测
        if self._emoji_only_pattern.match(content):
            return False, "纯表情/符号消息"

        # 5. 内容去重
        content_hash = self._hash_content(content)
        now = time.time()
        self._prune_dedup_cache(now)

        if content_hash in self._seen:
            last_seen = self._seen[content_hash]
            if now - last_seen < self.dedup_window:
                return False, f"重复消息 (距上次 {now - last_seen:.1f}s)"

        # 记录到去重缓存
        self._seen[content_hash] = now
        self._seen.move_to_end(content_hash)

        # 缓存容量控制
        while len(self._seen) > self.dedup_max_size:
            self._seen.popitem(last=False)

        return True, ""

    def filter(self, msg: PipelineMessage) -> PipelineMessage:
        """
        对消息执行过滤并标记。

        返回：
            标记后的 PipelineMessage（filtered=True 表示被过滤）
        """
        ok, reason = self.should_process(msg)
        msg.filtered = not ok
        msg.filter_reason = reason
        return msg

    # ── 内部方法 ─────────────────────────────────────────────

    @staticmethod
    def _hash_content(content: str) -> str:
        """对消息内容生成短哈希用于去重。"""
        return hashlib.md5(content.encode("utf-8")).hexdigest()[:12]

    def _prune_dedup_cache(self, now: float) -> None:
        """清除过期的去重记录。"""
        expired = [
            k for k, t in self._seen.items()
            if now - t > self.dedup_window * 2
        ]
        for k in expired:
            del self._seen[k]


def create_filter_from_config(config: dict) -> MessageFilter:
    """从 YAML 配置创建 MessageFilter 实例。"""
    pipeline_cfg = config.get("pipeline", {})
    return MessageFilter(
        min_length=pipeline_cfg.get("min_message_length", 2),
        spam_keywords=pipeline_cfg.get("spam_keywords", []),
    )
