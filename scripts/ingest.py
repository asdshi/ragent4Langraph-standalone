#!/usr/bin/env python
"""文档摄取命令行脚本。

功能：
- 支持单文件或目录批量摄取。
- 支持强制重跑（忽略历史记录）。
- 支持 dry-run 仅预览待处理文件。

退出码：
- 0: 全部成功。
- 1: 部分失败。
- 2: 全部失败或配置错误。
"""

import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional

# 将仓库根目录加入模块搜索路径，确保直接运行脚本时也能导入 `src.*`。
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_REPO_ROOT))

# Windows 控制台默认编码可能导致中文乱码，这里强制为 UTF-8。
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 兼容不同运行入口，再次兜底注入项目根目录。
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.core.settings import load_settings, Settings
from src.core.trace import TraceContext, TraceCollector
from src.ingestion.pipeline import IngestionPipeline, PipelineResult
from src.observability.logger import get_logger

logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="Ingest documents into the Modular RAG knowledge hub.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument(
        "--path", "-p",
        required=True,
        help="Path to file or directory to ingest. "
             "If directory, processes all PDF files recursively."
    )
    
    parser.add_argument(
        "--collection", "-c",
        default="default",
        help="Collection name for organizing documents (default: 'default')"
    )
    
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Force re-processing even if file was previously ingested"
    )
    
    parser.add_argument(
        "--config",
        default=str(_REPO_ROOT / "config" / "settings.yaml"),
        help="Path to configuration file (default: config/settings.yaml)"
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List files that would be processed without actually processing"
    )
    
    return parser.parse_args()


def discover_files(path: str, extensions: List[str] = None) -> List[Path]:
    """从路径发现待处理文件。

当路径是目录时，会递归搜索目标后缀（默认 PDF）。
    """
    if extensions is None:
        extensions = ['.pdf']
    
    path = Path(path)
    
    if not path.exists():
        raise FileNotFoundError(f"Path does not exist: {path}")
    
    if path.is_file():
        if path.suffix.lower() in extensions:
            return [path]
        else:
            raise ValueError(f"Unsupported file type: {path.suffix}. Supported: {extensions}")
    
    # 目录模式：递归收集同后缀（同时兼容大小写后缀）。
    files = []
    for ext in extensions:
        files.extend(path.rglob(f"*{ext}"))
        files.extend(path.rglob(f"*{ext.upper()}"))
    
    # 去重并排序，确保处理顺序稳定，便于复现。
    files = sorted(set(files))
    
    return files


def print_summary(results: List[PipelineResult], verbose: bool = False) -> None:
    """打印摄取结果汇总。"""
    total = len(results)
    successful = sum(1 for r in results if r.success)
    failed = total - successful
    
    total_chunks = sum(r.chunk_count for r in results if r.success)
    total_images = sum(r.image_count for r in results if r.success)
    
    print("\n" + "=" * 60)
    print("INGESTION SUMMARY")
    print("=" * 60)
    print(f"Total files processed: {total}")
    print(f"  [OK] Successful: {successful}")
    print(f"  [FAIL] Failed: {failed}")
    print(f"\nTotal chunks generated: {total_chunks}")
    print(f"Total images processed: {total_images}")
    
    if verbose and failed > 0:
        print("\nFailed files:")
        for r in results:
            if not r.success:
                print(f"  [FAIL] {r.file_path}: {r.error}")
    
    if verbose and successful > 0:
        print("\nSuccessful files:")
        for r in results:
            if r.success:
                skipped = r.stages.get("integrity", {}).get("skipped", False)
                status = "[SKIP] skipped" if skipped else f"[OK] {r.chunk_count} chunks"
                print(f"  {status}: {r.file_path}")
    
    print("=" * 60)


def main() -> int:
    """脚本主入口。"""
    args = parse_args()
    
    # 按需提升日志级别，便于排查问题。
    if args.verbose:
        import logging
        logging.getLogger().setLevel(logging.DEBUG)
    
    print("[*] Modular RAG Ingestion Script")
    print("=" * 60)
    
    # 1) 加载配置
    try:
        config_path = Path(args.config)
        if not config_path.exists():
            print(f"[FAIL] Configuration file not found: {config_path}")
            return 2
        
        settings = load_settings(str(config_path))
        print(f"[OK] Configuration loaded from: {config_path}")
    except Exception as e:
        print(f"[FAIL] Failed to load configuration: {e}")
        return 2
    
    # 2) 发现输入文件
    try:
        files = discover_files(args.path)
        print(f"[INFO] Found {len(files)} file(s) to process")
        
        if len(files) == 0:
            print("[WARN] No files found to process")
            return 0
        
        for f in files:
            print(f"   - {f}")
    except FileNotFoundError as e:
        print(f"[FAIL] {e}")
        return 2
    except ValueError as e:
        print(f"[FAIL] {e}")
        return 2
    
    # 3) dry-run 仅展示，不执行实际摄取
    if args.dry_run:
        print("\n[INFO] Dry run mode - no files were processed")
        return 0
    
    # 4) 初始化流水线组件
    print(f"\n[INFO] Initializing pipeline...")
    print(f"   Collection: {args.collection}")
    print(f"   Force: {args.force}")
    
    try:
        pipeline = IngestionPipeline(
            settings=settings,
            collection=args.collection,
            force=args.force
        )
    except Exception as e:
        print(f"[FAIL] Failed to initialize pipeline: {e}")
        logger.exception("Pipeline initialization failed")
        return 2
    
    # 5) 逐文件执行摄取
    print(f"\n[INFO] Processing files...")
    results: List[PipelineResult] = []
    
    collector = TraceCollector()

    for i, file_path in enumerate(files, 1):
        print(f"\n[{i}/{len(files)}] Processing: {file_path}")
        
        try:
            trace = TraceContext(trace_type="ingestion")
            trace.metadata["source_path"] = str(file_path)
            result = pipeline.run(str(file_path), trace=trace)
            collector.collect(trace)
            results.append(result)
            
            if result.success:
                skipped = result.stages.get("integrity", {}).get("skipped", False)
                if skipped:
                    print(f"   [SKIP] Skipped (already processed)")
                else:
                    print(f"   [OK] Success: {result.chunk_count} chunks, {result.image_count} images")
            else:
                print(f"   [FAIL] Failed: {result.error}")
        
        except Exception as e:
            logger.exception(f"Unexpected error processing {file_path}")
            results.append(PipelineResult(
                success=False,
                file_path=str(file_path),
                error=str(e)
            ))
            print(f"   [FAIL] Error: {e}")
    
    # 6) 输出汇总
    print_summary(results, args.verbose)
    
    # 7) 根据成功率计算退出码
    successful = sum(1 for r in results if r.success)
    if successful == len(results):
        return 0  # All successful
    elif successful > 0:
        return 1  # Partial failure
    else:
        return 2  # Complete failure


if __name__ == "__main__":
    sys.exit(main())
