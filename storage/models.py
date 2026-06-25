"""
数据模型 — 轻量级 dataclass 定义

所有模型与 SQLite 表一一对应，用于内存中传递和序列化，
不依赖 ORM，保持代码简洁。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ── 用户 ────────────────────────────────────────

@dataclass
class User:
    """直播间用户画像。"""
    platform_id: str                     # 平台内唯一ID
    platform: str                        # douyin / bilibili
    nickname: str
    id: Optional[int] = None             # 数据库自增ID（新建时为 None）
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    interaction_count: int = 0
    tags: str = ""                       # 逗号分隔标签
    notes: str = ""                      # Agent备注


# ── 会话 ────────────────────────────────────────

@dataclass
class Session:
    """单次直播会话。"""
    room_id: str
    platform: str
    id: Optional[int] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    message_count: int = 0
    summary: str = ""


# ── 消息 ────────────────────────────────────────

@dataclass
class Message:
    """单条消息记录。"""
    content: str
    role: str                            # user / agent / system
    session_id: Optional[int] = None
    user_id: Optional[int] = None        # 用户ID（角色为 agent 时为空）
    id: Optional[int] = None
    emotion: Optional[str] = None        # JSON: {"category":"happy","intensity":0.8}
    action: Optional[str] = None         # reply / greet / thank_gift / ignore / question
    inner_thought: Optional[str] = None
    created_at: Optional[datetime] = None

    @classmethod
    def from_danmaku(
        cls,
        content: str,
        user_id: Optional[int],
        session_id: int,
    ) -> "Message":
        """从弹幕创建用户消息。"""
        return cls(
            content=content,
            role="user",
            session_id=session_id,
            user_id=user_id,
            created_at=datetime.now(),
        )

    @classmethod
    def from_agent(
        cls,
        content: str,
        session_id: int,
        emotion: Optional[str] = None,
        action: Optional[str] = None,
        inner_thought: Optional[str] = None,
    ) -> "Message":
        """从 Agent 响应创建消息。"""
        return cls(
            content=content,
            role="agent",
            session_id=session_id,
            emotion=emotion,
            action=action,
            inner_thought=inner_thought,
            created_at=datetime.now(),
        )


# ── 记忆条目 ────────────────────────────────────

@dataclass
class Memory:
    """长期记忆条目（键值对）。"""
    key: str
    value: str
    id: Optional[int] = None
    user_id: Optional[int] = None        # NULL = 全局记忆
    importance: float = 0.5
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    access_count: int = 0


# ── 管道消息（内部流转用，不入库）───────────────

@dataclass
class PipelineMessage:
    """管道中流转的统一消息格式。"""

    raw_content: str                     # 原始弹幕内容
    display_name: str                    # 用户昵称
    platform: str                        # douyin / bilibili
    platform_user_id: str                # 平台用户ID
    message_type: str = "danmaku"        # danmaku / gift / enter_room / like / other

    # 处理过程中逐步填充
    filtered: bool = False               # 是否被过滤
    filter_reason: str = ""              # 过滤原因

    # LLM 响应
    ai_content: str = ""                 # AI 回复文本
    ai_emotion: str = ""                 # AI 情感（category）
    ai_emotion_intensity: float = 0.0
    ai_action: str = "reply"
    ai_inner_thought: str = ""

    # 内部引用
    db_user_id: Optional[int] = None     # 对应 users 表ID
    db_session_id: Optional[int] = None

    def to_agent_message(self) -> Message:
        """将 AI 响应部分转为 Message 入库。"""
        import json
        emotion_json = json.dumps({
            "category": self.ai_emotion,
            "intensity": self.ai_emotion_intensity,
        }) if self.ai_emotion else None

        return Message(
            content=self.ai_content,
            role="agent",
            session_id=self.db_session_id,
            emotion=emotion_json,
            action=self.ai_action,
            inner_thought=self.ai_inner_thought,
        )
