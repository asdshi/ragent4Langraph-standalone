"""
Tool Adapters — 将本地函数和 MCP Client 调用统一包装为 UnifiedTool。

Layer 1: 适配器负责将不同来源的工具封装成统一的 execute 接口。
Layer 2: ToolRegistry 通过 UnifiedTool 统一管理和调度。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from mcp import types as mcp_types

from src.tool_agent.unified_tool import UnifiedTool, ToolResult, build_input_schema


# =============================================================================
# Function Tool Adapter
# =============================================================================

def wrap_function_tool(
    name: str,
    description: str,
    handler: Callable[..., Awaitable[Any]],
    input_schema: Optional[Dict[str, Any]] = None,
    timeout_seconds: float = 30.0,
    result_formatter: Optional[Callable[[Any], str]] = None,
) -> UnifiedTool:
    """将任意异步函数包装为 UnifiedTool。
    
    Args:
        name: 工具名
        description: 工具描述
        handler: 异步函数，签名如 async def handler(query: str, top_k: int = 5) -> Any
        input_schema: JSON Schema，为 None 时尝试从 handler 的 __annotations__ 推断
        timeout_seconds: 超时（秒）
        result_formatter: 可选的结果格式化函数，将 handler 返回值转为 str。
            为 None 时，默认使用 str(result)。
            
    Returns:
        UnifiedTool 实例
        
    Example:
        >>> async def search_docs(query: str, top_k: int = 5) -> MCPToolResponse:
        ...     ...
        >>> tool = wrap_function_tool(
        ...     name="search_docs",
        ...     description="搜索知识库文档",
        ...     handler=search_docs,
        ...     input_schema=build_input_schema(
        ...         properties={"query": {"type": "string"}, "top_k": {"type": "integer", "default": 5}},
        ...         required=["query"],
        ...     ),
        ...     result_formatter=lambda r: r.content,  # 提取 MCPToolResponse.content
        ... )
    """
    # 构建 executor
    async def _executor(**kwargs: Any) -> ToolResult:
        t0 = time.monotonic()
        try:
            # 调用 handler
            raw_result = await handler(**kwargs)
            
            # 格式化输出
            if result_formatter is not None:
                output = result_formatter(raw_result)
            else:
                output = str(raw_result) if raw_result is not None else ""
            
            # 提取结构化数据（如果返回值有 to_dict 方法）
            structured_data: Dict[str, Any] = {}
            if hasattr(raw_result, "to_dict"):
                try:
                    structured_data = raw_result.to_dict()
                except Exception:
                    pass
            elif hasattr(raw_result, "metadata"):
                try:
                    structured_data = dict(raw_result.metadata) if raw_result.metadata else {}
                except Exception:
                    pass
            
            return ToolResult(
                output=output,
                structured_data=structured_data,
                success=True,
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return ToolResult.from_error(
                error=str(e),
                latency_ms=(time.monotonic() - t0) * 1000,
            )
    
    # 推断 input_schema（如果未提供）
    schema = input_schema or _infer_schema_from_handler(handler)
    
    tool = UnifiedTool(
        name=name,
        description=description,
        input_schema=schema,
        tool_type="function",
        timeout_seconds=timeout_seconds,
    )
    tool.set_executor(_executor)
    return tool


def _infer_schema_from_handler(handler: Callable) -> Dict[str, Any]:
    """从函数签名推断 JSON Schema（简单实现）。"""
    import inspect
    sig = inspect.signature(handler)
    properties: Dict[str, Any] = {}
    required: list[str] = []
    
    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue
        
        prop: Dict[str, Any] = {}
        # 类型推断
        if param.annotation is not inspect.Parameter.empty:
            if param.annotation is str:
                prop["type"] = "string"
            elif param.annotation is int:
                prop["type"] = "integer"
            elif param.annotation is float:
                prop["type"] = "number"
            elif param.annotation is bool:
                prop["type"] = "boolean"
            else:
                prop["type"] = "string"  # fallback
        else:
            prop["type"] = "string"
        
        # 默认值 = 非必填
        if param.default is inspect.Parameter.empty:
            required.append(param_name)
        else:
            prop["default"] = param.default
        
        properties[param_name] = prop
    
    schema: Dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


# =============================================================================
# MCP Tool Adapter
# =============================================================================

def wrap_mcp_tool(
    name: str,
    description: str,
    input_schema: Dict[str, Any],
    mcp_client: "MCPClient",  # 前向引用，在运行时解析
    timeout_seconds: float = 30.0,
) -> UnifiedTool:
    """将 MCP Client 调用包装为 UnifiedTool。
    
    Args:
        name: 工具名（必须与 MCP Server 注册名一致）
        description: 工具描述
        input_schema: MCP Server 提供的 input schema
        mcp_client: MCPClient 实例
        timeout_seconds: 超时（秒）
        
    Returns:
        UnifiedTool 实例
    """
    async def _executor(**kwargs: Any) -> ToolResult:
        t0 = time.monotonic()
        try:
            # 调用 MCP Client
            call_result: mcp_types.CallToolResult = await mcp_client.call_tool(
                name=name,
                arguments=kwargs,
            )
            
            # 解析 MCP 返回内容为文本
            texts: list[str] = []
            for item in call_result.content:
                text = getattr(item, "text", None)
                if text:
                    texts.append(text)
            
            output = "\n".join(texts).strip()
            if call_result.isError:
                return ToolResult.from_error(
                    error=output or "MCP tool returned error",
                    latency_ms=(time.monotonic() - t0) * 1000,
                )
            
            return ToolResult(
                output=output,
                success=True,
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except asyncio.TimeoutError:
            return ToolResult.from_error(
                error=f"MCP tool '{name}' timed out after {timeout_seconds}s",
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return ToolResult.from_error(
                error=f"MCP tool '{name}' failed: {e}",
                latency_ms=(time.monotonic() - t0) * 1000,
            )
    
    tool = UnifiedTool(
        name=name,
        description=description,
        input_schema=input_schema,
        tool_type="mcp",
        timeout_seconds=timeout_seconds,
    )
    tool.set_executor(_executor)
    return tool


def wrap_mcp_tools_from_list(
    tools_info: list[Dict[str, Any]],
    mcp_client: "MCPClient",
    timeout_seconds: float = 30.0,
) -> list[UnifiedTool]:
    """批量将 MCP Server 返回的工具列表包装为 UnifiedTool。
    
    Args:
        tools_info: MCP Server list_tools() 返回的结果，每个元素含 name/description/inputSchema
        mcp_client: MCPClient 实例
        timeout_seconds: 统一超时
        
    Returns:
        UnifiedTool 列表
        
    Example:
        >>> tools = await mcp_client.list_tools()
        >>> unified = wrap_mcp_tools_from_list(tools, mcp_client)
    """
    result: list[UnifiedTool] = []
    for info in tools_info:
        name = info.get("name") or info.get("function", {}).get("name", "")
        description = info.get("description") or info.get("function", {}).get("description", "")
        input_schema = info.get("inputSchema") or info.get("input_schema") or info.get("parameters", {})
        
        if not name:
            continue
        
        tool = wrap_mcp_tool(
            name=name,
            description=description,
            input_schema=input_schema,
            mcp_client=mcp_client,
            timeout_seconds=timeout_seconds,
        )
        result.append(tool)
    return result
