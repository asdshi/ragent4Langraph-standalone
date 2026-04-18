"""
对话列表管理 (PostgreSQL 版)

职责：
1. 管理对话的基本信息（ID、标题、创建时间等）
2. 提供对话的 CRUD 操作
3. 支持对话列表查询（分页、排序）
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Optional, Dict, Any

import asyncpg


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
    对话列表存储管理器 (PostgreSQL)
    """

    def __init__(self) -> None:
        self._pool: Optional[asyncpg.Pool] = None
        self._dsn = os.getenv("RAGENT_POSTGRES_URL", "postgresql://postgres:postgres@localhost:5432/ragent")

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        await self._ensure_schema()
        return self._pool

    async def _ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id VARCHAR(128) PRIMARY KEY,
                    title VARCHAR(512) NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    message_count INTEGER DEFAULT 0,
                    file_count INTEGER DEFAULT 0,
                    status VARCHAR(32) DEFAULT 'active',
                    metadata JSONB
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at DESC)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conv_status ON conversations(status)"
            )

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
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM conversations WHERE conversation_id = $1",
                conversation_id,
            )
            return self._row_to_conversation(row) if row else None

    async def list_conversations(
        self,
        status: str = "active",
        limit: int = 100,
        offset: int = 0
    ) -> List[Conversation]:
        """获取对话列表（按更新时间倒序）"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM conversations
                WHERE status = $1
                ORDER BY updated_at DESC
                LIMIT $2 OFFSET $3
                """,
                status, limit, offset,
            )
            return [self._row_to_conversation(row) for row in rows]

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

    async def _save_conversation(self, conv: Conversation) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO conversations
                (conversation_id, title, created_at, updated_at, message_count, file_count, status, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (conversation_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    updated_at = EXCLUDED.updated_at,
                    message_count = EXCLUDED.message_count,
                    file_count = EXCLUDED.file_count,
                    status = EXCLUDED.status,
                    metadata = EXCLUDED.metadata
                """,
                conv.conversation_id,
                conv.title,
                conv.created_at,
                conv.updated_at,
                conv.message_count,
                conv.file_count,
                conv.status,
                json.dumps(conv.metadata) if conv.metadata else None,
            )

    def _row_to_conversation(self, row: asyncpg.Record) -> Conversation:
        """asyncpg Record 转 Conversation"""
        return Conversation(
            conversation_id=row["conversation_id"],
            title=row["title"],
            created_at=row["created_at"] if isinstance(row["created_at"], datetime) else datetime.fromisoformat(str(row["created_at"])),
            updated_at=row["updated_at"] if isinstance(row["updated_at"], datetime) else datetime.fromisoformat(str(row["updated_at"])),
            message_count=row["message_count"],
            file_count=row["file_count"],
            status=row["status"],
            metadata=row["metadata"] if isinstance(row["metadata"], dict) else (json.loads(row["metadata"]) if row["metadata"] else {}),
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


def build_conversation_store() -> ConversationStore:
    """工厂函数"""
    return ConversationStore()
