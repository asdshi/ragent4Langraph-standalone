"""RAG 检索策略消融实验脚本。

对比不同检索配置在同一测试集上的表现：
  - dense_only   : 纯向量检索 (Chroma/ANN)
  - sparse_only  : 纯关键词检索 (BM25)
  - hybrid       : Dense + Sparse + RRF (默认配置)
  - hybrid_rerank: Hybrid + Cross-Encoder Rerank

用法：
    python scripts/run_ablation.py --strategies dense_only sparse_only hybrid hybrid_rerank

输出：
    reports/ablation_retrieval_{timestamp}.json
    reports/ablation_retrieval_{timestamp}.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.core.settings import load_settings, resolve_path
from src.core.query_engine.hybrid_search import HybridSearch, HybridSearchConfig
from src.core.query_engine.query_processor import QueryProcessor
from src.core.query_engine.dense_retriever import create_dense_retriever
from src.core.query_engine.sparse_retriever import create_sparse_retriever
from src.core.query_engine.fusion import RRFFusion
from src.core.query_engine.reranker import create_core_reranker, CoreReranker, RerankConfig
from src.ingestion.storage.bm25_indexer import BM25Indexer
from src.libs.embedding.embedding_factory import EmbeddingFactory
from src.libs.vector_store.vector_store_factory import VectorStoreFactory

from src.observability.evaluation.eval_runner import EvalRunner, load_test_set
from src.observability.evaluation.ragas_evaluator import RagasEvaluator
from src.libs.evaluator.custom_evaluator import CustomEvaluator


REPORTS_DIR = Path("reports")


@dataclass
class StrategyResult:
    strategy: str
    report: dict
    avg_latency_ms: float
    config_notes: str = ""


def build_hybrid_search(settings, strategy: str, collection: str):
    """根据策略构建不同的 HybridSearch 实例。"""
    embedding_client = EmbeddingFactory.create(settings)
    vector_store = VectorStoreFactory.create(settings, collection_name=collection)

    dense_retriever = create_dense_retriever(
        settings=settings,
        embedding_client=embedding_client,
        vector_store=vector_store,
    )

    bm25_indexer = BM25Indexer(index_dir=str(resolve_path(f"data/db/bm25/{collection}")))
    sparse_retriever = create_sparse_retriever(
        settings=settings,
        bm25_indexer=bm25_indexer,
        vector_store=vector_store,
    )
    sparse_retriever.default_collection = collection

    query_processor = QueryProcessor()
    fusion = RRFFusion(k=getattr(settings.retrieval, "rrf_k", 60))

    if strategy == "dense_only":
        config = HybridSearchConfig(
            dense_top_k=10,
            sparse_top_k=0,
            fusion_top_k=10,
            enable_dense=True,
            enable_sparse=False,
        )
    elif strategy == "sparse_only":
        config = HybridSearchConfig(
            dense_top_k=0,
            sparse_top_k=10,
            fusion_top_k=10,
            enable_dense=False,
            enable_sparse=True,
        )
    elif strategy == "hybrid":
        config = HybridSearchConfig(
            dense_top_k=10,
            sparse_top_k=10,
            fusion_top_k=10,
            enable_dense=True,
            enable_sparse=True,
        )
    elif strategy == "hybrid_rerank":
        config = HybridSearchConfig(
            dense_top_k=20,
            sparse_top_k=20,
            fusion_top_k=20,
            enable_dense=True,
            enable_sparse=True,
        )
    else:
        raise ValueError(f"Unknown strategy: {strategy}")

    return HybridSearch(
        settings=settings,
        query_processor=query_processor,
        dense_retriever=dense_retriever,
        sparse_retriever=sparse_retriever,
        fusion=fusion,
        config=config,
    )


async def run_strategy(settings, strategy: str, test_set_path: str, collection: str, limit: int | None = None) -> StrategyResult:
    """运行单个策略的评估。"""
    print(f"\n{'='*60}")
    print(f"Running strategy: {strategy}")
    print(f"{'='*60}")

    hybrid = build_hybrid_search(settings, strategy, collection)

    # evaluator: 只用 custom evaluator（hit_rate / MRR）因为 retrieval-only 没有 generated answer
    custom_evaluator = CustomEvaluator(settings=settings, metrics=["hit_rate", "mrr"])

    # 如果有 reference_answer，也可用 ragas 的 context_precision（需要 answer）
    # 这里简单拼接 contexts 作为 answer placeholder
    def placeholder_answer_generator(query, chunks):
        texts = []
        for c in chunks:
            if isinstance(c, dict):
                texts.append(c.get("text", str(c)))
            elif hasattr(c, "text"):
                texts.append(str(c.text))
            else:
                texts.append(str(c))
        return "\n".join(texts[:3])

    # reranker（仅 hybrid_rerank 策略启用）
    reranker = None
    if strategy == "hybrid_rerank":
        reranker = CoreReranker(
            settings=settings,
            config=RerankConfig(enabled=True, top_k=10),
        )

    runner = EvalRunner(
        settings=settings,
        hybrid_search=hybrid,
        evaluator=custom_evaluator,
        answer_generator=placeholder_answer_generator,
        reranker=reranker,
    )

    report = runner.run(test_set_path, top_k=10, collection=collection, limit=limit)

    avg_latency = report.total_elapsed_ms / max(len(report.query_results), 1)

    config_notes = {
        "dense_only": "仅启用 Dense 检索 (Chroma ANN)",
        "sparse_only": "仅启用 Sparse 检索 (BM25)",
        "hybrid": "Dense + Sparse + RRF 融合",
        "hybrid_rerank": "Hybrid + Cross-Encoder Rerank (top-20 -> top-10)",
    }.get(strategy, "")

    # 计算额外的无 ground_truth 指标
    query_count = len(report.query_results)
    result_coverage = sum(1 for qr in report.query_results if qr.retrieved_chunk_ids) / max(query_count, 1)
    avg_result_count = sum(len(qr.retrieved_chunk_ids) for qr in report.query_results) / max(query_count, 1)

    # 把额外指标注入 report dict
    report_dict = report.to_dict()
    report_dict["aggregate_metrics"]["result_coverage"] = result_coverage
    report_dict["aggregate_metrics"]["avg_result_count"] = avg_result_count

    return StrategyResult(
        strategy=strategy,
        report=report_dict,
        avg_latency_ms=avg_latency,
        config_notes=config_notes,
    )


async def main():
    parser = argparse.ArgumentParser(description="RAG Retrieval Ablation Study")
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=["dense_only", "sparse_only", "hybrid", "hybrid_rerank"],
        help="List of strategies to evaluate",
    )
    parser.add_argument(
        "--test-set",
        default="tests/fixtures/golden_test_set_v2.json",
        help="Path to golden test set",
    )
    parser.add_argument(
        "--collection",
        default="default",
        help="Target Chroma collection name",
    )
    parser.add_argument(
        "--output-dir",
        default="reports",
        help="Output directory",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit evaluation to first N test cases",
    )
    args = parser.parse_args()

    settings = load_settings()
    print(f"[Settings] Loaded from config. Collection: {args.collection}")

    results: List[StrategyResult] = []
    for strategy in args.strategies:
        try:
            result = await run_strategy(settings, strategy, args.test_set, args.collection, limit=args.limit)
            results.append(result)
        except Exception as exc:
            print(f"[Error] Strategy {strategy} failed: {exc}")

    if not results:
        print("No successful results. Exiting.")
        return

    # 生成报告
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = int(time.time())

    # JSON
    json_path = output_dir / f"ablation_retrieval_{timestamp}.json"
    json_path.write_text(
        json.dumps(
            {
                "timestamp": timestamp,
                "collection": args.collection,
                "test_set": args.test_set,
                "results": [
                    {
                        "strategy": r.strategy,
                        "config_notes": r.config_notes,
                        "avg_latency_ms": round(r.avg_latency_ms, 2),
                        "aggregate_metrics": r.report.get("aggregate_metrics", {}),
                    }
                    for r in results
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n[Report] JSON saved to: {json_path}")

    # Markdown
    md_lines = [
        f"# RAG 检索策略消融实验报告 ({time.strftime('%Y-%m-%d %H:%M:%S')})",
        "",
        f"- **测试集**: {args.test_set}",
        f"- **Collection**: {args.collection}",
        "",
        "## 聚合指标对比",
        "",
        "| 策略 | Avg Latency (ms) | Hit Rate | MRR | 说明 |",
        "|:---|:---:|:---:|:---:|:---|",
    ]

    for r in results:
        metrics = r.report.get("aggregate_metrics", {})
        hit_rate = metrics.get("hit_rate", 0.0)
        mrr = metrics.get("mrr", 0.0)
        coverage = metrics.get("result_coverage", 0.0)
        avg_res = metrics.get("avg_result_count", 0.0)
        md_lines.append(
            f"| {r.strategy} | {r.avg_latency_ms:.1f} | {hit_rate:.4f} | {mrr:.4f} | {coverage:.2f} | {avg_res:.2f} | {r.config_notes} |"
        )

    md_lines.extend(["", "## 结论与建议", ""])
    best_coverage = max(results, key=lambda r: r.report.get("aggregate_metrics", {}).get("result_coverage", 0.0))
    best_latency = min(results, key=lambda r: r.avg_latency_ms)
    md_lines.append(f"- **结果覆盖最优**: `{best_coverage.strategy}` (Result Coverage = {best_coverage.report.get('aggregate_metrics', {}).get('result_coverage', 0.0):.2f})")
    md_lines.append(f"- **延迟最低**: `{best_latency.strategy}` (Avg Latency = {best_latency.avg_latency_ms:.1f} ms)")
    md_lines.append("- 实际生产环境中，若对延迟敏感且文档关键词特征明显，可优先考虑 BM25-only；若语义泛化要求高，推荐 Hybrid 或 Hybrid+Rerank。")

    md_path = output_dir / f"ablation_retrieval_{timestamp}.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"[Report] Markdown saved to: {md_path}")

    # 控制台摘要
    print("\n" + "=" * 60)
    print("ABLATION SUMMARY")
    print("=" * 60)
    for r in results:
        metrics = r.report.get("aggregate_metrics", {})
        print(f"  {r.strategy:20s} | latency={r.avg_latency_ms:8.1f}ms | hit_rate={metrics.get('hit_rate', 0):.4f} | mrr={metrics.get('mrr', 0):.4f} | coverage={metrics.get('result_coverage', 0):.2f} | avg_results={metrics.get('avg_result_count', 0):.2f}")
    print("=" * 60)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
