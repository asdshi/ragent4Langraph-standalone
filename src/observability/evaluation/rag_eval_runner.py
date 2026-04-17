"""端到端 RAG 评估运行器。

与 EvalRunner 不同，RAGEvalRunner 会调用完整的 RAGWorkflow（包含
LangGraph 的 query rewrite、intent detect、memory management、generation），
然后使用 Ragas 对最终输出进行质量评估。

设计原则：
- 端到端：评估的是完整系统，而非仅检索模块
- 可观测：输出结构化报告，支持按 tag 分类统计
- 可对比：支持与 reference answer 的语义相似度计算
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.observability.evaluation.eval_runner import (
    EvalReport,
    GoldenTestCase,
    QueryResult,
    load_test_set,
)
from src.ragent_backend.workflow import RAGWorkflow

logger = logging.getLogger(__name__)


class RAGEvalRunner:
    """端到端 RAG 评估编排器。

    Example::

        runner = RAGEvalRunner(
            workflow=rag_workflow,
            ragas_evaluator=RagasEvaluator(settings),
        )
        report = await runner.run("tests/fixtures/golden_test_set_v2.json")
        print(report.aggregate_metrics)
    """

    def __init__(
        self,
        workflow: RAGWorkflow,
        ragas_evaluator: Any,
        custom_evaluator: Any = None,
    ) -> None:
        """Initialize RAGEvalRunner.

        Args:
            workflow: RAGWorkflow instance for end-to-end execution.
            ragas_evaluator: Evaluator for LLM-as-Judge metrics (e.g. Ragas).
            custom_evaluator: Optional evaluator for IR metrics (e.g. hit_rate/MRR).
        """
        self.workflow = workflow
        self.ragas_evaluator = ragas_evaluator
        self.custom_evaluator = custom_evaluator

    async def run(
        self,
        test_set_path: str | Path,
        top_k: int = 10,
    ) -> EvalReport:
        """Run end-to-end evaluation on the golden test set.

        Args:
            test_set_path: Path to golden_test_set_v2.json.
            top_k: Number of chunks to retrieve per query.

        Returns:
            EvalReport with per-query and aggregate metrics.
        """
        test_cases = load_test_set(test_set_path)
        if not test_cases:
            raise ValueError("Golden test set is empty.")

        logger.info(
            "Starting end-to-end RAG evaluation: %d test cases",
            len(test_cases),
        )

        report = EvalReport(
            evaluator_name=type(self.ragas_evaluator).__name__,
            test_set_path=str(test_set_path),
        )

        t0 = time.monotonic()

        for idx, tc in enumerate(test_cases):
            logger.info("Evaluating [%d/%d]: %s", idx + 1, len(test_cases), tc.query[:60])
            qr = await self._evaluate_single(tc, top_k=top_k)
            report.query_results.append(qr)

        report.total_elapsed_ms = (time.monotonic() - t0) * 1000.0
        report.aggregate_metrics = self._aggregate_metrics(report.query_results)

        logger.info(
            "Evaluation complete: %d queries, aggregate=%s",
            len(report.query_results),
            report.aggregate_metrics,
        )

        return report

    async def _evaluate_single(
        self,
        test_case: GoldenTestCase,
        top_k: int = 10,
    ) -> QueryResult:
        """Evaluate a single test case end-to-end."""
        t0 = time.monotonic()
        qr = QueryResult(query=test_case.query)

        # Step 1: Run full RAGWorkflow
        thread_id = f"eval_{uuid.uuid4().hex[:8]}"
        initial_state = {
            "query": test_case.query,
            "conversation_id": thread_id,
            "task_id": f"eval_task_{uuid.uuid4().hex[:8]}",
            "top_k": top_k,
            # 如有 history，可注入（当前版本预留）
        }

        try:
            final_state = await self.workflow.run(initial_state, thread_id=thread_id)
        except Exception as exc:
            logger.warning("Workflow failed for '%s': %s", test_case.query[:40], exc)
            qr.elapsed_ms = (time.monotonic() - t0) * 1000.0
            return qr

        # Step 2: Extract answer and retrieved contexts
        answer = final_state.get("final_answer", "")
        contexts = final_state.get("retrieval_contexts", [])
        if not contexts and final_state.get("retrieval_context"):
            contexts = [final_state["retrieval_context"]]

        qr.generated_answer = answer
        # 尝试从上下文中提取 chunk ids（当前以字符串形式存储，id 提取为 best effort）
        qr.retrieved_chunk_ids = self._extract_chunk_ids(contexts)

        # Step 3: Ragas evaluation
        metrics: Dict[str, float] = {}
        if answer and self.ragas_evaluator is not None:
            try:
                ragas_metrics = self.ragas_evaluator.evaluate(
                    query=test_case.query,
                    retrieved_chunks=contexts,
                    generated_answer=answer,
                )
                metrics.update(ragas_metrics)
            except Exception as exc:
                logger.warning("Ragas evaluation failed for '%s': %s", test_case.query[:40], exc)

        # Step 4: Custom evaluation (IR metrics)
        if self.custom_evaluator is not None:
            try:
                ground_truth = (
                    {"ids": test_case.expected_chunk_ids}
                    if test_case.expected_chunk_ids
                    else None
                )
                custom_metrics = self.custom_evaluator.evaluate(
                    query=test_case.query,
                    retrieved_chunks=contexts,
                    generated_answer=answer,
                    ground_truth=ground_truth,
                )
                metrics.update(custom_metrics)
            except Exception as exc:
                logger.warning("Custom evaluation failed for '%s': %s", test_case.query[:40], exc)

        # Step 5: Reference answer similarity (if available)
        if test_case.reference_answer and answer:
            try:
                sim = await self._calc_similarity(answer, test_case.reference_answer)
                metrics["reference_similarity"] = sim
            except Exception as exc:
                logger.debug("Similarity calc failed: %s", exc)

        qr.metrics = metrics
        qr.elapsed_ms = (time.monotonic() - t0) * 1000.0
        return qr

    @staticmethod
    def _extract_chunk_ids(contexts: List[Any]) -> List[str]:
        """Best-effort extraction of chunk ids from retrieval contexts."""
        ids = []
        for ctx in contexts:
            if isinstance(ctx, dict):
                for key in ("chunk_id", "id"):
                    if key in ctx:
                        ids.append(str(ctx[key]))
                        break
            elif hasattr(ctx, "chunk_id"):
                ids.append(str(getattr(ctx, "chunk_id")))
        return ids

    async def _calc_similarity(self, answer: str, reference: str) -> float:
        """Calculate simple embedding cosine similarity between answer and reference.

        Falls back to a naive token overlap ratio if embedding is unavailable.
        """
        try:
            from src.libs.embedding.embedding_factory import EmbeddingFactory
            from src.core.settings import load_settings

            settings = load_settings()
            emb = EmbeddingFactory.create(settings)
            vec_a = emb.embed_query(answer)
            vec_b = emb.embed_query(reference)

            import math
            dot = sum(x * y for x, y in zip(vec_a, vec_b))
            norm_a = math.sqrt(sum(x * x for x in vec_a))
            norm_b = math.sqrt(sum(x * x for x in vec_b))
            if norm_a == 0 or norm_b == 0:
                return 0.0
            return dot / (norm_a * norm_b)
        except Exception:
            # Fallback: token overlap ratio
            set_a = set(answer.lower().split())
            set_b = set(reference.lower().split())
            if not set_a or not set_b:
                return 0.0
            intersection = len(set_a & set_b)
            return intersection / max(len(set_a), len(set_b))

    @staticmethod
    def _aggregate_metrics(results: List[QueryResult]) -> Dict[str, float]:
        """Compute average metrics across all query results."""
        if not results:
            return {}

        all_keys: set[str] = set()
        for qr in results:
            all_keys.update(qr.metrics.keys())

        averages: Dict[str, float] = {}
        for key in sorted(all_keys):
            values = [qr.metrics[key] for qr in results if key in qr.metrics]
            averages[key] = sum(values) / len(values) if values else 0.0

        return averages


def analyze_by_tags(report: EvalReport, test_cases: List[GoldenTestCase]) -> Dict[str, Dict[str, float]]:
    """Aggregate metrics grouped by test case tags.

    Args:
        report: EvalReport from RAGEvalRunner.run()
        test_cases: Original golden test cases (must align with report.query_results).

    Returns:
        Mapping tag -> {metric_name: average_value}
    """
    tag_metrics: Dict[str, List[Dict[str, float]]] = {}

    for qr, tc in zip(report.query_results, test_cases):
        for tag in getattr(tc, "tags", []):
            tag_metrics.setdefault(tag, []).append(qr.metrics)

    result: Dict[str, Dict[str, float]] = {}
    for tag, metrics_list in tag_metrics.items():
        all_keys = set()
        for m in metrics_list:
            all_keys.update(m.keys())
        result[tag] = {}
        for key in sorted(all_keys):
            values = [m[key] for m in metrics_list if key in m]
            result[tag][key] = sum(values) / len(values) if values else 0.0

    return result
