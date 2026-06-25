"""
记忆系统 — 短期 + 工作 + 长期三级记忆架构。

┌─────────────┬──────────┬──────────────┐
│   层级       │ 存储位置  │ 生命周期      │
├─────────────┼──────────┼──────────────┤
│ 短期记忆     │ deque    │ 当前会话      │
│ 工作记忆     │ SQLite   │ 当前会话      │
│ 长期记忆     │ SQLite   │ 跨会话持久    │
└─────────────┴──────────┴──────────────┘
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from storage.database import Database, get_database
from utils.logger import logger


# ── 短期记忆环 ────────────────────────────────

@dataclass
class ShortTermMemory:
    """基于 deque 的滑动窗口短期记忆。"""

    max_size: int = 20
    _buffer: deque[dict[str, str]] = field(default_factory=deque)

    def add(self, role: str, content: str, nickname: str = "") -> None:
        """添加一条消息到窗口。"""
        entry = {
            "role": role,
            "content": content,
            "nickname": nickname or ("AI助手" if role == "agent" else "观众"),
            "time": datetime.now().strftime("%H:%M:%S"),
        }
        if len(self._buffer) >= self.max_size:
            self._buffer.popleft()
        self._buffer.append(entry)

    def get_all(self) -> list[dict[str, str]]:
        """获取所有消息（按时间顺序）。"""
        return list(self._buffer)

    def to_chat_format(self) -> list[dict[str, str]]:
        """转为 OpenAI chat messages 格式。"""
        return [
            {"role": msg["role"], "content": msg["content"]}
            for msg in self._buffer
        ]

    def to_text(self) -> str:
        """转为可读文本（用于 prompt 注入）。"""
        lines = []
        for msg in self._buffer:
            name = msg["nickname"]
            lines.append(f"[{msg['time']}] {name}: {msg['content']}")
        return "\n".join(lines)

    def clear(self) -> None:
        """清空短期记忆。"""
        self._buffer.clear()

    def __len__(self) -> int:
        return len(self._buffer)


# ── 工作记忆 + 长期记忆（基于 SQLite）─────────

class PersistentMemory:
    """
    基于 SQLite 的工作记忆和长期记忆管理器。

    工作记忆：当前会话的关键事实（按 session_id 隔离）。
    长期记忆：跨会话持久的用户画像和全局知识。
    """

    def __init__(self, db: Database):
        self._db = db
        self._session_id: Optional[int] = None

    # ── 会话管理 ──────────────────────────────

    async def start_session(self, room_id: str, platform: str) -> int:
        """开始新会话，返回 session_id。"""
        await self._db._ensure_initialized()
        cursor = await self._db.execute(
            "INSERT INTO sessions (room_id, platform) VALUES (?, ?)",
            (room_id, platform),
        )
        self._session_id = cursor.lastrowid
        logger.info(f"会话开始：id={self._session_id}, room={room_id}, platform={platform}")
        return self._session_id

    async def end_session(self, summary: str = "") -> None:
        """结束当前会话。"""
        if self._session_id is None:
            return
        await self._db.execute(
            "UPDATE sessions SET ended_at = datetime('now'), summary = ? WHERE id = ?",
            (summary, self._session_id),
        )
        logger.info(f"会话结束：id={self._session_id}")
        self._session_id = None

    @property
    def session_id(self) -> Optional[int]:
        return self._session_id

    # ── 用户管理 ──────────────────────────────

    async def get_or_create_user(
        self,
        platform_id: str,
        platform: str,
        nickname: str,
    ) -> int:
        """获取或创建用户，返回数据库 user_id。"""
        row = await self._db.fetch_one(
            "SELECT id, interaction_count, tags FROM users WHERE platform_id = ? AND platform = ?",
            (platform_id, platform),
        )
        if row:
            # 更新最后出现时间
            await self._db.execute(
                "UPDATE users SET last_seen = datetime('now'), nickname = ?, interaction_count = interaction_count + 1 WHERE id = ?",
                (nickname, row["id"]),
            )
            return row["id"]

        cursor = await self._db.execute(
            "INSERT INTO users (platform_id, platform, nickname) VALUES (?, ?, ?)",
            (platform_id, platform, nickname),
        )
        logger.info(f"新用户注册：{nickname} (platform={platform}, id={platform_id})")
        return cursor.lastrowid

    async def get_user_profile_text(self, user_id: int) -> str:
        """获取用户画像文本（用于 prompt）。"""
        row = await self._db.fetch_one(
            "SELECT nickname, interaction_count, tags, notes FROM users WHERE id = ?",
            (user_id,),
        )
        if not row:
            return "新观众，暂无信息"

        parts = [f"昵称：{row['nickname']}"]
        parts.append(f"互动次数：{row['interaction_count']}")
        if row["tags"]:
            parts.append(f"标签：{row['tags']}")
        if row["notes"]:
            parts.append(f"备注：{row['notes']}")
        return "；".join(parts)

    async def update_user_tags(self, user_id: int, tags: str) -> None:
        """更新用户标签。"""
        await self._db.execute(
            "UPDATE users SET tags = ? WHERE id = ?",
            (tags, user_id),
        )

    async def update_user_notes(self, user_id: int, notes: str) -> None:
        """更新用户备注。"""
        await self._db.execute(
            "UPDATE users SET notes = ? WHERE id = ?",
            (notes, user_id),
        )

    # ── 消息记录 ──────────────────────────────

    async def save_message(
        self,
        session_id: int,
        role: str,
        content: str,
        user_id: Optional[int] = None,
        emotion: Optional[str] = None,
        action: Optional[str] = None,
        inner_thought: Optional[str] = None,
    ) -> int:
        """保存一条消息到数据库。"""
        cursor = await self._db.execute(
            """INSERT INTO messages (session_id, user_id, role, content, emotion, action, inner_thought)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, user_id, role, content, emotion, action, inner_thought),
        )
        # 更新会话消息计数
        await self._db.execute(
            "UPDATE sessions SET message_count = message_count + 1 WHERE id = ?",
            (session_id,),
        )
        return cursor.lastrowid

    # ── 长期记忆（键值对）──────────────────────

    async def remember(
        self,
        key: str,
        value: str,
        user_id: Optional[int] = None,
        importance: float = 0.5,
    ) -> None:
        """存储一条长期记忆（存在则更新）。"""
        await self._db.execute(
            """INSERT INTO memories (user_id, key, value, importance, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))
               ON CONFLICT(user_id, key) DO UPDATE SET
                   value = excluded.value,
                   importance = excluded.importance,
                   updated_at = datetime('now'),
                   access_count = access_count + 1""",
            (user_id, key, value, importance),
        )

    async def recall(self, key: str, user_id: Optional[int] = None) -> Optional[str]:
        """读取一条长期记忆。"""
        row = await self._db.fetch_one(
            "SELECT value FROM memories WHERE user_id IS ? AND key = ?",
            (user_id, key),
        )
        if row:
            # 更新访问计数
            await self._db.execute(
                "UPDATE memories SET access_count = access_count + 1 WHERE user_id IS ? AND key = ?",
                (user_id, key),
            )
            return row["value"]
        return None

    async def recall_all(self, user_id: Optional[int] = None) -> dict[str, str]:
        """读取某个用户（或全局）的所有记忆。"""
        rows = await self._db.fetch_all(
            "SELECT key, value FROM memories WHERE user_id IS ? ORDER BY importance DESC",
            (user_id,),
        )
        return {row["key"]: row["value"] for row in rows}

    async def forget(self, key: str, user_id: Optional[int] = None) -> None:
        """删除一条记忆。"""
        await self._db.execute(
            "DELETE FROM memories WHERE user_id IS ? AND key = ?",
            (user_id, key),
        )


# ── 组合管理器 ────────────────────────────────

class MemoryManager:
    """
    记忆系统总管理器，整合三级记忆。

    使用方式：
        mem = MemoryManager(db)
        await mem.start_session("room123", "douyin")

        # 处理消息
        user_id = await mem.get_or_create_user("uid_001", "douyin", "小明")
        mem.short_term.add("user", "你好呀", "小明")
        await mem.save_message(...)

        # 构建 LLM 上下文
        context = mem.build_context(user_id)
    """

    def __init__(self, db: Database, short_term_size: int = 20):
        self.short_term = ShortTermMemory(max_size=short_term_size)
        self.persistent = PersistentMemory(db)

    async def start_session(self, room_id: str, platform: str) -> int:
        return await self.persistent.start_session(room_id, platform)

    async def end_session(self, summary: str = "") -> None:
        self.short_term.clear()
        await self.persistent.end_session(summary)

    async def get_or_create_user(self, platform_id: str, platform: str, nickname: str) -> int:
        return await self.persistent.get_or_create_user(platform_id, platform, nickname)

    async def save_message(
        self,
        session_id: int,
        role: str,
        content: str,
        user_id: Optional[int] = None,
        emotion: Optional[str] = None,
        action: Optional[str] = None,
        inner_thought: Optional[str] = None,
    ) -> int:
        return await self.persistent.save_message(
            session_id, role, content, user_id, emotion, action, inner_thought,
        )

    async def build_context(self, user_id: Optional[int] = None) -> dict[str, str]:
        """
        为 LLM 构建当前上下文。

        返回：
            dict: {
                "recent_messages": 近期对话文本,
                "user_profile": 用户画像文本,
                "long_term_memories": 长期记忆摘要,
            }
        """
        context: dict[str, str] = {
            "recent_messages": self.short_term.to_text() or "（暂无对话）",
            "user_profile": "（新观众）",
            "long_term_memories": "（暂无长期记忆）",
        }

        if user_id is not None:
            context["user_profile"] = await self.persistent.get_user_profile_text(user_id)
            long_term = await self.persistent.recall_all(user_id)
            if long_term:
                context["long_term_memories"] = "; ".join(
                    f"{k}:{v}" for k, v in long_term.items()
                )

        return context
