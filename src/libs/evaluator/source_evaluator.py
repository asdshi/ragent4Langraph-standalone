"""Source-aware evaluator using document-level ground truth.

Evaluates retrieval quality by checking whether retrieved chunks come from
the expected source documents (expected_sources in test set), avoiding
circular dependency on chunk_ids.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.libs.evaluator.base_evaluator import BaseEvaluator


class SourceEvaluator(BaseEvaluator):
    """Evaluator based on expected source document matching.

    Metrics:
    - source_hit_rate: binary, 1 if any retrieved chunk is from expected_sources
    - source_mrr: reciprocal rank of first correct-source chunk
    - source_coverage: fraction of expected_sources found in retrieved chunks
    """

    SUPPORTED_METRICS = {"source_hit_rate", "source_mrr", "source_coverage"}

    def __init__(
        self,
        settings: Any = None,
        metrics: Optional[Sequence[str]] = None,
        **kwargs: Any,
    ) -> None:
        self.settings = settings
        self.kwargs = kwargs

        if metrics is None:
            metrics = ["source_hit_rate", "source_mrr"]

        normalized = [str(m).strip().lower() for m in metrics]
        unsupported = [m for m in normalized if m not in self.SUPPORTED_METRICS]
        if unsupported:
            raise ValueError(
                f"Unsupported metrics: {', '.join(unsupported)}. "
                f"Supported: {', '.join(sorted(self.SUPPORTED_METRICS))}"
            )
        self.metrics = normalized

    def evaluate(
        self,
        query: str,
        retrieved_chunks: List[Any],
        generated_answer: Optional[str] = None,
        ground_truth: Optional[Any] = None,
        trace: Optional[Any] = None,
        **kwargs: Any,
    ) -> Dict[str, float]:
        self.validate_query(query)

        expected_sources = self._extract_expected_sources(ground_truth)
        if not expected_sources:
            return {m: 0.0 for m in self.metrics}

        # Extract source_path from each retrieved chunk
        retrieved_sources = []
        for chunk in retrieved_chunks:
            source = self._extract_source(chunk)
            retrieved_sources.append(source)

        results: Dict[str, float] = {}

        if "source_hit_rate" in self.metrics:
            results["source_hit_rate"] = self._compute_hit_rate(
                retrieved_sources, expected_sources
            )

        if "source_mrr" in self.metrics:
            results["source_mrr"] = self._compute_mrr(
                retrieved_sources, expected_sources
            )

        if "source_coverage" in self.metrics:
            results["source_coverage"] = self._compute_coverage(
                retrieved_sources, expected_sources
            )

        return results

    def _extract_expected_sources(self, ground_truth: Optional[Any]) -> List[str]:
        if ground_truth is None:
            return []
        if isinstance(ground_truth, dict):
            sources = ground_truth.get("expected_sources", [])
            if isinstance(sources, list):
                return [str(s) for s in sources if s]
        return []

    def _extract_source(self, chunk: Any) -> Optional[str]:
        if isinstance(chunk, dict):
            return chunk.get("source_path") or chunk.get("source") or chunk.get("metadata", {}).get("source_path")
        if hasattr(chunk, "metadata"):
            meta = getattr(chunk, "metadata", {}) or {}
            if isinstance(meta, dict):
                return meta.get("source_path") or meta.get("source")
        return None

    def _compute_hit_rate(self, retrieved: List[Optional[str]], expected: List[str]) -> float:
        return 1.0 if any(r in expected for r in retrieved if r) else 0.0

    def _compute_mrr(self, retrieved: List[Optional[str]], expected: List[str]) -> float:
        for rank, source in enumerate(retrieved, start=1):
            if source and source in expected:
                return 1.0 / rank
        return 0.0

    def _compute_coverage(self, retrieved: List[Optional[str]], expected: List[str]) -> float:
        if not expected:
            return 0.0
        found = set(r for r in retrieved if r) & set(expected)
        return len(found) / len(expected)
