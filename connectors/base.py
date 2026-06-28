"""
连接器抽象基类 — 定义直播平台接入的统一接口。

所有平台连接器（抖音、B站等）必须实现此接口。
新增平台只需：
1. 继承 BaseConnector
2. 实现 connect / disconnect / send_message
3. 通过 on_message 回调对外发送消息
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class LiveEvent:
    """直播间事件 — 统一的内部消息格式。"""

    platform: str                       # douyin / bilibili
    event_type: str                     # danmaku / gift / enter_room / like / system

    # 用户信息
    user_id: str = ""                   # 平台内唯一用户ID
    nickname: str = ""                  # 用户昵称

    # 内容
    content: str = ""                   # 弹幕文本 / 礼物名称

    # 礼物专用
    gift_name: str = ""
    gift_count: int = 1

    # 原始数据（调试用）
    raw_data: Optional[dict[str, Any]] = None


MessageCallback = Callable[[LiveEvent], None]
"""消息回调类型：接收 LiveEvent，无返回值。"""


class BaseConnector(ABC):
    """
    直播平台连接器抽象基类。

    生命周期：
        connector = DouyinConnector()
        connector.on_message(handler)
        await connector.connect(room_id)
        # ... 消息处理 ...
        await connector.disconnect()
    """

    def __init__(self):
        self._callbacks: list[MessageCallback] = []
        self._connected: bool = False

    @property
    def is_connected(self) -> bool:
        """是否已连接到直播间。"""
        return self._connected

    @abstractmethod
    async def connect(self, room_id: str) -> bool:
        """
        连接到指定直播间。

        参数：
            room_id: 直播间ID（平台相关格式）

        返回：
            True 表示连接成功
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接，清理资源。"""
        ...

    @abstractmethod
    async def send_message(self, content: str) -> bool:
        """
        发送消息到直播间。

        注意：大多数平台限制非主播账号发送消息，
        此方法可能抛出 NotImplementedError。

        返回：
            True 表示发送成功
        """
        ...

    def on_message(self, callback: MessageCallback) -> None:
        """
        注册消息回调。每当收到直播间事件时调用。

        参数：
            callback: 接收 LiveEvent 的回调函数
        """
        self._callbacks.append(callback)

    def _emit(self, event: LiveEvent) -> None:
        """向所有注册的回调发送事件。"""
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception as e:
                # 回调异常不应中断消息流，但需要记录详细错误
                import traceback
                from utils.logger import logger
                logger.error(f"消息回调异常 ({cb.__name__ if hasattr(cb, '__name__') else type(cb).__name__}): {e}")
                logger.error(traceback.format_exc())
