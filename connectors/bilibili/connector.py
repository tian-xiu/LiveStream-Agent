"""
B站直播连接器 — 通过 bilibili_api 接入B站直播间弹幕。

依赖：
  bilibili-api-python（可选，未安装时连接器不可用）
"""

import asyncio
from typing import Optional

from connectors.base import BaseConnector, LiveEvent


class BilibiliConnector(BaseConnector):
    """
    B站直播弹幕连接器。

    用法：
        connector = BilibiliConnector()
        connector.on_message(handler)
        await connector.connect("23564688")
        # ... 消息处理 ...
        await connector.disconnect()
    """

    def __init__(self):
        super().__init__()
        self._room: Optional[object] = None  # LiveDanmaku 实例
        self._room_id: str = ""

    async def connect(self, room_id: str) -> bool:
        """
        连接到B站直播间。

        参数：
            room_id: B站直播间ID（数字字符串，如 "23564688"）
        """
        if self._connected:
            return True

        try:
            from bilibili_api import live
        except ImportError:
            from utils.logger import logger
            logger.error(
                "bilibili-api-python 未安装，无法使用B站连接器。"
                "请执行：pip install bilibili-api-python"
            )
            return False

        self._room_id = room_id

        from utils.logger import logger
        logger.info(f"B站直播间连接中 → room_id={room_id}")

        # 创建弹幕客户端
        self._room = live.LiveDanmaku(room_id)

        # 注册事件回调
        @self._room.on("DANMU_MSG")
        async def _on_danmaku(event):
            """弹幕消息回调。"""
            data = event["data"]["info"]
            content = data[1]  # 弹幕文本
            user_info = data[2]  # 用户信息数组
            user_name = user_info[1]  # 用户昵称
            uid = str(user_info[0])  # 用户UID

            live_event = LiveEvent(
                platform="bilibili",
                event_type="danmaku",
                user_id=uid,
                nickname=user_name,
                content=content,
                raw_data={
                    "uid": uid,
                    "uname": user_name,
                    "content": content,
                },
            )
            self._emit(live_event)

        @self._room.on("SEND_GIFT")
        async def _on_gift(event):
            """礼物消息回调。"""
            data = event["data"]["data"]
            uid = str(data["uid"])
            uname = data["uname"]
            gift_name = data["giftName"]
            gift_num = data.get("num", 1)

            live_event = LiveEvent(
                platform="bilibili",
                event_type="gift",
                user_id=uid,
                nickname=uname,
                content=f"送出 {gift_name} x{gift_num}",
                gift_name=gift_name,
                gift_count=gift_num,
                raw_data={
                    "uid": uid,
                    "uname": uname,
                    "gift_name": gift_name,
                    "num": gift_num,
                },
            )
            self._emit(live_event)

        @self._room.on("INTERACT_WORD")
        async def _on_enter(event):
            """进房消息回调。"""
            data = event["data"]["data"]
            uid = str(data["uid"])
            uname = data["uname"]

            live_event = LiveEvent(
                platform="bilibili",
                event_type="enter_room",
                user_id=uid,
                nickname=uname,
                content=f"{uname} 进入直播间",
                raw_data={
                    "uid": uid,
                    "uname": uname,
                },
            )
            self._emit(live_event)

        # 在后台任务中启动 bilibili_api 的 WebSocket 连接（避免阻塞主事件循环）
        self._connected = True
        logger.info(f"B站直播间已连接 → room_id={room_id}")

        async def _run_room():
            try:
                await self._room.connect()
            except Exception as e:
                from utils.logger import logger
                logger.error(f"B站直播间 WebSocket 异常退出：{e}")
            finally:
                self._connected = False

        asyncio.create_task(_run_room())
        return True

    async def disconnect(self) -> None:
        """断开B站直播间连接。"""
        from utils.logger import logger
        logger.info("正在断开B站直播间连接...")
        if self._room:
            try:
                await self._room.disconnect()
            except Exception as e:
                logger.warning(f"B站断开连接异常：{e}")
        self._connected = False

    async def send_message(self, content: str) -> bool:
        """B站直播间不支持非主播发送弹幕。"""
        from utils.logger import logger
        logger.warning("B站直播间不支持观众发送消息")
        return False
