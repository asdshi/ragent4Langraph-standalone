"""
MCP Client — 封装 Model Context Protocol 客户端。

支持两种传输层：
- stdio: 启动子进程，通过 stdin/stdout 通信
- sse: 通过 HTTP SSE 连接远程 Server

Usage:
    >>> client = MCPClient()
    >>> await client.connect_stdio(command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"])
    >>> tools = await client.list_tools()
    >>> result = await client.call_tool("read_file", {"path": "/tmp/test.txt"})
    >>> await client.disconnect()
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional

from mcp import ClientSession, types as mcp_types
from mcp.client.stdio import stdio_client, StdioServerParameters

logger = logging.getLogger(__name__)


class MCPClient:
    """MCP 协议客户端封装。
    
    封装连接管理、工具发现、工具调用，提供简洁的 async API。
    
    Attributes:
        session: 底层的 ClientSession（连接成功后可用）
        server_name: 连接的服务器标识（用于日志）
        transport: "stdio" | "sse" | None（未连接）
    """
    
    def __init__(self, server_name: str = "unknown") -> None:
        self.server_name = server_name
        self.transport: Optional[str] = None
        
        # 内部状态
        self._session: Optional[ClientSession] = None
        self._exit_stack = AsyncExitStack()
        self._stdio_params: Optional[StdioServerParameters] = None
    
    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------
    
    async def connect_stdio(
        self,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> None:
        """通过 stdio 传输连接 MCP Server。
        
        Args:
            command: 可执行命令（如 "npx", "python", "uvx"）
            args: 命令参数列表
            env: 额外环境变量
            cwd: 工作目录
            
        Raises:
            RuntimeError: 连接失败
        """
        self._stdio_params = StdioServerParameters(
            command=command,
            args=args or [],
            env=env,
            cwd=cwd,
        )
        
        logger.info(
            f"[{self.server_name}] Connecting via stdio: {command} {' '.join(args or [])}"
        )
        
        try:
            # stdio_client 是 async context manager
            stdio_transport = await self._exit_stack.enter_async_context(
                stdio_client(self._stdio_params)
            )
            read_stream, write_stream = stdio_transport
            
            # 创建 session
            session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            
            # 初始化握手
            await session.initialize()
            self._session = session
            self.transport = "stdio"
            
            logger.info(f"[{self.server_name}] Connected via stdio.")
        except Exception as e:
            await self._exit_stack.aclose()
            raise RuntimeError(
                f"[{self.server_name}] Failed to connect via stdio: {e}"
            ) from e
    
    async def connect_sse(self, url: str) -> None:
        """通过 SSE 传输连接 MCP Server。
        
        Args:
            url: SSE endpoint URL（如 http://localhost:3000/sse）
            
        Raises:
            RuntimeError: 连接失败
            NotImplementedError: 当前环境缺少 sse_client
        """
        try:
            from mcp.client.sse import sse_client
        except ImportError as e:
            raise NotImplementedError(
                "SSE transport requires mcp.client.sse. "
                "Ensure mcp package is installed with SSE support."
            ) from e
        
        logger.info(f"[{self.server_name}] Connecting via SSE: {url}")
        
        try:
            sse_transport = await self._exit_stack.enter_async_context(
                sse_client(url)
            )
            read_stream, write_stream = sse_transport
            
            session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            
            await session.initialize()
            self._session = session
            self.transport = "sse"
            
            logger.info(f"[{self.server_name}] Connected via SSE.")
        except Exception as e:
            await self._exit_stack.aclose()
            raise RuntimeError(
                f"[{self.server_name}] Failed to connect via SSE: {e}"
            ) from e
    
    async def disconnect(self) -> None:
        """断开连接并清理资源。"""
        logger.info(f"[{self.server_name}] Disconnecting...")
        await self._exit_stack.aclose()
        self._session = None
        self.transport = None
        self._exit_stack = AsyncExitStack()
    
    def _ensure_session(self) -> ClientSession:
        """确保 session 已连接。"""
        if self._session is None:
            raise RuntimeError(
                f"[{self.server_name}] Not connected. Call connect_stdio() or connect_sse() first."
            )
        return self._session
    
    # ------------------------------------------------------------------
    # 工具发现
    # ------------------------------------------------------------------
    
    async def list_tools(self) -> List[Dict[str, Any]]:
        """获取 Server 注册的所有工具列表。
        
        Returns:
            每个元素格式：{"name": str, "description": str, "inputSchema": dict}
        """
        session = self._ensure_session()
        
        try:
            result = await session.list_tools()
            
            tools: List[Dict[str, Any]] = []
            for tool in result.tools:
                tools.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "inputSchema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
                })
            
            logger.info(
                f"[{self.server_name}] Discovered {len(tools)} tools: "
                f"{[t['name'] for t in tools]}"
            )
            return tools
        except Exception as e:
            logger.error(f"[{self.server_name}] list_tools failed: {e}")
            raise
    
    # ------------------------------------------------------------------
    # 工具调用
    # ------------------------------------------------------------------
    
    async def call_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
    ) -> mcp_types.CallToolResult:
        """调用指定工具。
        
        Args:
            name: 工具名
            arguments: 工具参数
            
        Returns:
            MCP CallToolResult
        """
        session = self._ensure_session()
        
        logger.debug(
            f"[{self.server_name}] Calling tool '{name}' with args: {arguments}"
        )
        
        try:
            result = await session.call_tool(name, arguments=arguments)
            
            if result.isError:
                logger.warning(
                    f"[{self.server_name}] Tool '{name}' returned error: {result.content}"
                )
            else:
                logger.debug(
                    f"[{self.server_name}] Tool '{name}' succeeded."
                )
            
            return result
        except Exception as e:
            logger.error(f"[{self.server_name}] call_tool '{name}' failed: {e}")
            raise
    
    # ------------------------------------------------------------------
    # 其他 MCP 能力（可选）
    # ------------------------------------------------------------------
    
    async def list_resources(self) -> List[Dict[str, Any]]:
        """列出 Server 提供的资源（如果支持）。"""
        session = self._ensure_session()
        try:
            result = await session.list_resources()
            return [
                {"uri": r.uri, "name": r.name, "mimeType": r.mimeType}
                for r in result.resources
            ]
        except Exception as e:
            logger.warning(f"[{self.server_name}] list_resources not supported: {e}")
            return []
    
    async def read_resource(self, uri: str) -> str:
        """读取资源内容（如果支持）。"""
        session = self._ensure_session()
        try:
            result = await session.read_resource(uri)
            # 简单提取文本内容
            contents = result.contents
            texts = []
            for c in contents:
                text = getattr(c, "text", None)
                if text:
                    texts.append(text)
            return "\n".join(texts)
        except Exception as e:
            logger.warning(f"[{self.server_name}] read_resource failed: {e}")
            raise
    
    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    
    @property
    def is_connected(self) -> bool:
        """是否已连接。"""
        return self._session is not None
    
    def __repr__(self) -> str:
        return f"MCPClient(server_name={self.server_name!r}, transport={self.transport!r}, connected={self.is_connected})"
