"""
Tool Registry — 统一工具注册表。

管理所有工具（内置 Function + 外部 MCP），对外提供统一接口：
- list_tools(): 列出所有可用工具
- to_openai_tools(): 生成 OpenAI function calling schema
- execute(name, args): 统一执行
- register_from_mcp_client(): 自动发现 MCP Server 的工具
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.tool_agent.unified_tool import UnifiedTool, ToolResult
from src.tool_agent.adapters import wrap_mcp_tools_from_list

logger = logging.getLogger(__name__)


class ToolRegistry:
    """统一工具注册表。
    
    单例模式（通过 module-level _default_registry），也支持显式实例化。
    
    Usage:
        >>> registry = ToolRegistry()
        >>> registry.register(tool)
        >>> schemas = registry.to_openai_tools()
        >>> result = await registry.execute("query_knowledge_hub", {"query": "..."})
    """
    
    def __init__(self) -> None:
        self._tools: Dict[str, UnifiedTool] = {}
        self._mcp_clients: Dict[str, "MCPClient"] = {}  # server_name -> MCPClient
    
    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------
    
    def register(self, tool: UnifiedTool) -> None:
        """注册单个工具。"""
        if not tool.name:
            raise ValueError("Tool name cannot be empty")
        self._tools[tool.name] = tool
        logger.info(f"Registered tool: {tool.name} (type={tool.tool_type})")
    
    def register_many(self, tools: List[UnifiedTool]) -> None:
        """批量注册工具。"""
        for tool in tools:
            self.register(tool)
    
    def unregister(self, name: str) -> Optional[UnifiedTool]:
        """注销工具，返回被移除的工具。"""
        return self._tools.pop(name, None)
    
    # ------------------------------------------------------------------
    # MCP Client 集成
    # ------------------------------------------------------------------
    
    async def register_from_mcp_client(
        self,
        mcp_client: "MCPClient",
        server_name: str,
        timeout_seconds: float = 30.0,
    ) -> List[UnifiedTool]:
        """从 MCP Client 自动发现工具并注册。
        
        Args:
            mcp_client: 已连接的 MCPClient 实例
            server_name: 服务器标识（用于日志和后续管理）
            timeout_seconds: 工具超时
            
        Returns:
            注册成功的 UnifiedTool 列表
        """
        self._mcp_clients[server_name] = mcp_client
        
        try:
            tools_info = await mcp_client.list_tools()
        except Exception as e:
            logger.error(f"Failed to list tools from MCP server '{server_name}': {e}")
            return []
        
        unified_tools = wrap_mcp_tools_from_list(
            tools_info=tools_info,
            mcp_client=mcp_client,
            timeout_seconds=timeout_seconds,
        )
        
        for tool in unified_tools:
            # 避免冲突：MCP 工具名加上 server_name 前缀
            original_name = tool.name
            tool.name = f"{server_name}.{original_name}"
            tool.description = f"[{server_name}] {tool.description}"
            self.register(tool)
        
        logger.info(
            f"Registered {len(unified_tools)} tools from MCP server '{server_name}'"
        )
        return unified_tools
    
    async def disconnect_all_mcp(self) -> None:
        """断开所有 MCP Client 连接。"""
        for name, client in self._mcp_clients.items():
            try:
                await client.disconnect()
                logger.info(f"Disconnected MCP client: {name}")
            except Exception as e:
                logger.warning(f"Failed to disconnect MCP client '{name}': {e}")
        self._mcp_clients.clear()
    
    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    
    def get(self, name: str) -> Optional[UnifiedTool]:
        """按名称获取工具。"""
        return self._tools.get(name)
    
    def list_tools(self) -> List[UnifiedTool]:
        """列出所有已注册工具。"""
        return list(self._tools.values())
    
    def list_by_type(self, tool_type: str) -> List[UnifiedTool]:
        """按类型筛选工具。"""
        return [t for t in self._tools.values() if t.tool_type == tool_type]
    
    @property
    def tool_count(self) -> int:
        """已注册工具总数。"""
        return len(self._tools)
    
    # ------------------------------------------------------------------
    # LLM 层兼容
    # ------------------------------------------------------------------
    
    def to_openai_tools(self) -> List[Dict[str, Any]]:
        """生成 OpenAI function calling schema 列表。
        
        Returns:
            [{"type": "function", "function": {...}}, ...]
        """
        return [t.to_openai_schema() for t in self._tools.values()]
    
    def to_anthropic_tools(self) -> List[Dict[str, Any]]:
        """生成 Anthropic tool_use schema 列表。"""
        return [t.to_anthropic_schema() for t in self._tools.values()]
    
    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------
    
    async def execute(self, name: str, arguments: Dict[str, Any]) -> ToolResult:
        """根据工具名执行对应工具。
        
        Args:
            name: 工具名
            arguments: 参数 dict
            
        Returns:
            ToolResult
            
        Raises:
            KeyError: 工具不存在
        """
        tool = self._tools.get(name)
        if tool is None:
            available = list(self._tools.keys())
            return ToolResult.from_error(
                error=f"Tool '{name}' not found. Available: {available}"
            )
        
        logger.debug(f"Executing tool: {name} with args: {arguments}")
        return await tool.execute(**arguments)
    
    async def execute_many(
        self,
        calls: List[Dict[str, Any]],
    ) -> List[ToolResult]:
        """批量执行工具调用。
        
        Args:
            calls: 每个元素含 {"name": str, "arguments": dict}
            
        Returns:
            ToolResult 列表（与 calls 一一对应）
        """
        import asyncio
        
        async def _exec_one(call: Dict[str, Any]) -> ToolResult:
            name = call.get("name", "")
            args = call.get("arguments") or call.get("args") or {}
            return await self.execute(name, args)
        
        return await asyncio.gather(*[_exec_one(c) for c in calls])
    
    # ------------------------------------------------------------------
    # 序列化（用于调试 / 可观测性）
    # ------------------------------------------------------------------
    
    def to_dict(self) -> Dict[str, Any]:
        """序列化注册表状态（不含 executor）。"""
        return {
            "tool_count": len(self._tools),
            "tools": [
                {
                    "name": t.name,
                    "type": t.tool_type,
                    "description": t.description[:100] + "..." if len(t.description) > 100 else t.description,
                }
                for t in self._tools.values()
            ],
            "mcp_servers": list(self._mcp_clients.keys()),
        }


# Module-level default registry（单例）
_default_registry: Optional[ToolRegistry] = None


def get_default_registry() -> ToolRegistry:
    """获取默认全局注册表。"""
    global _default_registry
    if _default_registry is None:
        _default_registry = ToolRegistry()
    return _default_registry
