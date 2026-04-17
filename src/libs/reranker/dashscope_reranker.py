"""DashScope (阿里云百炼) Reranker implementation.

This module implements reranking using the DashScope API, supporting
models such as qwen3-rerank and gte-rerank-v2.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from src.libs.reranker.base_reranker import BaseReranker

logger = logging.getLogger(__name__)


class DashScopeRerankError(RuntimeError):
    """Raised when DashScope reranking fails."""


class DashScopeReranker(BaseReranker):
    """DashScope API-based reranker for cloud reranking.

    Uses the DashScope compatible rerank API endpoint:
        POST https://dashscope.aliyuncs.com/compatible-api/v1/reranks

    Supports text-only rerank models (e.g., qwen3-rerank).
    """

    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-api/v1/reranks"

    def __init__(
        self,
        settings: Any,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
        **kwargs: Any,
    ) -> None:
        """Initialize the DashScope Reranker.

        Args:
            settings: Application settings.
            api_key: DashScope API key. If None, reads from OPENAI_API_KEY
                or DASHSCOPE_API_KEY env var.
            model: Model name (e.g., "qwen3-rerank"). If None, reads from
                settings.rerank.model.
            base_url: API endpoint override.
            timeout: HTTP request timeout in seconds.
            **kwargs: Additional parameters.
        """
        self.settings = settings
        self.timeout = timeout
        self.kwargs = kwargs

        # API key
        self.api_key = api_key or self._resolve_api_key()

        # Model name
        self.model = model or self._resolve_model_name(settings)

        # Base URL
        self.base_url = base_url or self.DEFAULT_BASE_URL

    def _resolve_api_key(self) -> str:
        """Resolve API key from environment."""
        key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not key:
            raise DashScopeRerankError(
                "DashScope API key not found. Set DASHSCOPE_API_KEY or OPENAI_API_KEY environment variable."
            )
        return key

    def _resolve_model_name(self, settings: Any) -> str:
        """Extract model name from settings."""
        try:
            model_name = settings.rerank.model
            if not model_name or not isinstance(model_name, str):
                raise ValueError("Model name must be a non-empty string")
            return model_name
        except AttributeError as e:
            raise AttributeError(
                "Missing configuration: settings.rerank.model. "
                "Please specify 'rerank.model' in settings.yaml"
            ) from e

    def rerank(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        trace: Optional[Any] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """Rerank candidates using DashScope API.

        Args:
            query: The user query string.
            candidates: List of candidate records. Each must contain 'text' or 'content'.
            trace: Optional TraceContext for observability.
            **kwargs: Additional parameters (top_k, etc.).

        Returns:
            Reranked list of candidates ordered by relevance score (descending).
            Each candidate includes a 'rerank_score' field.

        Raises:
            ValueError: If query or candidates are invalid.
            DashScopeRerankError: If API call fails.
        """
        self.validate_query(query)
        self.validate_candidates(candidates)

        if len(candidates) == 1:
            return candidates

        top_k = kwargs.get("top_k", len(candidates))

        # Extract document texts
        documents = []
        for candidate in candidates:
            text = candidate.get("text") or candidate.get("content", "")
            if not isinstance(text, str):
                text = str(text)
            documents.append(text)

        # Call API
        response_data = self._call_api(query, documents, top_k)

        # Map results back to candidates
        reranked = self._map_results(response_data, candidates, top_k)

        if trace:
            logger.debug(
                "DashScope rerank: query='%s...', input=%d, output=%d",
                query[:50],
                len(candidates),
                len(reranked),
            )

        return reranked

    def _call_api(self, query: str, documents: List[str], top_n: int) -> Dict[str, Any]:
        """Call DashScope rerank API.

        Args:
            query: Query string.
            documents: List of document texts.
            top_n: Number of top results to return.

        Returns:
            Parsed JSON response.

        Raises:
            DashScopeRerankError: If the API call fails.
        """
        payload = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": min(top_n, len(documents)),
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            import httpx
        except ImportError:
            raise DashScopeRerankError(
                "httpx is required for DashScope reranking. "
                "Install it with: pip install httpx"
            )

        # Retry with exponential backoff for transient network errors
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = httpx.post(
                    self.base_url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                data = response.json()
                break
            except httpx.HTTPStatusError as e:
                raise DashScopeRerankError(
                    f"DashScope API returned {e.response.status_code}: {e.response.text}"
                ) from e
            except (httpx.NetworkError, httpx.RemoteProtocolError) as e:
                if attempt < max_retries - 1:
                    import time
                    wait = 2 ** attempt
                    logger.warning(f"DashScope API transient error (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                raise DashScopeRerankError(f"DashScope API request failed after {max_retries} retries: {e}") from e
            except Exception as e:
                raise DashScopeRerankError(f"DashScope API request failed: {e}") from e

        return data

    def _map_results(
        self,
        response_data: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """Map API response results back to original candidates.

        Args:
            response_data: Parsed JSON from DashScope API.
            candidates: Original candidate list.
            top_k: Limit output size.

        Returns:
            Reranked candidates with 'rerank_score' field.
        """
        results = response_data.get("results", [])
        if not results:
            logger.warning("DashScope rerank returned empty results")
            return []

        reranked = []
        for result in results[:top_k]:
            index = result.get("index")
            score = result.get("relevance_score", 0.0)

            if index is None or not (0 <= index < len(candidates)):
                logger.warning("Invalid index in rerank result: %s", result)
                continue

            candidate_copy = candidates[index].copy()
            candidate_copy["rerank_score"] = float(score)
            reranked.append(candidate_copy)

        # Sort by score descending (API usually returns in order, but be safe)
        reranked.sort(key=lambda x: x.get("rerank_score", 0.0), reverse=True)

        return reranked
