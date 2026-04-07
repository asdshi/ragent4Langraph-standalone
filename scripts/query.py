#!/usr/bin/env python
"""查询命令行脚本。

功能：
- 使用 HybridSearch（Dense + Sparse + RRF）执行检索。
- 支持可选 rerank。
- 支持 verbose 输出中间结果，便于调参与排障。

退出码：
- 0: 成功。
- 1: 查询失败。
- 2: 配置或初始化失败。
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# 将仓库根目录加入搜索路径，保证脚本可独立运行。
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_REPO_ROOT))

# Windows 终端统一 UTF-8 输出，避免中文乱码。
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 再次兜底注入项目根目录，兼容不同调用方式。
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.core.settings import load_settings
from src.core.query_engine.query_processor import QueryProcessor
from src.core.query_engine.hybrid_search import create_hybrid_search
from src.core.query_engine.dense_retriever import create_dense_retriever
from src.core.query_engine.sparse_retriever import create_sparse_retriever
from src.core.query_engine.reranker import create_core_reranker
from src.core.trace import TraceContext, TraceCollector
from src.ingestion.storage.bm25_indexer import BM25Indexer
from src.libs.embedding.embedding_factory import EmbeddingFactory
from src.libs.vector_store.vector_store_factory import VectorStoreFactory
from src.observability.logger import get_logger

logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="Query documents from the Modular RAG knowledge hub.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--query", "-q",
        required=True,
        help="Query string."
    )

    parser.add_argument(
        "--collection", "-c",
        default="default",
        help="Collection name (default: 'default')"
    )

    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Max number of results (default: 10)"
    )

    parser.add_argument(
        "--config",
        default=str(_REPO_ROOT / "config" / "settings.yaml"),
        help="Path to configuration file (default: config/settings.yaml)"
    )

    parser.add_argument(
        "--no-rerank",
        action="store_true",
        help="Disable reranking even if enabled in settings"
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print intermediate results (dense/sparse/fusion/rerank)"
    )

    return parser.parse_args()


def _format_filters(filters: Dict[str, Any]) -> str:
    if not filters:
        return "(none)"
    return ", ".join(f"{k}={v}" for k, v in filters.items())


def _print_results(results: List[Any], top_k: int, title: str = "RESULTS") -> None:
    """以可读形式打印检索结果列表。"""
    print("\n" + "=" * 60)
    print(f"{title} (top_k={top_k}, returned={len(results)})")
    print("=" * 60)

    for idx, result in enumerate(results, start=1):
        metadata = result.metadata or {}
        source_path = metadata.get("source_path", "")
        chunk_index = metadata.get("chunk_index", "")
        page_num = metadata.get("page_num", "")
        snippet = (result.text or "").replace("\n", " ")[:200]

        print(f"#{idx:02d}  score={result.score:.4f}  id={result.chunk_id}")
        print(f"     source_path={source_path}")
        if chunk_index != "":
            print(f"     chunk_index={chunk_index}")
        if page_num != "":
            print(f"     page_num={page_num}")
        print(f"     text={snippet}...")

    print("=" * 60)


def _build_components(settings, collection: str):
    """按 collection 构建检索链路组件。"""
    vector_store = VectorStoreFactory.create(
        settings,
        collection_name=collection,
    )

    embedding_client = EmbeddingFactory.create(settings)
    dense_retriever = create_dense_retriever(
        settings=settings,
        embedding_client=embedding_client,
        vector_store=vector_store,
    )

    bm25_indexer = BM25Indexer(index_dir=f"data/db/bm25/{collection}")
    sparse_retriever = create_sparse_retriever(
        settings=settings,
        bm25_indexer=bm25_indexer,
        vector_store=vector_store,
    )
    # 显式绑定集合，避免误查默认 BM25 索引。
    sparse_retriever.default_collection = collection

    query_processor = QueryProcessor()
    hybrid_search = create_hybrid_search(
        settings=settings,
        query_processor=query_processor,
        dense_retriever=dense_retriever,
        sparse_retriever=sparse_retriever,
    )

    reranker = create_core_reranker(settings=settings)

    return hybrid_search, reranker


def _run_query(
    hybrid_search,
    reranker,
    query: str,
    top_k: Optional[int],
    use_rerank: bool,
    verbose: bool,
) -> int:
    """执行单次查询并打印结果，返回脚本退出码。"""
    trace = TraceContext(trace_type="query")
    trace.metadata["query"] = query[:200]
    trace.metadata["top_k"] = top_k

    try:
        hybrid_result = hybrid_search.search(
            query=query,
            top_k=top_k,
            filters=None,
            trace=trace,
            return_details=verbose,
        )
    except Exception as e:
        print(f"[FAIL] Hybrid search failed: {e}")
        TraceCollector().collect(trace)
        return 1

    if verbose:
        results = hybrid_result.results
        if hybrid_result.used_fallback:
            print(
                f"[WARN] HybridSearch fallback used. "
                f"dense_error={hybrid_result.dense_error}, "
                f"sparse_error={hybrid_result.sparse_error}"
            )
        if hybrid_result.processed_query:
            print(
                f"[INFO] ProcessedQuery keywords={hybrid_result.processed_query.keywords} "
                f"filters={_format_filters(hybrid_result.processed_query.filters)}"
            )
        _print_results(hybrid_result.dense_results or [], top_k=top_k, title="DENSE RESULTS")
        _print_results(hybrid_result.sparse_results or [], top_k=top_k, title="SPARSE RESULTS")
        _print_results(hybrid_result.results, top_k=top_k, title="FUSION RESULTS")
    else:
        results = hybrid_result

    effective_top_k = top_k if top_k is not None else len(results)

    if not results:
        print("[INFO] 未找到相关文档，请先运行 ingest.py 摄取数据。")
        return 0

    # 可选重排：在融合结果基础上进一步优化排序。
    if use_rerank and reranker.is_enabled:
        try:
            rerank_result = reranker.rerank(query=query, results=results, top_k=top_k, trace=trace)
            results = rerank_result.results
            if verbose and rerank_result.used_fallback:
                print(
                    f"[WARN] Rerank fallback used: {rerank_result.fallback_reason} "
                    f"(reranker={rerank_result.reranker_type})"
                )
            if verbose:
                _print_results(results, top_k=top_k, title="RERANK RESULTS")
        except Exception as e:
            print(f"[WARN] Reranking failed: {e}. Using original order.")
    elif verbose and not reranker.is_enabled:
        print("[INFO] Reranking disabled by settings.")

    _print_results(results, top_k=effective_top_k)
    TraceCollector().collect(trace)
    return 0


def main() -> int:
    """脚本主入口。"""
    args = parse_args()

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

    print("[*] Modular RAG Query Script")
    print("=" * 60)
    print(f"Collection: {args.collection}")

    try:
        hybrid_search, reranker = _build_components(settings, args.collection)
    except Exception as e:
        print(f"[FAIL] Failed to initialize query components: {e}")
        logger.exception("Query initialization failed")
        return 2

    use_rerank = not args.no_rerank

    # 2) 单轮查询模式
    return _run_query(
        hybrid_search=hybrid_search,
        reranker=reranker,
        query=args.query,
        top_k=args.top_k,
        use_rerank=use_rerank,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    sys.exit(main())
