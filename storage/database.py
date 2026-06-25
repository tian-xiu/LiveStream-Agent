"""
数据库模块 — 基于 aiosqlite 的异步 SQLite 管理

提供：
- 连接池（单连接复用）
- 自动建表
- 线程安全的初始化锁
"""

import asyncio
import threading
from pathlib import Path

import aiosqlite


class Database:
    """异步 SQLite 数据库管理器。"""

    def __init__(self, db_path: str = "data/agent_memory.db"):
        self._db_path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()
        self._init_lock = threading.Lock()

    # ── 存储层内部接口 ──────────────────────────

    async def _ensure_initialized(self) -> None:
        """确保数据库已连接并建表。"""
        if self._conn is not None:
            return

        async with self._lock:
            if self._conn is not None:
                return

            # 确保目录存在（线程安全）
            with self._init_lock:
                self._db_path.parent.mkdir(parents=True, exist_ok=True)

            self._conn = await aiosqlite.connect(str(self._db_path))
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA foreign_keys=ON")
            await self._create_tables()
            await self._conn.commit()

    async def _create_tables(self) -> None:
        """创建所有核心表（如果不存在）。"""
        assert self._conn is not None

        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                platform_id     TEXT    NOT NULL,       -- 平台内用户ID
                platform        TEXT    NOT NULL,       -- douyin / bilibili
                nickname        TEXT    NOT NULL,
                first_seen      TEXT    NOT NULL DEFAULT (datetime('now')),
                last_seen       TEXT    NOT NULL DEFAULT (datetime('now')),
                interaction_count INTEGER NOT NULL DEFAULT 0,
                tags            TEXT    DEFAULT '',     -- 逗号分隔的用户标签
                notes           TEXT    DEFAULT '',     -- Agent 对用户的备注
                UNIQUE(platform_id, platform)
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id         TEXT    NOT NULL,
                platform        TEXT    NOT NULL,
                started_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                ended_at        TEXT,
                message_count   INTEGER NOT NULL DEFAULT 0,
                summary         TEXT    DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      INTEGER NOT NULL,
                user_id         INTEGER,                -- NULL = Agent 自身
                role            TEXT    NOT NULL,        -- user / agent / system
                content         TEXT    NOT NULL,
                emotion         TEXT,                    -- JSON: {"category":"happy","intensity":0.8}
                action          TEXT,                    -- reply / greet / thank_gift / ignore / question
                inner_thought   TEXT,
                created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (session_id) REFERENCES sessions(id),
                FOREIGN KEY (user_id)    REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS memories (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER,                -- NULL = 全局记忆
                key             TEXT    NOT NULL,
                value           TEXT    NOT NULL,
                importance      REAL    NOT NULL DEFAULT 0.5,  -- 0~1 重要性评分
                created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                access_count    INTEGER NOT NULL DEFAULT 0,
                UNIQUE(user_id, key)
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id);
            CREATE INDEX IF NOT EXISTS idx_messages_user
                ON messages(user_id);
            CREATE INDEX IF NOT EXISTS idx_memories_user
                ON memories(user_id);
        """)

    # ── 公开接口 ──────────────────────────────────

    @property
    def db_path(self) -> str:
        return str(self._db_path)

    async def get_conn(self) -> aiosqlite.Connection:
        """获取数据库连接（自动初始化）。"""
        await self._ensure_initialized()
        assert self._conn is not None
        return self._conn

    async def execute(self, sql: str, params: tuple | dict | None = None) -> aiosqlite.Cursor:
        """执行一条 SQL，返回 cursor。"""
        conn = await self.get_conn()
        cursor = await conn.execute(sql, params or ())
        await conn.commit()
        return cursor

    async def executemany(self, sql: str, seq_of_params: list) -> aiosqlite.Cursor:
        """批量执行 SQL。"""
        conn = await self.get_conn()
        cursor = await conn.executemany(sql, seq_of_params)
        await conn.commit()
        return cursor

    async def fetch_one(self, sql: str, params: tuple | dict | None = None) -> aiosqlite.Row | None:
        """查询单行。"""
        conn = await self.get_conn()
        async with conn.execute(sql, params or ()) as cursor:
            return await cursor.fetchone()

    async def fetch_all(self, sql: str, params: tuple | dict | None = None) -> list[aiosqlite.Row]:
        """查询多行。"""
        conn = await self.get_conn()
        async with conn.execute(sql, params or ()) as cursor:
            return await cursor.fetchall()

    async def close(self) -> None:
        """关闭数据库连接。"""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None


# 全局单例 — 模块级惰性初始化
_db_instance: Database | None = None


def get_database(db_path: str = "data/agent_memory.db") -> Database:
    """获取全局数据库单例（按路径区分）。"""
    global _db_instance
    if _db_instance is None or _db_instance.db_path != str(db_path):
        _db_instance = Database(db_path)
    return _db_instance
