from __future__ import annotations

import asyncio
import importlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

@dataclass(frozen=True)
class SessionMemoryBundle:
    conversation_id: str
    task_id: Optional[str]
    history: List[Dict[str, Any]]
    memory_summary: str
    long_term_memory: List[Dict[str, Any]]


class RAGSessionStore:
    """Hybrid session store with Redis hot state and MySQL cold history.

    Hot memory:
    - Redis stores the latest serialized state and recent turns.

    Cold memory:
    - MySQL stores the full conversation history, summaries, and long-term memory.

    The workflow should only load the recent turns and LLM summary into state.
    """

    def __init__(self) -> None:
        self._redis = None
        self._mysql_pool = None
        self._model_blacklist: Dict[str, float] = {}
        self._recent_turns = self._env_int("RAGENT_MEMORY_RECENT_TURNS", 4)
        self._redis_state_prefix = os.getenv("RAGENT_REDIS_STATE_PREFIX", "ragent:state:")
        self._redis_blacklist_prefix = os.getenv("RAGENT_REDIS_BLACKLIST_PREFIX", "ragent:blacklist:")
        self._mysql_database = os.getenv("RAGENT_MYSQL_DATABASE", "ragent")
        self._mysql_host = os.getenv("RAGENT_MYSQL_HOST", "127.0.0.1")
        self._mysql_port = self._env_int("RAGENT_MYSQL_PORT", 3306)
        self._mysql_user = os.getenv("RAGENT_MYSQL_USER", "root")
        self._mysql_password = os.getenv("RAGENT_MYSQL_PASSWORD", "")
        self._mysql_charset = os.getenv("RAGENT_MYSQL_CHARSET", "utf8mb4")

    async def load_history(self, conversation_id: str) -> List[Dict[str, Any]]:
        bundle = await self.load_memory_bundle(conversation_id)
        return bundle.history

    async def load_memory_bundle(self, conversation_id: str) -> SessionMemoryBundle:
        state = await self._load_hot_state(conversation_id)
        if state is not None:
            return SessionMemoryBundle(
                conversation_id=conversation_id,
                task_id=state.get("task_id"),
                history=state.get("history", []),
                memory_summary=state.get("memory_summary", ""),
                long_term_memory=state.get("long_term_memory", []),
            )

        history = await self._load_history_from_mysql(conversation_id)
        summary = await self._load_summary_from_mysql(conversation_id)
        long_term_memory = await self._load_long_term_memory_from_mysql(conversation_id)
        trimmed_history = self._trim_history(history)
        await self._save_hot_state(
            conversation_id,
            {
                "conversation_id": conversation_id,
                "history": trimmed_history,
                "memory_summary": summary,
                "long_term_memory": long_term_memory,
            },
        )
        return SessionMemoryBundle(
            conversation_id=conversation_id,
            task_id=None,
            history=trimmed_history,
            memory_summary=summary,
            long_term_memory=long_term_memory,
        )

    async def save_exchange(
        self,
        conversation_id: str,
        user_query: str,
        answer: str,
        model_id: str,
        citations: str,
        task_id: Optional[str] = None,
        memory_summary: Optional[str] = None,
    ) -> None:
        now = time.time()
        user_message = {
            "role": "user",
            "content": user_query,
            "ts": now,
        }
        assistant_message = {
            "role": "assistant",
            "content": answer,
            "model_id": model_id,
            "citations": citations,
            "ts": now,
        }

        await self._ensure_mysql_schema()
        await self._insert_message(conversation_id, user_message)
        await self._insert_message(conversation_id, assistant_message)

        if memory_summary is not None:
            await self._upsert_summary(conversation_id, memory_summary)

        history = await self._load_history_from_mysql(conversation_id)
        trimmed_history = self._trim_history(history)
        summary = memory_summary if memory_summary is not None else await self._load_summary_from_mysql(conversation_id)
        long_term_memory = await self._load_long_term_memory_from_mysql(conversation_id)

        hot_state = {
            "conversation_id": conversation_id,
            "task_id": task_id,
            "history": trimmed_history,
            "memory_summary": summary,
            "long_term_memory": long_term_memory,
            "updated_at": now,
        }
        await self._save_hot_state(conversation_id, hot_state)

    async def save_long_term_memory(
        self,
        conversation_id: str,
        content: str,
        memory_type: str = "long_term",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self._ensure_mysql_schema()
        payload = {
            "conversation_id": conversation_id,
            "memory_type": memory_type,
            "content": content,
            "metadata": json.dumps(metadata or {}, ensure_ascii=False),
            "created_at": time.time(),
        }
        await self._insert_long_term_memory(payload)
        current = await self._load_hot_state(conversation_id)
        if current is not None:
            long_term_memory = current.get("long_term_memory", [])
        else:
            long_term_memory = await self._load_long_term_memory_from_mysql(conversation_id)
        long_term_memory.append(payload)
        await self._save_hot_state(
            conversation_id,
            {
                "conversation_id": conversation_id,
                "history": (current or {}).get("history", []),
                "memory_summary": (current or {}).get("memory_summary", ""),
                "long_term_memory": long_term_memory,
            },
        )

    async def save_state(self, conversation_id: str, state: Dict[str, Any]) -> None:
        hot_state = dict(state)
        hot_state["conversation_id"] = conversation_id
        hot_state["history"] = self._trim_history(hot_state.get("history", []))
        await self._save_hot_state(conversation_id, hot_state)

    async def blacklist_model(self, model_id: str, ttl_seconds: int) -> None:
        expires_at = time.time() + ttl_seconds
        self._model_blacklist[model_id] = expires_at
        redis_client = await self._get_redis()
        if redis_client is not None:
            await redis_client.set(
                f"{self._redis_blacklist_prefix}{model_id}",
                str(expires_at),
                ex=ttl_seconds,
            )

    async def is_model_blacklisted(self, model_id: str) -> bool:
        expires_at = self._model_blacklist.get(model_id)
        if expires_at is not None and expires_at > time.time():
            return True

        redis_client = await self._get_redis()
        if redis_client is None:
            return False

        value = await redis_client.get(f"{self._redis_blacklist_prefix}{model_id}")
        if value is None:
            return False
        try:
            expires_at = float(value)
        except (TypeError, ValueError):
            return True
        if expires_at <= time.time():
            await redis_client.delete(f"{self._redis_blacklist_prefix}{model_id}")
            return False
        return True

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None
        if self._mysql_pool is not None:
            self._mysql_pool.close()
            await self._mysql_pool.wait_closed()
            self._mysql_pool = None

    def _trim_history(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if self._recent_turns <= 0:
            return list(history)

        turns: List[List[Dict[str, Any]]] = []
        current_turn: List[Dict[str, Any]] = []
        for message in history:
            current_turn.append(message)
            if message.get("role") == "assistant":
                turns.append(current_turn)
                current_turn = []
        if current_turn:
            turns.append(current_turn)

        trimmed_turns = turns[-self._recent_turns :]
        trimmed_history: List[Dict[str, Any]] = []
        for turn in trimmed_turns:
            trimmed_history.extend(turn)
        return trimmed_history

    async def _load_hot_state(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        redis_client = await self._get_redis()
        if redis_client is None:
            return None
        raw = await redis_client.get(f"{self._redis_state_prefix}{conversation_id}")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def _save_hot_state(self, conversation_id: str, state: Dict[str, Any]) -> None:
        redis_client = await self._get_redis()
        if redis_client is None:
            return
        await redis_client.set(
            f"{self._redis_state_prefix}{conversation_id}",
            json.dumps(state, ensure_ascii=False),
            ex=self._env_int("RAGENT_REDIS_STATE_TTL", 86400),
        )

    async def _get_redis(self):
        if self._redis is not None:
            return self._redis
        redis_url = os.getenv("RAGENT_REDIS_URL")
        if not redis_url:
            return None
        try:
            redis_module = importlib.import_module("redis.asyncio")
        except ImportError:
            return None
        self._redis = redis_module.from_url(redis_url, decode_responses=True)
        return self._redis

    async def _get_mysql_pool(self):
        if self._mysql_pool is not None:
            return self._mysql_pool
        mysql_url = os.getenv("RAGENT_MYSQL_URL")
        if mysql_url:
            self._mysql_pool = await self._create_pool_from_url(mysql_url)
        else:
            self._mysql_pool = await self._create_pool_from_parts()
        return self._mysql_pool

    async def _create_pool_from_url(self, mysql_url: str):
        try:
            aiomysql = importlib.import_module("aiomysql")
        except ImportError as exc:
            raise RuntimeError("aiomysql is required for MySQL session storage") from exc

        parsed = self._parse_mysql_url(mysql_url)
        return await aiomysql.create_pool(
            host=parsed["host"],
            port=parsed["port"],
            user=parsed["user"],
            password=parsed["password"],
            db=parsed["database"],
            charset=self._mysql_charset,
            autocommit=True,
            minsize=1,
            maxsize=5,
        )

    async def _create_pool_from_parts(self):
        try:
            aiomysql = importlib.import_module("aiomysql")
        except ImportError as exc:
            raise RuntimeError("aiomysql is required for MySQL session storage") from exc

        return await aiomysql.create_pool(
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

    def _parse_mysql_url(self, mysql_url: str) -> Dict[str, Any]:
        from urllib.parse import urlparse

        parsed = urlparse(mysql_url)
        if parsed.scheme not in {"mysql", "mysql+aiomysql"}:
            raise ValueError("RAGENT_MYSQL_URL must start with mysql:// or mysql+aiomysql://")
        if not parsed.hostname or not parsed.path:
            raise ValueError("Invalid RAGENT_MYSQL_URL")
        return {
            "host": parsed.hostname,
            "port": parsed.port or 3306,
            "user": parsed.username or self._mysql_user,
            "password": parsed.password or self._mysql_password,
            "database": parsed.path.lstrip("/") or self._mysql_database,
        }

    async def _ensure_mysql_schema(self) -> None:
        pool = await self._get_mysql_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_messages (
                        id BIGINT PRIMARY KEY AUTO_INCREMENT,
                        conversation_id VARCHAR(128) NOT NULL,
                        role VARCHAR(32) NOT NULL,
                        content LONGTEXT NOT NULL,
                        model_id VARCHAR(128) NULL,
                        citations LONGTEXT NULL,
                        metadata JSON NULL,
                        created_at DOUBLE NOT NULL,
                        INDEX idx_chat_messages_conversation_id_created_at (conversation_id, created_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                    """
                )
                await cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_memory_summaries (
                        conversation_id VARCHAR(128) PRIMARY KEY,
                        summary LONGTEXT NOT NULL,
                        updated_at DOUBLE NOT NULL
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                    """
                )
                await cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_long_term_memory (
                        id BIGINT PRIMARY KEY AUTO_INCREMENT,
                        conversation_id VARCHAR(128) NOT NULL,
                        memory_type VARCHAR(64) NOT NULL,
                        content LONGTEXT NOT NULL,
                        metadata JSON NULL,
                        created_at DOUBLE NOT NULL,
                        INDEX idx_chat_ltm_conversation_id_created_at (conversation_id, created_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                    """
                )

    async def _insert_message(self, conversation_id: str, message: Dict[str, Any]) -> None:
        pool = await self._get_mysql_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO chat_messages
                    (conversation_id, role, content, model_id, citations, metadata, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        conversation_id,
                        message.get("role"),
                        message.get("content"),
                        message.get("model_id"),
                        message.get("citations"),
                        json.dumps(message.get("metadata", {}), ensure_ascii=False),
                        message.get("ts", time.time()),
                    ),
                )

    async def _insert_long_term_memory(self, payload: Dict[str, Any]) -> None:
        pool = await self._get_mysql_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO chat_long_term_memory
                    (conversation_id, memory_type, content, metadata, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        payload.get("conversation_id"),
                        payload.get("memory_type"),
                        payload.get("content"),
                        payload.get("metadata"),
                        payload.get("created_at", time.time()),
                    ),
                )

    async def _upsert_summary(self, conversation_id: str, summary: str) -> None:
        pool = await self._get_mysql_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO chat_memory_summaries (conversation_id, summary, updated_at)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE summary = VALUES(summary), updated_at = VALUES(updated_at)
                    """,
                    (conversation_id, summary, time.time()),
                )

    async def _load_history_from_mysql(self, conversation_id: str) -> List[Dict[str, Any]]:
        pool = await self._get_mysql_pool()
        await self._ensure_mysql_schema()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT role, content, model_id, citations, metadata, created_at
                    FROM chat_messages
                    WHERE conversation_id = %s
                    ORDER BY created_at ASC, id ASC
                    """,
                    (conversation_id,),
                )
                rows = await cursor.fetchall()

        history: List[Dict[str, Any]] = []
        for row in rows:
            metadata = row[4]
            parsed_metadata: Dict[str, Any]
            if metadata:
                try:
                    parsed_metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    parsed_metadata = {}
            else:
                parsed_metadata = {}
            item: Dict[str, Any] = {
                "role": row[0],
                "content": row[1],
                "ts": row[5],
            }
            if row[2] is not None:
                item["model_id"] = row[2]
            if row[3] is not None:
                item["citations"] = row[3]
            if parsed_metadata:
                item["metadata"] = parsed_metadata
            history.append(item)
        return history

    async def _load_summary_from_mysql(self, conversation_id: str) -> str:
        pool = await self._get_mysql_pool()
        await self._ensure_mysql_schema()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT summary FROM chat_memory_summaries WHERE conversation_id = %s",
                    (conversation_id,),
                )
                row = await cursor.fetchone()
        if not row:
            return ""
        return row[0] or ""

    async def _load_long_term_memory_from_mysql(self, conversation_id: str) -> List[Dict[str, Any]]:
        pool = await self._get_mysql_pool()
        await self._ensure_mysql_schema()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT memory_type, content, metadata, created_at
                    FROM chat_long_term_memory
                    WHERE conversation_id = %s
                    ORDER BY created_at ASC, id ASC
                    """,
                    (conversation_id,),
                )
                rows = await cursor.fetchall()

        memories: List[Dict[str, Any]] = []
        for row in rows:
            metadata = row[2]
            parsed_metadata: Dict[str, Any]
            if metadata:
                try:
                    parsed_metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    parsed_metadata = {}
            else:
                parsed_metadata = {}
            memories.append(
                {
                    "memory_type": row[0],
                    "content": row[1],
                    "metadata": parsed_metadata,
                    "ts": row[3],
                }
            )
        return memories

    def _env_int(self, key: str, default: int) -> int:
        raw = os.getenv(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            return default


def build_session_store() -> RAGSessionStore:
    """Factory helper to keep app initialization simple."""
    return RAGSessionStore()
