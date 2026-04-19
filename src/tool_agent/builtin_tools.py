"""
Builtin Tools Registration — 将现有的 MCP Server 工具注册到 ToolRegistry。

实现双出口：
- MCP Server 出口：通过 protocol_handler 注册（原有逻辑不变）
- Function Tool 出口：通过 wrap_function_tool 注册到 ToolRegistry（新增）

Usage:
    >>> from src.tool_agent.builtin_tools import register_builtin_tools
    >>> from src.tool_agent.tool_registry import ToolRegistry
    >>> registry = ToolRegistry()
    >>> register_builtin_tools(registry)
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.tool_agent.adapters import wrap_function_tool
from src.tool_agent.tool_registry import ToolRegistry

# 导入现有工具类
from src.mcp_server.tools.query_knowledge_hub import (
    QueryKnowledgeHubTool,
    TOOL_NAME as QUERY_KNOWLEDGE_HUB_NAME,
    TOOL_DESCRIPTION as QUERY_KNOWLEDGE_HUB_DESCRIPTION,
    TOOL_INPUT_SCHEMA as QUERY_KNOWLEDGE_HUB_SCHEMA,
)
from src.mcp_server.tools.list_collections import (
    ListCollectionsTool,
    TOOL_NAME as LIST_COLLECTIONS_NAME,
    TOOL_DESCRIPTION as LIST_COLLECTIONS_DESCRIPTION,
    TOOL_INPUT_SCHEMA as LIST_COLLECTIONS_SCHEMA,
)
from src.mcp_server.tools.get_document_summary import (
    GetDocumentSummaryTool,
    TOOL_NAME as GET_DOCUMENT_SUMMARY_NAME,
    TOOL_DESCRIPTION as GET_DOCUMENT_SUMMARY_DESCRIPTION,
    TOOL_INPUT_SCHEMA as GET_DOCUMENT_SUMMARY_SCHEMA,
)


def _mcp_result_to_text(result: Any) -> str:
    """将 MCP CallToolResult 提取为文本。"""
    if hasattr(result, "content"):
        texts: List[str] = []
        for item in result.content:
            text = getattr(item, "text", None)
            if text:
                texts.append(text)
        return "\n".join(texts).strip()
    return str(result)


def register_builtin_tools(registry: ToolRegistry) -> None:
    """注册所有内置工具到 ToolRegistry。
    
    Args:
        registry: ToolRegistry 实例
    """
    _register_query_knowledge_hub(registry)
    _register_list_collections(registry)
    _register_get_document_summary(registry)


def _register_query_knowledge_hub(registry: ToolRegistry) -> None:
    """注册 query_knowledge_hub 工具。"""
    tool = QueryKnowledgeHubTool()

    async def handler(query: str, top_k: int = 5, collection: str = None) -> Any:
        return await tool.execute(query=query, top_k=top_k, collection=collection)

    unified_tool = wrap_function_tool(
        name=QUERY_KNOWLEDGE_HUB_NAME,
        description=QUERY_KNOWLEDGE_HUB_DESCRIPTION,
        handler=handler,
        input_schema=QUERY_KNOWLEDGE_HUB_SCHEMA,
        result_formatter=lambda r: r.content if hasattr(r, "content") else str(r),
    )
    registry.register(unified_tool)


def _register_list_collections(registry: ToolRegistry) -> None:
    """注册 list_collections 工具。"""
    tool = ListCollectionsTool()

    async def handler(include_stats: bool = True) -> Any:
        return await tool.execute(include_stats=include_stats)

    unified_tool = wrap_function_tool(
        name=LIST_COLLECTIONS_NAME,
        description=LIST_COLLECTIONS_DESCRIPTION,
        handler=handler,
        input_schema=LIST_COLLECTIONS_SCHEMA,
        result_formatter=_mcp_result_to_text,
    )
    registry.register(unified_tool)


def _register_get_document_summary(registry: ToolRegistry) -> None:
    """注册 get_document_summary 工具。"""
    tool = GetDocumentSummaryTool()

    async def handler(doc_id: str, collection: str = None) -> Any:
        return await tool.execute(doc_id=doc_id, collection=collection)

    unified_tool = wrap_function_tool(
        name=GET_DOCUMENT_SUMMARY_NAME,
        description=GET_DOCUMENT_SUMMARY_DESCRIPTION,
        handler=handler,
        input_schema=GET_DOCUMENT_SUMMARY_SCHEMA,
        result_formatter=_mcp_result_to_text,
    )
    registry.register(unified_tool)
