"""
简易 MCP Server — 纯 Python 标准库实现，无需外部依赖。

提供工具：
- calculator: 数学计算
- get_current_time: 获取当前时间
- list_directory: 列出目录内容
- web_search: 简易网页搜索（使用 DuckDuckGo HTML API，无需 API key）

启动方式：
    python scripts/simple_mcp_server.py

或作为 MCP Server 通过 stdio 启动：
    python scripts/simple_mcp_server.py --stdio
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# MCP SDK
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server


# =============================================================================
# Tool Handlers
# =============================================================================

async def calculator_handler(expression: str) -> types.CallToolResult:
    """安全计算数学表达式。"""
    try:
        # 白名单：只允许数学运算
        allowed_names = {
            k: v for k, v in math.__dict__.items()
            if not k.startswith('_')
        }
        allowed_names.update({
            "abs": abs, "max": max, "min": min, "sum": sum,
            "len": len, "round": round,
            "math": math,  # 允许通过 math.xxx 调用
        })
        
        # 使用 eval 但限制全局/局部变量
        result = eval(expression, {"__builtins__": {}}, allowed_names)
        
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Result: {result}")],
            isError=False,
        )
    except Exception as e:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Error: {e}")],
            isError=True,
        )


async def get_current_time_handler(timezone: str = "UTC") -> types.CallToolResult:
    """获取当前时间。"""
    now = datetime.now()
    text = f"Current time ({timezone}): {now.strftime('%Y-%m-%d %H:%M:%S')}"
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        isError=False,
    )


async def list_directory_handler(path: str = ".") -> types.CallToolResult:
    """列出目录内容。"""
    try:
        target = Path(path).resolve()
        if not target.exists():
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Path not found: {path}")],
                isError=True,
            )
        
        items: List[str] = []
        for item in target.iterdir():
            prefix = "[DIR]" if item.is_dir() else "[FILE]"
            size = ""
            if item.is_file():
                size = f" ({item.stat().st_size} bytes)"
            items.append(f"{prefix} {item.name}{size}")
        
        text = f"Contents of {target}:\n" + "\n".join(items)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            isError=False,
        )
    except Exception as e:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Error: {e}")],
            isError=True,
        )


# Tavily API Key（生产环境建议通过环境变量 TAVILY_API_KEY 注入）
TAVILY_API_KEY = os.environ.get(
    "TAVILY_API_KEY",
    "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",
)
TAVILY_API_URL = "https://api.tavily.com/search"


async def web_search_handler(query: str, max_results: int = 5) -> types.CallToolResult:
    """网页搜索 — 使用 Tavily 实时搜索引擎 API。
    
    相比 DuckDuckGo，Tavily 提供更稳定的响应速度、结构化结果和 AI 生成摘要。
    """
    import urllib.request
    import urllib.error

    payload = json.dumps({
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "basic",
        "max_results": min(max_results, 10),
        "include_answer": True,
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        TAVILY_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:500]
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Tavily API 错误 ({e.code}): {body}")],
            isError=True,
        )
    except Exception as e:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Tavily 请求失败: {e}")],
            isError=True,
        )

    # 解析响应
    results = data.get("results", [])
    answer = data.get("answer", "")

    if not results and not answer:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"未找到关于「{query}」的搜索结果。")],
            isError=False,
        )

    lines = [f"Tavily 搜索「{query}」的结果：\n"]

    # 如果有 AI 生成的摘要，放在最前面
    if answer:
        lines.append("【AI 摘要】")
        lines.append(answer.strip())
        lines.append("")

    for i, r in enumerate(results[:max_results], 1):
        title = r.get("title", "无标题")
        url = r.get("url", "")
        content = r.get("content", "")
        score = r.get("score", 0)
        lines.append(f"{i}. {title}")
        if url:
            lines.append(f"   链接: {url}")
        if content:
            # 截断过长的正文
            snippet = content[:300] + "..." if len(content) > 300 else content
            lines.append(f"   摘要: {snippet}")
        if score:
            lines.append(f"   相关度: {score:.2f}")
        lines.append("")

    return types.CallToolResult(
        content=[types.TextContent(type="text", text="\n".join(lines))],
        isError=False,
    )


# =============================================================================
# Tool Definitions
# =============================================================================

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "calculator",
        "description": "Evaluate mathematical expressions safely. Supports: +, -, *, /, **, math functions (sin, cos, log, sqrt, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Mathematical expression to evaluate, e.g. '2 + 2 * 3' or 'math.sqrt(16)'",
                },
            },
            "required": ["expression"],
        },
        "handler": calculator_handler,
    },
    {
        "name": "get_current_time",
        "description": "Get the current date and time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "Timezone name (default: UTC)",
                    "default": "UTC",
                },
            },
            "required": [],
        },
        "handler": get_current_time_handler,
    },
    {
        "name": "list_directory",
        "description": "List files and directories in a given path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path to list (default: current directory)",
                    "default": ".",
                },
            },
            "required": [],
        },
        "handler": list_directory_handler,
    },
    {
        "name": "web_search",
        "description": "Search the web using Tavily real-time search engine. Returns structured results with title, URL, snippet, relevance score and an AI-generated answer summary. Fast and reliable.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (default: 5, max: 10)",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["query"],
        },
        "handler": web_search_handler,
    },
]


# =============================================================================
# MCP Server
# =============================================================================

async def run_stdio_server() -> int:
    """Run MCP server with stdio transport."""
    server = Server("simple-mcp-server")
    
    @server.list_tools()
    async def list_tools() -> List[types.Tool]:
        return [
            types.Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["input_schema"],
            )
            for t in TOOLS
        ]
    
    @server.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any]) -> List[types.TextContent]:
        for tool in TOOLS:
            if tool["name"] == name:
                result = await tool["handler"](**arguments)
                return result.content
        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
    
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
    
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Simple MCP Server")
    parser.add_argument("--stdio", action="store_true", help="Run with stdio transport (for MCP)")
    args = parser.parse_args()
    
    if args.stdio:
        import asyncio
        return asyncio.run(run_stdio_server())
    else:
        # 直接运行模式：打印工具列表
        print("Simple MCP Server")
        print("=" * 40)
        print("\nAvailable tools:")
        for tool in TOOLS:
            print(f"  - {tool['name']}: {tool['description'][:60]}...")
        print("\nRun with --stdio for MCP mode.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
