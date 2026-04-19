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


async def web_search_handler(query: str, max_results: int = 5) -> types.CallToolResult:
    """简易网页搜索 — 使用 DuckDuckGo Lite HTML（无需 API key）。"""
    try:
        import urllib.request
        import urllib.parse
        import re
        
        # 使用 DuckDuckGo HTML 版本
        url = "https://html.duckduckgo.com/html/"
        data = urllib.parse.urlencode({"q": query}).encode("utf-8")
        
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://duckduckgo.com/",
            },
        )
        
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        
        # 提取搜索结果
        results: List[Dict[str, str]] = []
        
        # DuckDuckGo HTML 结果格式：.result 包含 .result__a (标题+链接) 和 .result__snippet (摘要)
        result_blocks = re.findall(
            r'<div class="result[^"]*"[^>]*>.*?<h2 class="result__title">.*?</h2>.*?</div>\s*</div>',
            html, re.S
        )
        
        if not result_blocks:
            # fallback：用更宽松的模式
            result_blocks = re.findall(
                r'<div class="web-result[^"]*"[^>]*>.*?</div>\s*</div>',
                html, re.S
            )
        
        for block in result_blocks[:max_results]:
            # 提取标题和链接
            title_match = re.search(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
            # 提取摘要
            snippet_match = re.search(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
            
            if not title_match:
                continue
            
            href = title_match.group(1)
            title = re.sub(r'<[^>]+>', '', title_match.group(2))
            snippet = re.sub(r'<[^>]+>', '', snippet_match.group(1)) if snippet_match else ""
            
            # 处理 DuckDuckGo 重定向链接
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = "https://duckduckgo.com" + href
            
            results.append({
                "title": title.strip(),
                "url": href,
                "snippet": snippet[:200] + "..." if len(snippet) > 200 else snippet,
            })
        
        if not results:
            # 可能是 DuckDuckGo 返回了 CAPTCHA 或空页面
            return types.CallToolResult(
                content=[types.TextContent(
                    type="text",
                    text=f"未能获取搜索结果（DuckDuckGo 可能要求验证）。\n\n建议：直接访问搜索引擎查询「{query}」"
                )],
                isError=False,
            )
        
        lines = [f"搜索「{query}」的结果：\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}")
            lines.append(f"   链接: {r['url']}")
            if r['snippet']:
                lines.append(f"   摘要: {r['snippet']}")
            lines.append("")
        
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="\n".join(lines))],
            isError=False,
        )
        
    except Exception as e:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"搜索失败: {e}\n\n建议：直接访问搜索引擎查询「{query}」")],
            isError=True,
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
        "description": "Search the web using DuckDuckGo. Returns top results with title, URL, and snippet. No API key required.",
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
