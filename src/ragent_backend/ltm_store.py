"""
长期记忆存储 (Long-Term Memory Store) — PostgreSQL 版

设计原则：
1. 对话结束自动提取：从用户问题和助手回答中提炼结构化事实
2. 对话开始自动召回：用关键词粗排 + 时间衰减，取出最相关的记忆注入 prompt
3. 低开销：不增加额外的 LLM 调用（提取仅在 archive 时触发一次）
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Any, List, Optional

import asyncpg
from langchain_core.messages import HumanMessage


class LTMStore:
    """
    长期记忆存储 (PostgreSQL)

    核心能力：
    - save_facts: 保存提取后的记忆事实
    - retrieve_facts: 基于当前 query 召回相关记忆
    - extract_facts: 用 LLM 从一轮对话中自动提炼事实
    """

    def __init__(self) -> None:
        self._pool: Optional[asyncpg.Pool] = None
        self._dsn = os.getenv("RAGENT_POSTGRES_URL", "postgresql://postgres:postgres@localhost:5432/ragent")

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=3)
        await self._ensure_schema()
        return self._pool

    async def _ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS long_term_memories (
                    id TEXT PRIMARY KEY,
                    user_id VARCHAR(64) NOT NULL,
                    conversation_id VARCHAR(64),
                    turn_id VARCHAR(64),
                    fact TEXT NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL,
                    access_count INTEGER DEFAULT 0
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ltm_user ON long_term_memories(user_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ltm_conv_turn ON long_term_memories(conversation_id, turn_id)"
            )

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    async def save_facts(
        self,
        user_id: str,
        facts: List[str],
        conversation_id: Optional[str] = None,
        turn_id: Optional[str] = None,
    ) -> None:
        """批量保存记忆事实（自动去重）"""
        if not facts:
            return

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            # 查询该用户已有的事实
            existing_rows = await conn.fetch(
                "SELECT fact FROM long_term_memories WHERE user_id = $1",
                user_id,
            )
            existing_set = {r["fact"].lower().strip() for r in existing_rows}

            to_insert = [
                f for f in facts
                if f and f.lower().strip() not in existing_set
            ]

            if not to_insert:
                return

            now = time.time()
            await conn.executemany(
                """
                INSERT INTO long_term_memories (id, user_id, conversation_id, turn_id, fact, created_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                [
                    (str(uuid.uuid4()), user_id, conversation_id, turn_id, fact.strip(), now)
                    for fact in to_insert
                ],
            )

    async def retrieve_facts(
        self,
        user_id: str,
        query: str,
        top_k: int = 3,
    ) -> List[str]:
        """召回相关记忆"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, fact, created_at, access_count
                FROM long_term_memories
                WHERE user_id = $1
                ORDER BY created_at DESC
                """,
                user_id,
            )

        if not rows:
            return []

        query_tokens = set(
            t.lower()
            for t in re.findall(r"[a-zA-Z_]+|\S", query)
            if len(t) > 1
        )

        scored = []
        for row in rows:
            fact = row["fact"]
            score = 0
            if query_tokens:
                fact_lower = fact.lower()
                hits = sum(1 for t in query_tokens if t in fact_lower)
                score += hits * 10
            age_hours = (time.time() - row["created_at"]) / 3600.0
            score += max(0, 24 - age_hours)
            score += row["access_count"] * 2
            scored.append((score, row["id"], fact))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_ids = [sid for _, sid, _ in scored[:top_k]]
        top_facts = [fact for _, _, fact in scored[:top_k]]

        if top_ids:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE long_term_memories
                    SET access_count = access_count + 1
                    WHERE id = ANY($1)
                    """,
                    top_ids,
                )

        return top_facts

    async def extract_facts(
        self,
        query: str,
        answer: str,
        llm: Any,
    ) -> List[str]:
        """从一轮 Q&A 中提取长期记忆事实"""
        if llm is None:
            return []

        prompt = f"""你是一个记忆提取助手。请分析以下对话，提取 0~3 条关于用户的长期记忆事实。

要求：
1. 只提取关于用户身份、偏好、禁忌、工作背景的客观事实
2. 事实应简洁，一句话说完
3. 不要提取泛泛而谈或临时性的内容
4. 如果没有值得记住的内容，请返回空列表 []

【用户问题】
{query}

【助手回答】
{answer}

请直接输出 JSON 列表，格式示例：
["用户是金融风控工程师，主要使用 Python", "用户偏好简洁的技术回答，不喜欢过多业务解释"]
"""
        try:
            response = await llm.ainvoke([HumanMessage(content=prompt)])
            content = response.content.strip()
            if content.startswith("```"):
                content = content.strip("`").strip()
                if content.lower().startswith("json"):
                    content = content[4:].strip()
            facts = json.loads(content)
            if isinstance(facts, list):
                return [str(f).strip() for f in facts if str(f).strip()]
        except Exception:
            pass
        return []

    async def delete_facts_from_turn(
        self,
        conversation_id: str,
        turn_id: str,
    ) -> int:
        """删除指定 turn 产生的长期记忆事实，返回删除行数"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM long_term_memories
                WHERE conversation_id = $1 AND turn_id = $2
                """,
                conversation_id, turn_id,
            )
            return int(result.split()[-1]) if result.split()[-1].isdigit() else 0

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
