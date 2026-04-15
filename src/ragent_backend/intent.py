from __future__ import annotations

import re
from typing import List

from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage
from src.ragent_backend.schemas import IntentResult


class QueryAnalysisResult(BaseModel):
    """LLM 结构化输出：查询重写 + 子查询拆分"""
    rewritten_query: str = Field(
        description="消除所有代词和指代后的完整、独立查询"
    )
    sub_queries: List[str] = Field(
        description="如果查询包含多个并列主题，拆分为可独立执行的子查询列表；否则只放一个元素"
    )


async def analyze_query(query: str, messages: list, llm=None) -> QueryAnalysisResult:
    """
    单次结构化 LLM 调用：同时完成指代消解和子查询拆分。
    如果 LLM 不可用或调用失败，回退到规则-based 处理。
    """
    cleaned = " ".join(query.split())

    # 没有 LLM，直接做基础 fallback
    if llm is None:
        return QueryAnalysisResult(
            rewritten_query=cleaned,
            sub_queries=_fallback_split(cleaned)
        )

    # 取最近最多 4 轮对话作为上下文
    recent = messages[-4:] if messages else []
    history_lines = []
    for m in recent:
        role = "User" if m.type == "human" else "Assistant"
        content = str(getattr(m, "content", "")).strip()
        if content:
            history_lines.append(f"{role}: {content}")

    history_text = "\n".join(history_lines)

    prompt = f"""你是一个查询分析助手。请根据对话历史，将用户的当前问题处理为独立、完整的查询单元。

处理要求：
1. 消除所有代词和指代（如"它"、"这个"、"that"、"这个文档"、"上面说的"、"前者"等），替换为对话历史中提到的具体实体。
2. 如果当前问题包含多个并列主题（如多个城市、多个产品、多个时间段的比较），即使没有连词也必须拆分成可独立执行的子查询列表。
3. 如果问题只涉及单一主题，sub_queries 列表中只放一个元素即可。
4. 每个子查询必须完整、无歧义、不依赖上下文即可理解。

示例 1：
当前问题：北京上海杭州的天气怎么样
输出：{{"rewritten_query": "北京、上海、杭州的天气怎么样", "sub_queries": ["北京的天气怎么样", "上海的天气怎么样", "杭州的天气怎么样"]}}

示例 2：
当前问题：华为和苹果的旗舰手机对比
输出：{{"rewritten_query": "华为和苹果的旗舰手机对比", "sub_queries": ["华为旗舰手机", "苹果旗舰手机"]}}

示例 3：
当前问题：2024年英伟达财报表现如何
输出：{{"rewritten_query": "2024年英伟达财报表现如何", "sub_queries": ["2024年英伟达财报表现如何"]}}

对话历史：
{history_text}

当前问题：{cleaned}

请直接输出 JSON 对象，不要添加任何解释或 Markdown 格式。"""

    try:
        structured_llm = llm.with_structured_output(QueryAnalysisResult, method="json_mode")
        result: QueryAnalysisResult = await structured_llm.ainvoke([HumanMessage(content=prompt)])

        # 后处理：确保 sub_queries 非空
        if not result.sub_queries:
            result.sub_queries = [result.rewritten_query or cleaned]

        # 清洗：去掉子查询前后的空白和标点
        result.sub_queries = [
            sq.strip(" ,，。？！?！")
            for sq in result.sub_queries
            if sq.strip(" ,，。？！?！")
        ] or [result.rewritten_query or cleaned]

        return result
    except Exception as e:
        print(f"[Intent] Structured query analysis failed: {e}")
        # Fallback: 用旧逻辑兜底
        rewritten = await rewrite_query(cleaned, messages, llm)
        return QueryAnalysisResult(
            rewritten_query=rewritten,
            sub_queries=_fallback_split(rewritten)
        )


def _fallback_split(query: str) -> List[str]:
    """LLM 失败时的子查询拆分回退"""
    return split_parallel_subqueries(query)


async def rewrite_query(query: str, messages: list, llm=None) -> str:
    """
    （保留用于兼容性 fallback）
    真正的查询重写：拼接最近对话历史，消除指代消解。
    """
    cleaned = " ".join(query.split())

    if not messages or llm is None:
        return cleaned

    recent = messages[-4:]
    history_lines = []
    for m in recent:
        role = "User" if m.type == "human" else "Assistant"
        content = str(getattr(m, "content", "")).strip()
        if content:
            history_lines.append(f"{role}: {content}")

    history_text = "\n".join(history_lines)
    if not history_text:
        return cleaned

    prompt = f"""你是一个查询重写助手。请根据对话历史，将用户的当前问题改写为一个独立、完整、没有歧义的新查询。
改写要求：
1. 必须消除所有代词和指代（如"它"、"这个"、"that"、"这个文档"、"上面说的"等），替换为对话历史中提到的具体实体。
2. 如果当前问题涉及多个实体，保持它们的关系和比较意图。
3. 只输出改写后的查询，不要加任何解释、引号或前缀。

对话历史：
{history_text}

当前问题：{cleaned}

改写后查询："""

    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        rewritten = response.content.strip()
        rewritten = re.sub(r'^(改写后查询[：:]?\s*|[""「])', '', rewritten).strip()
        return rewritten if rewritten else cleaned
    except Exception:
        return cleaned


def detect_intent(rewritten_query: str) -> IntentResult:
    """
    基于已重写后的查询做意图判断。
    此时指代问题已被消除，只需做简单校验。
    """
    vague_pronouns = ["它", "这个", "那个", "that", "it", "this", "上述", "上面"]
    has_vague = any(token in rewritten_query for token in vague_pronouns)

    if len(rewritten_query.strip()) < 4 or (has_vague and len(rewritten_query) < 10):
        return IntentResult(
            rewritten_query=rewritten_query,
            confidence=0.35,
            need_clarify=True,
            clarify_prompt="请补充更具体的信息，例如具体的产品名、文档名或业务指标。",
        )

    return IntentResult(
        rewritten_query=rewritten_query,
        confidence=0.92,
        need_clarify=False,
        clarify_prompt=None,
    )


def split_parallel_subqueries(query: str) -> List[str]:
    """将包含多个并列主题的查询拆分为子查询列表（保留作为 fallback）。"""
    normalized = query.strip()
    if not normalized:
        return []

    segments = re.split(r"[；;]|，并|,\s*and\s+", normalized)

    pieces: List[str] = []
    for seg in segments:
        seg = seg.strip(" ,，。？！?！")
        if not seg:
            continue

        sub_parts = re.split(r"\s*(?:和|与|以及|及|and)\s*", seg)
        if len(sub_parts) <= 1:
            pieces.append(seg)
            continue

        tail = sub_parts[-1].strip()
        predicate_match = re.search(r"(是.*|有.*|怎么.*|如何.*|多少.*|哪些.*|是什么.*)$", tail)
        predicate = predicate_match.group(1) if predicate_match else ""

        for idx, part in enumerate(sub_parts):
            part = part.strip(" ,，。？！?！")
            if not part:
                continue
            if idx < len(sub_parts) - 1 and predicate:
                pieces.append(f"{part}{predicate}")
            else:
                pieces.append(part)

    deduped: List[str] = []
    seen = set()
    for piece in pieces:
        item = piece.strip(" ,，。？！?！")
        if not item or item in seen:
            continue
        seen.add(item)
        deduped.append(item)

    return deduped or [normalized]
