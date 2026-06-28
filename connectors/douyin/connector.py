"""
抖音直播连接器 — 通过 WebSocket + Protobuf 接入抖音直播间弹幕。

协议说明：
  抖音直播消息使用自定义二进制协议传输：
  1. WebSocket 收到原始字节 → PushFrame (protobuf)
  2. PushFrame.payload 是 gzip 压缩数据 → 解压得到 Response
  3. Response.messagesList 包含具体消息 → 根据 method 分发
  4. 弹幕对应 WebcastChatMessage，礼物对应 WebcastGiftMessage

依赖：
  - websocket-client (同步，在单独线程中运行)
  - protobuf + douyin_pb2 (消息解析)
  - requests (获取直播间信息)
"""

import asyncio
import gzip
import json
import re
import sys
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import unquote_plus

import requests
from websocket import WebSocketApp, WebSocketConnectionClosedException

from connectors.base import BaseConnector, LiveEvent

# 将项目根目录和当前目录加入 sys.path，以便导入编译后的 proto 模块
_project_root = Path(__file__).resolve().parent.parent.parent
_current_dir = Path(__file__).resolve().parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
if str(_current_dir) not in sys.path:
    sys.path.insert(0, str(_current_dir))

from douyin_pb2 import PushFrame, Response, ChatMessage  # noqa: E402


class DouyinConnector(BaseConnector):
    """
    抖音直播弹幕连接器。

    用法：
        connector = DouyinConnector()
        connector.on_message(handler)
        await connector.connect("https://live.douyin.com/123456789")
        # ... 消息处理 ...
        await connector.disconnect()
    """

    def __init__(self, fetch_timeout: float = 10.0):
        super().__init__()
        self._ws: Optional[WebSocketApp] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._room_id: str = ""
        self._room_title: str = ""
        self._fetch_timeout = fetch_timeout
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── 公开接口 ─────────────────────────────────────────────

    async def connect(self, room_url: str) -> bool:
        """
        连接到抖音直播间。

        参数：
            room_url: 抖音直播间完整 URL，如 https://live.douyin.com/361749035935
        """
        if self._connected:
            return True

        self._loop = asyncio.get_running_loop()

        # 1. 获取直播间信息（room_id / ttwid / wss_url）
        try:
            self._room_id, self._room_title, wss_url, ttwid = await asyncio.to_thread(
                self._fetch_live_room_info, room_url
            )
        except Exception as e:
            from utils.logger import logger
            logger.error(f"获取抖音直播间信息失败：{e}")
            return False

        from utils.logger import logger
        logger.info(f"抖音直播间连接中 → {self._room_title} (room_id={self._room_id})")

        # 2. 在后台线程启动 WebSocket
        self._stop_event.clear()
        self._ws_thread = threading.Thread(
            target=self._run_websocket,
            args=(wss_url, ttwid),
            daemon=True,
        )
        self._ws_thread.start()
        self._connected = True
        return True

    async def disconnect(self) -> None:
        """断开直播间连接。"""
        from utils.logger import logger
        logger.info("正在断开抖音直播间连接...")
        self._stop_event.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        self._connected = False

    async def send_message(self, content: str) -> bool:
        """抖音直播间不支持非主播发送消息。"""
        from utils.logger import logger
        logger.warning("抖音直播间不支持观众发送消息")
        return False

    # ── 直播间信息获取 ───────────────────────────────────────

    @staticmethod
    def _fetch_live_room_info(url: str) -> tuple[str, str, str, str]:
        """
        从抖音直播间页面提取连接所需信息。

        返回：
            (room_id, room_title, wss_url, ttwid)
        """
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        })

        # 先访问首页获取初始 cookies
        session.get("https://www.douyin.com/", timeout=10)

        # 从 URL 中提取房间号
        room_id_match = re.search(r'live\.douyin\.com/(\d+)', url)
        if not room_id_match:
            raise RuntimeError("无法从 URL 中提取房间号")
        room_id = room_id_match.group(1)

        # 使用 douyin webcast API 获取房间信息
        import time
        ts = int(time.time() * 1000)

        api_params = {
            "aid": 6383,
            "app_name": "douyin_web",
            "live_id": 1,
            "device_platform": "web",
            "room_id": room_id,
            "_signature": "",
            "_": ts,
        }

        api_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": url,
        }

        api_res = session.get(
            "https://live.douyin.com/webcast/room/web/enter/",
            params=api_params,
            headers=api_headers,
            timeout=10,
        )

        # 尝试备用 API：直接从页面提取 ttwid 后用另一个接口
        ttwid = session.cookies.get_dict().get("ttwid", "")

        # 从页面提取房间标题（兜底）
        match = re.search(
            r'<title>(.*?)</title>',
            session.get(url, timeout=10).text,
        )
        room_title = match.group(1) if match else f"抖音直播间 {room_id}"

        # 构建 WebSocket URL（使用基础参数）
        wss_url = (
            "wss://webcast5-ws-web-lf.douyin.com/webcast/im/push/v2/"
            "?app_name=douyin_web"
            "&version_code=180800"
            "&webcast_sdk_version=1.0.12"
            "&update_version_code=1.0.12"
            "&compress=gzip"
            "&device_platform=web"
            "&cookie_enabled=true"
            "&screen_width=1536"
            "&screen_height=864"
            "&browser_language=zh-CN"
            "&browser_platform=Win32"
            "&browser_name=Mozilla"
            "&browser_version=5.0%20(Windows%20NT%2010.0;%20WOW64)%20AppleWebKit"
            "/537.36%20(KHTML,%20like%20Gecko)%20Chrome/86.0.4240.198%20Safari/537.36"
            "&browser_online=true"
            "&tz_name=Asia/Shanghai"
            "&cursor=t-1_r-1_d-1_u-1_h-1"
            "&internal_ext=internal_src:dim|wss_push_room_id:"
            f"{room_id}"
            "|wss_push_did:0"
            "|first_req_ms:" + str(ts) + "|fetch_time:" + str(ts) + "|seq:1"
            "|wss_info:0-0-0-0|wrds_v:0"
            "&host=https://live.douyin.com"
            "&aid=6383&live_id=1&did_rule=3"
            "&endpoint=live_pc&support_wrds=1"
            "&user_unique_id=0"
            "&im_path=/webcast/im/fetch/"
            "&identity=audience"
            "&need_persist_msg_count=15"
            "&insert_task_id=&live_reason="
            f"&room_id={room_id}"
            "&heartbeatDuration=0"
            "&signature="
        )

        return room_id, room_title, wss_url, ttwid

    # ── WebSocket 线程 ───────────────────────────────────────

    def _run_websocket(self, wss_url: str, ttwid: str) -> None:
        """在后台线程中运行 WebSocket 连接（同步阻塞）。"""
        self._ws = WebSocketApp(
            url=wss_url,
            header={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/108.0.0.0 Safari/537.36"
                ),
            },
            cookie=f"ttwid={ttwid}",
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        try:
            # run_forever 自带 ping/pong 和自动重连
            self._ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            if not self._stop_event.is_set():
                from utils.logger import logger
                logger.error(f"抖音 WebSocket 异常退出：{e}")

    # ── WebSocket 回调（在 WS 线程中执行）─────────────────────

    def _on_open(self, ws) -> None:
        from utils.logger import logger
        logger.info("抖音 WebSocket 已连接")

    def _on_message(self, ws, raw_data: bytes) -> None:
        """解析收到的 Protobuf 消息并转换为 LiveEvent。"""
        try:
            frame = PushFrame()
            frame.ParseFromString(raw_data)

            # gzip 解压 payload
            try:
                origin_bytes = gzip.decompress(frame.payload)
            except gzip.BadGzipFile:
                return

            response = Response()
            response.ParseFromString(origin_bytes)

            # 发送 ACK（服务端要求时）
            if response.needAck:
                ack_frame = PushFrame()
                ack_frame.payloadType = "ack"
                ack_frame.payload = response.internalExt.encode("utf-8")
                ack_frame.logId = frame.logId
                try:
                    ws.send(ack_frame.SerializeToString())
                except WebSocketConnectionClosedException:
                    pass

            # 遍历消息列表
            for item in response.messagesList:
                self._dispatch_message(ws, item)

        except Exception as e:
            from utils.logger import logger
            logger.debug(f"解析抖音消息异常：{e}")

    def _dispatch_message(self, ws, msg_item) -> None:
        """根据消息 method 分发到对应的解析器。"""
        method = msg_item.method

        if method == "WebcastChatMessage":
            self._handle_chat_message(msg_item)
        elif method == "WebcastGiftMessage":
            self._handle_gift_message(msg_item)
        elif method == "WebcastMemberMessage":
            self._handle_enter_room(msg_item)
        elif method == "WebcastLikeMessage":
            self._handle_like_message(msg_item)

    def _handle_chat_message(self, msg_item) -> None:
        """解析弹幕消息。"""
        message = ChatMessage()
        message.ParseFromString(msg_item.payload)

        event = LiveEvent(
            platform="douyin",
            event_type="danmaku",
            user_id=str(message.user.id),
            nickname=message.user.nickName,
            content=message.content,
            raw_data={
                "user_id": str(message.user.id),
                "nickname": message.user.nickName,
                "content": message.content,
            },
        )
        self._emit(event)

    def _handle_gift_message(self, msg_item) -> None:
        """解析礼物消息。"""
        try:
            # WebcastGiftMessage 的 proto 字段
            from douyin_pb2 import WebcastGiftMessage
            gift = WebcastGiftMessage()
            gift.ParseFromString(msg_item.payload)

            # 对于重复礼物（combo），只取最后一条
            if gift.repeat_end == 1 or gift.group_id == 0:
                event = LiveEvent(
                    platform="douyin",
                    event_type="gift",
                    user_id=str(gift.user.id),
                    nickname=gift.user.nickName,
                    content=f"送出 {gift.gift_name} x{gift.repeat_count}",
                    gift_name=gift.gift_name,
                    gift_count=gift.repeat_count,
                    raw_data={
                        "gift_id": gift.gift_id,
                        "gift_name": gift.gift_name,
                        "count": gift.repeat_count,
                        "user_id": str(gift.user.id),
                        "nickname": gift.user.nickName,
                    },
                )
                self._emit(event)
        except Exception:
            # WebcastGiftMessage 可能未在 proto 中定义，跳过
            pass

    def _handle_enter_room(self, msg_item) -> None:
        """解析进房消息。"""
        try:
            from douyin_pb2 import WebcastMemberMessage
            member = WebcastMemberMessage()
            member.ParseFromString(msg_item.payload)

            event = LiveEvent(
                platform="douyin",
                event_type="enter_room",
                user_id=str(member.user.id),
                nickname=member.user.nickName,
                content=f"{member.user.nickName} 进入直播间",
                raw_data={
                    "user_id": str(member.user.id),
                    "nickname": member.user.nickName,
                },
            )
            self._emit(event)
        except Exception:
            pass

    def _handle_like_message(self, msg_item) -> None:
        """解析点赞消息。"""
        try:
            from douyin_pb2 import WebcastLikeMessage
            like = WebcastLikeMessage()
            like.ParseFromString(msg_item.payload)

            event = LiveEvent(
                platform="douyin",
                event_type="like",
                user_id=str(like.user.id),
                nickname=like.user.nickName,
                content=f"{like.user.nickName} 点赞 x{like.count}",
                raw_data={
                    "user_id": str(like.user.id),
                    "nickname": like.user.nickName,
                    "count": like.count,
                },
            )
            self._emit(event)
        except Exception:
            pass

    def _on_error(self, ws, error) -> None:
        from utils.logger import logger
        logger.error(f"抖音 WebSocket 错误：{error}")

    def _on_close(self, ws, close_status_code, close_msg) -> None:
        from utils.logger import logger
        logger.info(
            f"抖音 WebSocket 已关闭 (code={close_status_code}, msg={close_msg})"
        )
        self._connected = False
