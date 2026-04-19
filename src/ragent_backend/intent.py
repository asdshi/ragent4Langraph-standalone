"""
意图识别模块 — 三分支路由（clarify / rag / tool）。

核心改进：
1. detect_intent() 支持 LLM-based 三分类，同时保留规则 fallback
2. 工具意图通过 available_tools 列表让 LLM 自主判断
3. 分类理由（reasoning）写入 trace_events，提升可观测性
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage
from src.ragent_backend.schemas import IntentResult


# ============== 结构化 LLM 输出模型 ==============

class QueryAnalysisResult(BaseModel):
    """LLM 结构化输出：查询重写 + 子查询拆分"""
    rewritten_query: str = Field(
        description="消除所有代词和指代后的完整、独立查询"
    )
    sub_queries: List[str] = Field(
        description="如果查询包含多个并列主题，拆分为可独立执行的子查询列表；否则只放一个元素"
    )


class IntentDetectionResult(BaseModel):
    """LLM 结构化输出：意图三分类"""
    intent_type: Literal["clarify", "rag", "tool"] = Field(
        description="意图类型: clarify=需要澄清, rag=知识库检索, tool=需要调用外部工具"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="分类置信度 (0-1)"
    )
    target_tool: Optional[str] = Field(
        default=None,
        description="当 intent_type=tool 时，指定最适合的工具名"
    )
    tool_args_preview: Optional[Dict[str, Any]] = Field(
        default=None,
        description="当 intent_type=tool 时，预解析的参数（可选）"
    )
    need_clarify: bool = Field(
        default=False,
        description="是否需要澄清"
    )
    clarify_prompt: Optional[str] = Field(
        default=None,
        description="当 need_clarify=True 时，给用户的澄清提示"
    )
    reasoning: str = Field(
        description="分类理由（为什么是这个意图类型）"
    )


# ============== 查询分析 ==============

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


# ============== 意图检测（三分支） ==============

# 工具意图关键词映射（规则 fallback 用）
# 工具名必须与 ToolRegistry 中注册的实际名称完全一致
_TOOL_KEYWORDS: Dict[str, List[str]] = {
    # 内置工具
    "query_knowledge_hub": ["文档", "文件", "知识库", "资料", "帮我找", "查询"],
    "list_collections": ["集合", "collection", "有哪些文件"],
    "get_document_summary": ["摘要", "总结", "文档详情", "doc_id"],
    # MCP 外部工具（simple.* 前缀必须与注册时一致）
    "simple.web_search": ["搜索", "网上", "网页", "google", "百度", "bing", "查一下", "查查", "搜一下"],
    "simple.calculator": ["计算", "等于", "公式", "算一下", "等于多少"],
    "simple.get_current_time": ["时间", "现在几点", "日期", "当前时间"],
    "simple.list_directory": ["目录", "文件夹", "文件列表", "ls"],
}

# 通用工具意图关键词（不绑定特定工具，只判断 intent_type="tool"）
_TOOL_INTENT_KEYWORDS: List[str] = [
    "搜索", "网上", "网页", "google", "百度", "bing", "查一下", "查查", "搜一下",
    "计算", "等于", "公式", "算一下", "等于多少",
    "时间", "现在几点", "日期",
    "天气", "气温", "降水",
]


async def detect_intent(
    rewritten_query: str,
    llm=None,
    available_tools: Optional[List[Dict[str, Any]]] = None,
) -> IntentResult:
    """
    意图三分类：clarify / rag / tool。

    策略：
    1. 先检查是否需要澄清（保留现有规则）
    2. 如果有 LLM，用结构化调用做三分类（推荐）
    3. 无 LLM 时，回退到规则-based 分类

    Args:
        rewritten_query: 已重写（指代消解后）的查询
        llm: 可选的 LLM 实例
        available_tools: 可用工具列表（用于 LLM 判断 tool 意图）

    Returns:
        IntentResult
    """
    # === Step 1: 澄清检查（硬规则，不经过 LLM）===
    vague_pronouns = ["它", "这个", "那个", "that", "it", "this", "上述", "上面"]
    has_vague = any(token in rewritten_query for token in vague_pronouns)

    if len(rewritten_query.strip()) < 4 or (has_vague and len(rewritten_query) < 10):
        return IntentResult(
            intent_type="clarify",
            rewritten_query=rewritten_query,
            confidence=0.35,
            need_clarify=True,
            clarify_prompt="请补充更具体的信息，例如具体的产品名、文档名或业务指标。",
            reasoning="查询过短或包含模糊代词，需要澄清",
        )

    # === Step 2: LLM-based 三分类 ===
    if llm is not None:
        try:
            return await _detect_intent_with_llm(
                rewritten_query, llm, available_tools or []
            )
        except Exception as e:
            print(f"[Intent] LLM-based detection failed: {e}, falling back to rule-based")

    # === Step 3: 规则 fallback ===
    return _detect_intent_rule_based(rewritten_query, available_tools or [])


async def _detect_intent_with_llm(
    rewritten_query: str,
    llm,
    available_tools: List[Dict[str, Any]],
) -> IntentResult:
    """使用 LLM 做结构化意图三分类。"""

    # 构建工具描述（从 OpenAI function schema 中提取）
    tools_text = ""
    if available_tools:
        lines = []
        for t in available_tools:
            # OpenAI schema: {"type": "function", "function": {"name": ..., "description": ...}}
            func = t.get("function") or {}
            name = func.get("name") or t.get("name", "unknown")
            desc = func.get("description") or t.get("description", "无描述")
            lines.append(f"- {name}: {desc[:80]}")
        tools_text = "\n".join(lines)
    else:
        tools_text = "（当前无可用的外部工具）"

    prompt = f"""你是意图分类专家。请根据用户查询和可用工具列表，判断用户的真实意图。

分类规则：
- "clarify": 查询模糊、不完整、需要用户补充信息才能回答
- "rag": 查询涉及知识库、文档、内部资料等，可以通过检索回答
- "tool": 查询明确需要调用外部工具（如搜索网页、查天气、计算等）

可用工具列表：
{tools_text}

用户查询：{rewritten_query}

请输出结构化分类结果，包含 intent_type、confidence、reasoning 等字段。
注意：target_tool 必须从可用工具列表中选择，不能编造不存在的工具名。"""

    structured_llm = llm.with_structured_output(IntentDetectionResult, method="json_mode")
    result: IntentDetectionResult = await structured_llm.ainvoke([HumanMessage(content=prompt)])

    # 验证 target_tool 是否存在于可用工具列表中
    available_tool_names = {t.get("name", "") for t in available_tools}
    if result.target_tool and result.target_tool not in available_tool_names:
        result.target_tool = None  # 让子图自己选

    # 置信度阈值
    if result.confidence < 0.5:
        # 低置信度，默认 rag
        return IntentResult(
            intent_type="rag",
            rewritten_query=rewritten_query,
            confidence=0.6,
            reasoning=f"LLM 分类置信度过低({result.confidence:.2f})，默认回退到 rag",
        )

    return IntentResult(
        intent_type=result.intent_type,
        rewritten_query=rewritten_query,
        confidence=result.confidence,
        target_tool=result.target_tool,
        tool_args=result.tool_args_preview,
        need_clarify=result.need_clarify,
        clarify_prompt=result.clarify_prompt,
        reasoning=result.reasoning,
    )


def _detect_intent_rule_based(
    rewritten_query: str,
    available_tools: List[Dict[str, Any]] = None,
) -> IntentResult:
    """规则-based 意图分类（无 LLM 时的 fallback）。
    
    策略：
    1. 先匹配具体工具关键词，返回对应工具名
    2. 再匹配通用工具意图关键词，返回 tool 但不指定具体工具（让子图自选）
    3. 默认 rag
    """
    query_lower = rewritten_query.lower()
    available_tool_names = {t.get("name", "") for t in (available_tools or [])}

    # Step 1: 匹配具体工具关键词
    for tool_name, keywords in _TOOL_KEYWORDS.items():
        # 只推荐实际存在的工具
        if tool_name not in available_tool_names:
            continue
        for kw in keywords:
            if kw.lower() in query_lower:
                return IntentResult(
                    intent_type="tool",
                    rewritten_query=rewritten_query,
                    confidence=0.75,
                    target_tool=tool_name,
                    reasoning=f"关键词匹配工具 '{tool_name}': {kw}",
                )

    # Step 2: 通用工具意图（不指定具体工具，让子图自己选）
    for kw in _TOOL_INTENT_KEYWORDS:
        if kw.lower() in query_lower:
            return IntentResult(
                intent_type="tool",
                rewritten_query=rewritten_query,
                confidence=0.65,
                target_tool=None,  # 子图自己选工具
                reasoning=f"通用工具意图关键词: {kw}",
            )

    # 默认 rag
    return IntentResult(
        intent_type="rag",
        rewritten_query=rewritten_query,
        confidence=0.85,
        reasoning="无工具关键词匹配，默认归类为知识库检索",
    )


# ============== 子查询拆分（保留） ==============

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
