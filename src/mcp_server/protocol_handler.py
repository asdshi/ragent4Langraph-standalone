"""MCP 协议处理器（JSON-RPC 2.0）。

模块职责：
1. 工具注册与输入 schema 管理。
2. 工具执行调度与统一错误处理。
3. 在初始化阶段对外声明服务能力（capabilities）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from mcp import types
from mcp.server.lowlevel import Server

from src.observability.logger import get_logger


# JSON-RPC 2.0 Error Codes
class JSONRPCErrorCodes:
    """JSON-RPC 2.0 标准错误码常量。"""

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603


@dataclass
class ToolDefinition:
    """MCP 工具定义。

包含工具名称、描述、入参 JSON Schema 与执行处理器。
    """

    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: Callable[..., Any]


@dataclass
class ProtocolHandler:
    """MCP 协议核心处理类。

该类将“协议层”与“业务工具层”解耦：
- 协议层负责路由、参数承接、错误包装。
- 业务层只需实现工具 handler，并按约定注册。

属性：
- server_name: 服务器名。
- server_version: 服务版本号。
- tools: 工具注册表（name -> ToolDefinition）。
    """

    server_name: str
    server_version: str
    tools: Dict[str, ToolDefinition] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """dataclass 初始化后补充 logger。"""
        self._logger = get_logger(log_level="INFO")

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        handler: Callable[..., Any],
    ) -> None:
        """注册 MCP 工具。

参数：
- name: 工具唯一名称。
- description: 人类可读描述。
- input_schema: 工具入参 JSON Schema。
- handler: 实际执行逻辑（通常为 async 函数）。

异常：
- ValueError: 工具重名时抛出，避免覆盖已注册工具。
        """
        if name in self.tools:
            raise ValueError(f"Tool '{name}' is already registered")

        self.tools[name] = ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
        )
        self._logger.info("Registered tool: %s", name)

    def get_tool_schemas(self) -> List[types.Tool]:
        """获取 `tools/list` 所需的工具描述列表。"""
        return [
            types.Tool(
                name=tool.name,
                description=tool.description,
                inputSchema=tool.input_schema,
            )
            for tool in self.tools.values()
        ]

    async def execute_tool(
        self, name: str, arguments: Dict[str, Any]
    ) -> types.CallToolResult:
        """按名称执行工具并统一封装返回值。

返回值规范：
- 若 handler 已返回 `CallToolResult`，直接透传。
- 若返回字符串/列表/其他对象，转换为标准文本 content。
- 出现参数错误或内部异常时，返回 `isError=True`。
        """
        if name not in self.tools:
            self._logger.warning("Tool not found: %s", name)
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"Error: Tool '{name}' not found",
                    )
                ],
                isError=True,
            )

        tool = self.tools[name]
        try:
            self._logger.info("Executing tool: %s", name)
            result = await tool.handler(**arguments)

            # 兼容不同 handler 返回类型，统一收敛为 CallToolResult。
            if isinstance(result, types.CallToolResult):
                return result
            if isinstance(result, str):
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=result)],
                    isError=False,
                )
            if isinstance(result, list):
                return types.CallToolResult(content=result, isError=False)
            # Default: convert to string
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(result))],
                isError=False,
            )

        except TypeError as e:
            # 参数错误：通常是调用参数缺失/多余或类型不匹配。
            self._logger.error("Invalid params for tool %s: %s", name, e)
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"Error: Invalid parameters - {e}",
                    )
                ],
                isError=True,
            )
        except Exception as e:
            # 内部错误：日志保留堆栈，协议返回对外通用信息，避免泄漏细节。
            self._logger.exception("Internal error executing tool %s", name)
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"Error: Internal server error while executing '{name}'",
                    )
                ],
                isError=True,
            )

    def get_capabilities(self) -> Dict[str, Any]:
        """返回 initialize 阶段的能力声明。"""
        return {
            "tools": {} if self.tools else {},
        }


def _register_default_tools(protocol_handler: ProtocolHandler) -> None:
    """注册默认工具集合。"""
    # Import and register query_knowledge_hub tool
    from src.mcp_server.tools.query_knowledge_hub import register_tool as register_query_tool
    register_query_tool(protocol_handler)
    
    # Import and register list_collections tool
    from src.mcp_server.tools.list_collections import register_tool as register_list_tool
    register_list_tool(protocol_handler)
    
    # Import and register get_document_summary tool
    from src.mcp_server.tools.get_document_summary import register_tool as register_summary_tool
    register_summary_tool(protocol_handler)


def create_mcp_server(
    server_name: str,
    server_version: str,
    protocol_handler: Optional[ProtocolHandler] = None,
    register_tools: bool = True,
) -> Server:
    """创建并配置 MCP Server。

该工厂函数会：
1. 准备协议处理器（可复用外部传入实例）。
2. 按需注册默认工具。
3. 绑定 `tools/list` 与 `tools/call` 路由。

返回可直接运行的低层 `Server` 实例。
    """
    if protocol_handler is None:
        protocol_handler = ProtocolHandler(
            server_name=server_name,
            server_version=server_version,
        )

    # Register default tools if requested
    if register_tools:
        _register_default_tools(protocol_handler)

    # Create low-level server
    server = Server(server_name)

    # Register tools/list handler
    @server.list_tools()
    async def handle_list_tools() -> List[types.Tool]:
        """Handle tools/list request."""
        return protocol_handler.get_tool_schemas()

    # Register tools/call handler
    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: Dict[str, Any]
    ) -> types.CallToolResult:
        """Handle tools/call request."""
        return await protocol_handler.execute_tool(name, arguments)

    # 将协议处理器挂在 server 上，便于测试或扩展场景读取。
    server._protocol_handler = protocol_handler  # type: ignore[attr-defined]

    return server


def get_protocol_handler(server: Server) -> ProtocolHandler:
    """从 `Server` 实例中取回关联的协议处理器。"""
    return server._protocol_handler  # type: ignore[attr-defined]
