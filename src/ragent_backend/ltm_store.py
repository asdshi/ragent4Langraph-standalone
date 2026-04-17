"""
轻量级长期记忆存储 (Long-Term Memory Store)

设计原则：
1. 零外部依赖：复用现有的 SQLite，不引入新的向量库
2. 对话结束自动提取：从用户问题和助手回答中提炼结构化事实
3. 对话开始自动召回：用关键词粗排 + 时间衰减，取出最相关的记忆注入 prompt
4. 低开销：不增加额外的 LLM 调用（提取仅在 archive 时触发一次）

借鉴 mem0 的核心思想，但实现极度轻量，适合个人项目的快速落地。
"""

from __future__ import annotations

import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, List, Optional

from langchain_core.messages import HumanMessage


class LTMStore:
    """
    长期记忆存储

    核心能力：
    - save_facts: 保存提取后的记忆事实
    - retrieve_facts: 基于当前 query 召回相关记忆
    - extract_facts: 用 LLM 从一轮对话中自动提炼事实
    """

    def __init__(self, db_path: str = "./data/db/ltm.db") -> None:
        self._db_path = Path(db_path).resolve()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """确保长期记忆表存在（含增量列迁移）"""
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS long_term_memories (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT,
                    turn_id TEXT,
                    fact TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    access_count INTEGER DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ltm_user 
                ON long_term_memories(user_id)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ltm_conv_turn
                ON long_term_memories(conversation_id, turn_id)
                """
            )
            # 增量迁移已有表
            cursor = conn.execute("PRAGMA table_info(long_term_memories)")
            existing_cols = {row[1] for row in cursor.fetchall()}
            if 'conversation_id' not in existing_cols:
                conn.execute("ALTER TABLE long_term_memories ADD COLUMN conversation_id TEXT")
            if 'turn_id' not in existing_cols:
                conn.execute("ALTER TABLE long_term_memories ADD COLUMN turn_id TEXT")
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def save_facts(
        self,
        user_id: str,
        facts: List[str],
        conversation_id: Optional[str] = None,
        turn_id: Optional[str] = None,
    ) -> None:
        """批量保存记忆事实（自动去重）"""
        if not facts:
            return

        # 简单去重：和该 user 已有的 fact 文本对比
        existing = self._list_facts(user_id)
        existing_set = {f.lower().strip() for f in existing}
        to_insert = [
            f for f in facts
            if f and f.lower().strip() not in existing_set
        ]

        if not to_insert:
            return

        now = time.time()
        with self._conn() as conn:
            for fact in to_insert:
                conn.execute(
                    """
                    INSERT INTO long_term_memories (id, user_id, conversation_id, turn_id, fact, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), user_id, conversation_id, turn_id, fact.strip(), now),
                )
            conn.commit()

    def retrieve_facts(
        self,
        user_id: str,
        query: str,
        top_k: int = 3,
    ) -> List[str]:
        """
        召回相关记忆

        策略（零向量依赖）：
        1. 取出该 user 的所有记忆
        2. 做简单关键词覆盖匹配（query 中的实词是否在 fact 中出现）
        3. 若未命中，退回到最新的 top_k 条
        4. 命中后增加 access_count（热度信号）
        """
        rows = self._list_rows(user_id)
        if not rows:
            return []

        # 提取 query 中的中文/英文词汇（长度>1）
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
                score += hits * 10  # 关键词命中权重
            # 时间衰减：越新的记忆分越高
            age_hours = (time.time() - row["created_at"]) / 3600.0
            score += max(0, 24 - age_hours)  # 24 小时内满分
            # 访问热度
            score += row["access_count"] * 2
            scored.append((score, row["id"], fact))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_ids = [sid for _, sid, _ in scored[:top_k]]
        top_facts = [fact for _, _, fact in scored[:top_k]]

        # 更新访问计数
        if top_ids:
            with self._conn() as conn:
                placeholders = ",".join("?" * len(top_ids))
                conn.execute(
                    f"""
                    UPDATE long_term_memories
                    SET access_count = access_count + 1
                    WHERE id IN ({placeholders})
                    """,
                    top_ids,
                )
                conn.commit()

        return top_facts

    async def extract_facts(
        self,
        query: str,
        answer: str,
        llm: Any,
    ) -> List[str]:
        """
        从一轮 Q&A 中提取长期记忆事实

        规则：
        - 只提取关于用户偏好、身份、约束条件的客观事实
        - 不提取泛泛而谈的内容（如"这是一个好问题"）
        - 如果没有任何值得记忆的内容，返回空列表
        """
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
            # 尝试解析 JSON 列表
            import json
            # 去掉可能的 markdown 代码块
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

    def delete_facts_from_turn(
        self,
        conversation_id: str,
        turn_id: str,
    ) -> int:
        """删除指定 turn 产生的长期记忆事实，返回删除行数"""
        with self._conn() as conn:
            cursor = conn.execute(
                """
                DELETE FROM long_term_memories
                WHERE conversation_id = ? AND turn_id = ?
                """,
                (conversation_id, turn_id),
            )
            conn.commit()
            return cursor.rowcount

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _list_facts(self, user_id: str) -> List[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT fact FROM long_term_memories WHERE user_id = ?",
                (user_id,),
            ).fetchall()
            return [r["fact"] for r in rows]

    def _list_rows(self, user_id: str) -> List[sqlite3.Row]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, fact, created_at, access_count
                FROM long_term_memories
                WHERE user_id = ?
                ORDER BY created_at DESC
                """,
                (user_id,),
            ).fetchall()
            return rows
