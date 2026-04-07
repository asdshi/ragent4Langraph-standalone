"""MCP Server 启动入口（基于官方 Python MCP SDK）。

该模块使用 stdio 作为传输层：
- stdout 仅允许输出 JSON-RPC 协议消息。
- 所有日志统一重定向到 stderr，避免污染协议流。

此外，本模块在启动阶段预加载重量级依赖，降低线程场景下的导入锁竞争风险。
"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

from src.mcp_server.protocol_handler import create_mcp_server
from src.observability.logger import get_logger

if TYPE_CHECKING:
    pass


SERVER_NAME = "modular-rag-mcp-server"
SERVER_VERSION = "0.1.0"


def _redirect_all_loggers_to_stderr() -> None:
    """将根日志处理器重定向到 stderr。

    原因：MCP stdio 协议把 stdout 作为通信信道，
    一旦日志写入 stdout，会直接破坏 JSON-RPC 报文边界。
    """
    import logging as _logging

    root = _logging.getLogger()
    stderr_handler = _logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(
        _logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    # Replace any existing stream handlers that might point to stdout
    for handler in root.handlers[:]:
        if isinstance(handler, _logging.StreamHandler) and not isinstance(
            handler, _logging.FileHandler
        ):
            root.removeHandler(handler)
    root.addHandler(stderr_handler)


def _preload_heavy_imports() -> None:
        """在主线程中预加载重量级依赖。

        背景：
        - MCP SDK 运行后会由 anyio 管理后台线程处理 stdio I/O。
        - 工具处理器里若再通过 `asyncio.to_thread()` 触发首次重型 import，
            可能与 I/O 线程争用 Python 全局 import 锁，极端情况下造成卡死。

        方案：
        - 在服务正式进入 I/O 线程前，先在主线程完成重型模块导入。
        - 后续线程中的 import 直接命中 `sys.modules`，避免阻塞。
        """
    # chromadb is the heaviest culprit (onnxruntime, numpy, …)
    try:
        import chromadb  # noqa: F401
        import chromadb.config  # noqa: F401
    except ImportError:
        pass  # optional at install time

    # Internal modules that tools lazy-import inside asyncio.to_thread
    try:
        import src.core.query_engine.query_processor  # noqa: F401
        import src.core.query_engine.hybrid_search  # noqa: F401
        import src.core.query_engine.dense_retriever  # noqa: F401
        import src.core.query_engine.sparse_retriever  # noqa: F401
        import src.core.query_engine.reranker  # noqa: F401
        import src.ingestion.storage.bm25_indexer  # noqa: F401
        import src.libs.embedding.embedding_factory  # noqa: F401
        import src.libs.vector_store.vector_store_factory  # noqa: F401
    except ImportError:
        pass


async def run_stdio_server_async() -> int:
    """以异步方式运行 MCP stdio 服务并返回退出码。"""
    # Import here to avoid import errors if mcp not installed
    import mcp.server.stdio

    # Ensure ALL logging goes to stderr (stdout is reserved for JSON-RPC)
    _redirect_all_loggers_to_stderr()

    # Pre-load heavy deps in main thread to prevent import-lock deadlocks
    # when tool handlers later call asyncio.to_thread().
    _preload_heavy_imports()

    logger = get_logger(log_level="INFO")
    logger.info("Starting MCP server (stdio transport) with official SDK.")

    # Create server with protocol handler
    server = create_mcp_server(SERVER_NAME, SERVER_VERSION)

    # Run with stdio transport
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )

    logger.info("MCP server shutting down.")
    return 0


def run_stdio_server() -> int:
    """同步包装器：用于非异步调用场景。"""
    return asyncio.run(run_stdio_server_async())


def main() -> int:
    """进程入口。"""
    return run_stdio_server()


if __name__ == "__main__":
    sys.exit(main())