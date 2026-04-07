"""Modular RAG MCP Server 主入口。

该文件负责应用启动最小闭环：
1. 读取并校验配置文件。
2. 初始化日志系统。
3. 输出启动阶段信息并返回进程退出码。

说明：
- 本入口当前主要用于配置验证与启动占位。
- MCP 的 stdio 服务器入口在 `src/mcp_server/server.py` 中。
"""

import sys
from pathlib import Path

from src.core.settings import SettingsError, load_settings
from src.observability.logger import get_logger


def main() -> int:
    """程序主函数。

    返回值约定：
    - 0: 启动前检查通过（配置可用）。
    - 非 0: 启动前检查失败（如配置缺失或格式错误）。
    """
    print("Modular RAG MCP Server - Starting...")

    settings_path = Path("config/settings.yaml")
    try:
        settings = load_settings(settings_path)
    except SettingsError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    logger = get_logger(log_level=settings.observability.log_level)
    logger.info("Settings loaded successfully.")
    logger.info("MCP Server will be implemented in Phase E.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
