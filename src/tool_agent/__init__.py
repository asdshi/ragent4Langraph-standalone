"""
Tool Agent — 统一工具抽象层。

同时兼容 Function Calling（OpenAI/Anthropic 原生）和 MCP（Model Context Protocol）。
"""

from src.tool_agent.unified_tool import UnifiedTool, ToolResult, ToolDecision
from src.tool_agent.tool_registry import ToolRegistry
from src.tool_agent.mcp_client import MCPClient

__all__ = [
    "UnifiedTool",
    "ToolResult",
    "ToolDecision",
    "ToolRegistry",
    "MCPClient",
]
