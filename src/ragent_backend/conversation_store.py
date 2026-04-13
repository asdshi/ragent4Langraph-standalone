"""
对话列表管理

职责：
1. 管理对话的基本信息（ID、标题、创建时间等）
2. 提供对话的 CRUD 操作
3. 支持对话列表查询（分页、排序）
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Optional, Dict, Any
from pathlib import Path


@dataclass
class Conversation:
    """对话基本信息"""
    conversation_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    file_count: int = 0
    status: str = "active"  # active, archived, deleted
    metadata: Optional[Dict[str, Any]] = None


class ConversationStore:
    """
    对话列表存储管理器
    
    支持两种后端：
    1. MySQL（优先，如果配置了 RAGENT_MYSQL_HOST）
    2. SQLite（备选，存储在 data/db/conversations.db）
    """
    
    def __init__(self, db_path: str = "./data/db/conversations.db") -> None:
        # 数据库配置
        self._mysql_enabled = bool(os.getenv("RAGENT_MYSQL_HOST"))
        self._sqlite_path = Path(db_path).resolve()
        self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        
        if self._mysql_enabled:
            self._mysql_host = os.getenv("RAGENT_MYSQL_HOST", "127.0.0.1")
            self._mysql_port = int(os.getenv("RAGENT_MYSQL_PORT", "3306"))
            self._mysql_user = os.getenv("RAGENT_MYSQL_USER", "root")
            self._mysql_password = os.getenv("RAGENT_MYSQL_PASSWORD", "")
            self._mysql_database = os.getenv("RAGENT_MYSQL_DATABASE", "ragent")
            self._mysql_charset = os.getenv("RAGENT_MYSQL_CHARSET", "utf8mb4")
        
        self._mysql_pool = None
        
        # 确保表结构存在
        if not self._mysql_enabled:
            self._ensure_sqlite_schema()
    
    def _get_db_connection(self):
        """获取 SQLite 连接"""
        conn = sqlite3.connect(str(self._sqlite_path))
        conn.row_factory = sqlite3.Row
        return conn
    
    def _ensure_sqlite_schema(self) -> None:
        """确保 SQLite 表结构存在"""
        with self._get_db_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    message_count INTEGER DEFAULT 0,
                    file_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    metadata TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_conv_updated 
                ON conversations (updated_at DESC)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_conv_status 
                ON conversations (status)
            """)
    
    async def create_conversation(self, title: Optional[str] = None) -> Conversation:
        """创建新对话"""
        conversation_id = f"conv_{uuid.uuid4().hex[:16]}"
        now = datetime.now()
        title = title or f"New Chat {now.strftime('%m-%d %H:%M')}"
        
        conv = Conversation(
            conversation_id=conversation_id,
            title=title,
            created_at=now,
            updated_at=now,
            message_count=0,
            file_count=0,
            status="active",
            metadata={}
        )
        
        await self._save_conversation(conv)
        return conv
    
    async def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """获取单个对话信息"""
        if self._mysql_enabled:
            return await self._get_conversation_mysql(conversation_id)
        return self._get_conversation_sqlite(conversation_id)
    
    async def list_conversations(
        self, 
        status: str = "active",
        limit: int = 100,
        offset: int = 0
    ) -> List[Conversation]:
        """获取对话列表（按更新时间倒序）"""
        if self._mysql_enabled:
            return await self._list_conversations_mysql(status, limit, offset)
        return self._list_conversations_sqlite(status, limit, offset)
    
    async def update_conversation(
        self, 
        conversation_id: str,
        title: Optional[str] = None,
        message_count: Optional[int] = None,
        file_count: Optional[int] = None,
        status: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> bool:
        """更新对话信息"""
        conv = await self.get_conversation(conversation_id)
        if not conv:
            return False
        
        if title is not None:
            conv.title = title
        if message_count is not None:
            conv.message_count = message_count
        if file_count is not None:
            conv.file_count = file_count
        if status is not None:
            conv.status = status
        if metadata is not None:
            conv.metadata = {**(conv.metadata or {}), **metadata}
        
        conv.updated_at = datetime.now()
        await self._save_conversation(conv)
        return True
    
    async def delete_conversation(self, conversation_id: str) -> bool:
        """删除对话（软删除）"""
        return await self.update_conversation(conversation_id, status="deleted")
    
    async def archive_conversation(self, conversation_id: str) -> bool:
        """归档对话"""
        return await self.update_conversation(conversation_id, status="archived")
    
    # ============ MySQL 实现 ============
    
    async def _get_mysql_pool(self):
        """获取 MySQL 连接池"""
        if not self._mysql_enabled:
            return None
        if self._mysql_pool is not None:
            return self._mysql_pool
        
        try:
            import aiomysql
        except ImportError as exc:
            raise RuntimeError("aiomysql is required") from exc
        
        self._mysql_pool = await aiomysql.create_pool(
            host=self._mysql_host,
            port=self._mysql_port,
            user=self._mysql_user,
            password=self._mysql_password,
            db=self._mysql_database,
            charset=self._mysql_charset,
            autocommit=True,
            minsize=1,
            maxsize=5,
        )
        await self._ensure_mysql_schema()
        return self._mysql_pool
    
    async def _ensure_mysql_schema(self) -> None:
        """确保 MySQL 表结构存在"""
        pool = await self._get_mysql_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS conversations (
                        conversation_id VARCHAR(128) PRIMARY KEY,
                        title VARCHAR(512) NOT NULL,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL,
                        message_count INT DEFAULT 0,
                        file_count INT DEFAULT 0,
                        status VARCHAR(32) DEFAULT 'active',
                        metadata JSON,
                        INDEX idx_updated (updated_at DESC),
                        INDEX idx_status (status)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
    
    async def _save_conversation(self, conv: Conversation) -> None:
        """保存对话（自动选择 MySQL 或 SQLite）"""
        if self._mysql_enabled:
            await self._save_conversation_mysql(conv)
        else:
            self._save_conversation_sqlite(conv)
    
    async def _save_conversation_mysql(self, conv: Conversation) -> None:
        """保存到 MySQL"""
        pool = await self._get_mysql_pool()
        if pool is None:
            return
        
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    INSERT INTO conversations 
                    (conversation_id, title, created_at, updated_at, message_count, file_count, status, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                    title = VALUES(title),
                    updated_at = VALUES(updated_at),
                    message_count = VALUES(message_count),
                    file_count = VALUES(file_count),
                    status = VALUES(status),
                    metadata = VALUES(metadata)
                """, (
                    conv.conversation_id,
                    conv.title,
                    conv.created_at,
                    conv.updated_at,
                    conv.message_count,
                    conv.file_count,
                    conv.status,
                    json.dumps(conv.metadata) if conv.metadata else None
                ))
    
    async def _get_conversation_mysql(self, conversation_id: str) -> Optional[Conversation]:
        """从 MySQL 获取对话"""
        pool = await self._get_mysql_pool()
        if pool is None:
            return None
        
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT * FROM conversations WHERE conversation_id = %s",
                    (conversation_id,)
                )
                row = await cursor.fetchone()
                return self._row_to_conversation_mysql(row) if row else None
    
    async def _list_conversations_mysql(
        self, status: str, limit: int, offset: int
    ) -> List[Conversation]:
        """从 MySQL 获取列表"""
        pool = await self._get_mysql_pool()
        if pool is None:
            return []
        
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    SELECT * FROM conversations 
                    WHERE status = %s 
                    ORDER BY updated_at DESC 
                    LIMIT %s OFFSET %s
                """, (status, limit, offset))
                rows = await cursor.fetchall()
                return [self._row_to_conversation_mysql(row) for row in rows]
    
    # ============ SQLite 实现 ============
    
    def _save_conversation_sqlite(self, conv: Conversation) -> None:
        """保存到 SQLite"""
        with self._get_db_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO conversations 
                (conversation_id, title, created_at, updated_at, message_count, file_count, status, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                conv.conversation_id,
                conv.title,
                conv.created_at.isoformat(),
                conv.updated_at.isoformat(),
                conv.message_count,
                conv.file_count,
                conv.status,
                json.dumps(conv.metadata) if conv.metadata else None
            ))
    
    def _get_conversation_sqlite(self, conversation_id: str) -> Optional[Conversation]:
        """从 SQLite 获取对话"""
        with self._get_db_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?",
                (conversation_id,)
            )
            row = cursor.fetchone()
            return self._row_to_conversation_sqlite(row) if row else None
    
    def _list_conversations_sqlite(
        self, status: str, limit: int, offset: int
    ) -> List[Conversation]:
        """从 SQLite 获取列表"""
        with self._get_db_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM conversations 
                WHERE status = ? 
                ORDER BY updated_at DESC 
                LIMIT ? OFFSET ?
            """, (status, limit, offset))
            rows = cursor.fetchall()
            return [self._row_to_conversation_sqlite(row) for row in rows]
    
    # ============ 辅助方法 ============
    
    def _row_to_conversation_mysql(self, row: tuple) -> Conversation:
        """MySQL 行转 Conversation"""
        return Conversation(
            conversation_id=row[0],
            title=row[1],
            created_at=row[2] if isinstance(row[2], datetime) else datetime.fromisoformat(str(row[2])),
            updated_at=row[3] if isinstance(row[3], datetime) else datetime.fromisoformat(str(row[3])),
            message_count=row[4],
            file_count=row[5],
            status=row[6],
            metadata=json.loads(row[7]) if row[7] else {}
        )
    
    def _row_to_conversation_sqlite(self, row: sqlite3.Row) -> Conversation:
        """SQLite 行转 Conversation"""
        return Conversation(
            conversation_id=row["conversation_id"],
            title=row["title"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            message_count=row["message_count"],
            file_count=row["file_count"],
            status=row["status"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {}
        )


import json


def build_conversation_store() -> ConversationStore:
    """工厂函数"""
    return ConversationStore()
