"""
对话归档存储

职责：只存储用户可见的完整历史（冷存储）
不负责模型状态管理（那是 checkpointer 的事）

设计原则：
1. 异步写入，不阻塞主流程
2. 批量插入，减少数据库压力
3. 支持加载完整历史供用户查看
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class SessionMemoryBundle:
    """
    从 checkpointer 加载的记忆包
    注意：这不直接来自 MySQL，而是来自 LangGraph 的 checkpoint
    """
    conversation_id: str
    task_id: Optional[str]
    messages: List[Any]  # 最近的消息（给模型用）
    summary: str         # 滚动摘要（给模型用）


class ConversationArchiveStore:
    """
    对话归档存储
    
    只负责：
    1. 将归档的消息批量写入 MySQL（用户可见的完整历史）
    2. 从 MySQL 加载完整历史供用户查看
    
    不负责：
    - 模型状态管理（由 LangGraph checkpointer 处理）
    - Redis 热存储（移除，改为纯 checkpointer）
    """

    def __init__(self) -> None:
        self._mysql_pool = None
        self._mysql_host = os.getenv("RAGENT_MYSQL_HOST", "127.0.0.1")
        self._mysql_port = self._env_int("RAGENT_MYSQL_PORT", 3306)
        self._mysql_user = os.getenv("RAGENT_MYSQL_USER", "root")
        self._mysql_password = os.getenv("RAGENT_MYSQL_PASSWORD", "")
        self._mysql_database = os.getenv("RAGENT_MYSQL_DATABASE", "ragent")
        self._mysql_charset = os.getenv("RAGENT_MYSQL_CHARSET", "utf8mb4")

    async def append_to_history(
        self, 
        conversation_id: str, 
        messages: List[Dict[str, Any]]
    ) -> None:
        """
        批量追加消息到历史记录（异步调用）
        
        Args:
            conversation_id: 对话 ID
            messages: 要归档的消息列表，每项包含 role, content, message_id, ts
        """
        if not messages:
            return
        
        try:
            pool = await self._get_mysql_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    values = [
                        (
                            conversation_id,
                            msg["role"],
                            msg["content"],
                            msg.get("message_id", ""),
                            msg.get("ts", time.time())
                        )
                        for msg in messages
                    ]
                    
                    await cursor.executemany(
                        """INSERT INTO conversation_archive 
                           (conversation_id, role, content, message_id, created_at) 
                           VALUES (%s, %s, %s, %s, %s)""",
                        values
                    )
        except Exception as e:
            # 归档失败不应该影响主流程，但应该记录日志
            print(f"[ArchiveStore] Failed to append history: {e}")
            # 这里可以接入 sentry 等监控系统

    async def load_full_history(self, conversation_id: str) -> List[Dict[str, Any]]:
        """
        加载完整历史（给用户看）
        这是从 MySQL 加载，不是从 checkpoint 加载
        """
        pool = await self._get_mysql_pool()
        await self._ensure_schema()
        
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT role, content, message_id, created_at
                       FROM conversation_archive 
                       WHERE conversation_id = %s 
                       ORDER BY created_at ASC, id ASC""",
                    (conversation_id,)
                )
                rows = await cursor.fetchall()
        
        return [
            {
                "role": row[0],
                "content": row[1],
                "message_id": row[2],
                "ts": row[3]
            }
            for row in rows
        ]

    async def close(self) -> None:
        """关闭连接池"""
        if self._mysql_pool is not None:
            self._mysql_pool.close()
            await self._mysql_pool.wait_closed()
            self._mysql_pool = None

    async def _get_mysql_pool(self):
        """获取或创建 MySQL 连接池"""
        if self._mysql_pool is not None:
            return self._mysql_pool
        
        try:
            import aiomysql
        except ImportError as exc:
            raise RuntimeError("aiomysql is required for MySQL archive storage") from exc
        
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
        await self._ensure_schema()
        return self._mysql_pool

    async def _ensure_schema(self) -> None:
        """确保表结构存在"""
        pool = await self._get_mysql_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS conversation_archive (
                        id BIGINT PRIMARY KEY AUTO_INCREMENT,
                        conversation_id VARCHAR(128) NOT NULL,
                        role VARCHAR(32) NOT NULL,
                        content LONGTEXT NOT NULL,
                        message_id VARCHAR(64) NULL,
                        created_at DOUBLE NOT NULL,
                        INDEX idx_archive_conversation_time (conversation_id, created_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                    """
                )

    def _env_int(self, key: str, default: int) -> int:
        """读取环境变量并转为整数"""
        raw = os.getenv(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            return default


def build_archive_store() -> ConversationArchiveStore:
    """工厂函数"""
    return ConversationArchiveStore()
