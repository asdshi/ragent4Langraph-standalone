"""混合检索引擎：将稠密检索、稀疏检索与 RRF 融合组合起来。

这个模块负责实现 HybridSearch，核心职责是把多个检索通道串成一条
稳定、可观测、可降级的查询链路。整体流程如下：
1. QueryProcessor：对原始查询做预处理，提取关键词与过滤条件。
2. DenseRetriever：基于向量语义相似度进行稠密检索。
3. SparseRetriever：基于 BM25 的关键词检索。
4. RRFFusion：通过 Reciprocal Rank Fusion 对多路结果进行融合排序。

设计原则：
- 优雅降级：任意一条检索路径失败时，尽量使用其余可用结果继续返回。
- 可插拔：所有核心组件都通过构造函数注入，便于测试和替换实现。
- 可观测：与 TraceContext 集成，便于排查问题和分析耗时。
- 配置驱动：top_k 等检索参数统一从配置中读取，避免硬编码。
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from src.core.types import ProcessedQuery, RetrievalResult

if TYPE_CHECKING:
    from src.core.query_engine.dense_retriever import DenseRetriever
    from src.core.query_engine.fusion import RRFFusion
    from src.core.query_engine.query_processor import QueryProcessor
    from src.core.query_engine.sparse_retriever import SparseRetriever
    from src.core.settings import Settings

logger = logging.getLogger(__name__)


def _snapshot_results(
    results: Optional[List[RetrievalResult]],
) -> List[Dict[str, Any]]:
    """将检索结果转换为可序列化快照，供 trace 记录和调试使用。

    Args:
        results: RetrievalResult 对象列表。

    Returns:
        包含 chunk_id、score、text、source 的字典列表。
    """
    if not results:
        return []
    return [
        {
            "chunk_id": r.chunk_id,
            "score": round(r.score, 4),
            "text": r.text or "",
            "source": r.metadata.get("source_path", r.metadata.get("source", "")),
        }
        for r in results
    ]


@dataclass
class HybridSearchConfig:
    """HybridSearch 的运行配置。
    
    Attributes:
        dense_top_k: 稠密检索阶段返回的结果数量上限。
        sparse_top_k: 稀疏检索阶段返回的结果数量上限。
        fusion_top_k: 融合之后最终保留的结果数量上限。
        enable_dense: 是否启用稠密检索。
        enable_sparse: 是否启用稀疏检索。
        parallel_retrieval: 是否并行执行多路检索。
        metadata_filter_post: 是否在融合后再补做一次元数据过滤。
    """
    dense_top_k: int = 20
    sparse_top_k: int = 20
    fusion_top_k: int = 10
    enable_dense: bool = True
    enable_sparse: bool = True
    parallel_retrieval: bool = True
    metadata_filter_post: bool = True


@dataclass
class HybridSearchResult:
    """混合检索的完整返回结果。
    
    Attributes:
        results: 最终排序后的结果列表。
        dense_results: 稠密检索的原始结果，便于排查问题。
        sparse_results: 稀疏检索的原始结果，便于排查问题。
        dense_error: 稠密检索失败时的错误信息。
        sparse_error: 稀疏检索失败时的错误信息。
        used_fallback: 是否启用了降级路径。
        processed_query: 预处理后的查询对象，便于调试。
    """
    results: List[RetrievalResult] = field(default_factory=list)
    dense_results: Optional[List[RetrievalResult]] = None
    sparse_results: Optional[List[RetrievalResult]] = None
    dense_error: Optional[str] = None
    sparse_error: Optional[str] = None
    used_fallback: bool = False
    processed_query: Optional[ProcessedQuery] = None


class HybridSearch:
    """把稠密检索和稀疏检索组合起来的混合搜索引擎。
    
    这个类负责编排完整的混合检索流程：
    1. 查询预处理：从原始 query 中提取关键词和过滤条件。
    2. 并行检索：同时触发稠密检索和稀疏检索，减少总体等待时间。
    3. 结果融合：通过 RRF 算法把不同通道的结果合并为统一排序。
    4. 后置过滤：如有需要，在融合后再按元数据条件做一次补充过滤。
    
    设计原则：
    - 优雅降级：某一路失败时，尽量保留其他通道的有效结果。
    - 可插拔：所有组件都可通过依赖注入替换。
    - 可观测：支持 TraceContext，便于定位慢点和失败点。
    - 配置驱动：参数尽量从 settings 读取，减少散落常量。
    
    Example:
        >>> # 初始化各个组件
        >>> query_processor = QueryProcessor()
        >>> dense_retriever = DenseRetriever(settings, embedding_client, vector_store)
        >>> sparse_retriever = SparseRetriever(settings, bm25_indexer, vector_store)
        >>> fusion = RRFFusion(k=60)
        >>>
        >>> # 创建混合检索器
        >>> hybrid = HybridSearch(
        ...     settings=settings,
        ...     query_processor=query_processor,
        ...     dense_retriever=dense_retriever,
        ...     sparse_retriever=sparse_retriever,
        ...     fusion=fusion
        ... )
        >>>
        >>> # 发起搜索
        >>> results = hybrid.search("如何配置 Azure OpenAI？", top_k=10)
    """
    
    def __init__(
        self,
        settings: Optional[Settings] = None,
        query_processor: Optional[QueryProcessor] = None,
        dense_retriever: Optional[DenseRetriever] = None,
        sparse_retriever: Optional[SparseRetriever] = None,
        fusion: Optional[RRFFusion] = None,
        config: Optional[HybridSearchConfig] = None,
    ) -> None:
        """初始化混合搜索引擎及其依赖组件。
        
        Args:
            settings: 用于提取检索配置的应用设置对象。
            query_processor: 用于查询预处理的 QueryProcessor。
            dense_retriever: 用于语义检索的 DenseRetriever。
            sparse_retriever: 用于关键词检索的 SparseRetriever。
            fusion: 用于融合多路结果的 RRFFusion。
            config: 可选的 HybridSearchConfig；未提供时会尝试从 settings 提取。
        
        Note:
            至少需要提供 dense_retriever 或 sparse_retriever 其中之一，
            搜索才能真正工作；如果某一路不可用或发生异常，会尽量降级返回。
        """
        self.query_processor = query_processor
        self.dense_retriever = dense_retriever
        self.sparse_retriever = sparse_retriever
        self.fusion = fusion
        
        # 优先使用外部显式传入的 config；否则从 settings 中提取；再不行就使用默认值。
        self.config = config or self._extract_config(settings)
        
        logger.info(
            f"HybridSearch initialized: dense={self.dense_retriever is not None}, "
            f"sparse={self.sparse_retriever is not None}, "
            f"config={self.config}"
        )
    
    def _extract_config(self, settings: Optional[Settings]) -> HybridSearchConfig:
        """从 Settings 中提取 HybridSearchConfig。
        
        Args:
            settings: 应用设置对象。
            
        Returns:
            从 settings 读取或回退到默认值的 HybridSearchConfig。
        """
        if settings is None:
            return HybridSearchConfig()
        
        retrieval_config = getattr(settings, 'retrieval', None)
        if retrieval_config is None:
            return HybridSearchConfig()
        
        return HybridSearchConfig(
            dense_top_k=getattr(retrieval_config, 'dense_top_k', 20),
            sparse_top_k=getattr(retrieval_config, 'sparse_top_k', 20),
            fusion_top_k=getattr(retrieval_config, 'fusion_top_k', 10),
            enable_dense=True,
            enable_sparse=True,
            parallel_retrieval=True,
            metadata_filter_post=True,
        )
    
    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
        trace: Optional[Any] = None,
        return_details: bool = False,
    ) -> List[RetrievalResult] | HybridSearchResult:
        """执行混合检索，组合稠密检索和稀疏检索的结果。
        
        Args:
            query: 搜索查询字符串。
            top_k: 最多返回的结果数量；如果为 None，则使用 config.fusion_top_k。
            filters: 可选的元数据过滤条件，例如 {"collection": "docs"}。
            trace: 可选的 TraceContext，用于可观测性记录。
            return_details: 为 True 时返回带调试信息的 HybridSearchResult。
        
        Returns:
            return_details=False 时返回按相关度排序的 RetrievalResult 列表。
            return_details=True 时返回包含完整调试信息的 HybridSearchResult。
        
        Raises:
            ValueError: 当 query 为空或只有空白字符时抛出。
            RuntimeError: 当两条检索路径都失败或都不可用时抛出。
        
        Example:
            >>> results = hybrid.search("Azure configuration", top_k=5)
            >>> for r in results:
            ...     print(f"[{r.score:.4f}] {r.chunk_id}: {r.text[:50]}...")
        """
        # 先做最基础的输入校验，避免后续检索组件处理空字符串。
        if not query or not query.strip():
            raise ValueError("Query cannot be empty or whitespace-only")
        
        effective_top_k = top_k if top_k is not None else self.config.fusion_top_k
        
        logger.debug(f"HybridSearch: query='{query[:50]}...', top_k={effective_top_k}")
        
        # 第 1 步：对原始 query 进行预处理，提取后续检索需要的结构化信息。
        # 
        _t0 = time.monotonic()
        processed_query = self._process_query(query)
        _elapsed = (time.monotonic() - _t0) * 1000.0
        if trace is not None:
            trace.record_stage("query_processing", {
                "method": "query_processor",
                "original_query": query,
                "keywords": processed_query.keywords,
            }, elapsed_ms=_elapsed)
        
        # 将显式传入的 filters 与 query 中解析出的 filters 合并。
        # 外部显式参数优先，避免用户手动指定的条件被查询解析结果覆盖。
        merged_filters = self._merge_filters(processed_query.filters, filters)
        
        # 第 2 步：执行多路检索。
        dense_results, sparse_results, dense_error, sparse_error = self._run_retrievals(
            processed_query=processed_query,
            filters=merged_filters,
            trace=trace,
        )
        
        # 第 3 步：处理各种降级场景。
        # 只要还有一路可用，就尽量避免直接失败。
        used_fallback = False
        if dense_error and sparse_error:
            # 两条检索链路都失败，已经没有可用结果，因此直接抛错。
            raise RuntimeError(
                f"Both retrieval paths failed. "
                f"Dense error: {dense_error}. Sparse error: {sparse_error}"
            )
        elif dense_error:
            # 稠密检索失败时，退化为仅使用稀疏检索结果。
            logger.warning(f"Dense retrieval failed, using sparse only: {dense_error}")
            used_fallback = True
            fused_results = sparse_results or []
        elif sparse_error:
            # 稀疏检索失败时，退化为仅使用稠密检索结果。
            logger.warning(f"Sparse retrieval failed, using dense only: {sparse_error}")
            used_fallback = True
            fused_results = dense_results or []
        elif not dense_results and not sparse_results:
            # 两路都成功了，但都没有召回到任何结果。
            fused_results = []
        else:
            # 第 4 步：把两路召回结果融合成一个统一排序。
            # rrf算法,score[chunk1]=sum(1/k+rank4chunk) ps:每个切快的
            fused_results = self._fuse_results(
                dense_results=dense_results or [],
                sparse_results=sparse_results or [],
                top_k=effective_top_k,
                trace=trace,
            )
        
        # 第 5 步：在融合后补做元数据过滤。
        # 这是兜底逻辑，适合底层存储对过滤语法支持不完整的情况。
        if merged_filters and self.config.metadata_filter_post:
            fused_results = self._apply_metadata_filters(fused_results, merged_filters)
        
        # 第 6 步：最后再统一截断到 top_k，确保返回数量符合接口约定。
        final_results = fused_results[:effective_top_k]
        
        logger.debug(f"HybridSearch: returning {len(final_results)} results")
        
        if return_details:
            return HybridSearchResult(
                results=final_results,
                dense_results=dense_results,
                sparse_results=sparse_results,
                dense_error=dense_error,
                sparse_error=sparse_error,
                used_fallback=used_fallback,
                processed_query=processed_query,
            )
        
        return final_results
    
    def _process_query(self, query: str) -> ProcessedQuery:
        """使用 QueryProcessor 处理原始查询。
        
        Args:
            query: 原始查询字符串。
            
        Returns:
            包含关键词和过滤条件的 ProcessedQuery。
        """
        if self.query_processor is None:
            # 没有配置 QueryProcessor 时，退回到最朴素的空白分词策略。
            # 这种方式不够智能，但能保证检索流程不中断。
            logger.warning("No QueryProcessor configured, using basic tokenization")
            keywords = query.split()
            return ProcessedQuery(
                original_query=query,
                keywords=keywords,
                filters={},
            )
        
        return self.query_processor.process(query)
    
    def _merge_filters(
        self,
        query_filters: Dict[str, Any],
        explicit_filters: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """合并从查询中解析出的过滤条件与外部显式传入的过滤条件。

        显式 filters 的优先级更高，原因是调用方通常比自动解析更明确。
        
        Args:
            query_filters: QueryProcessor 从 query 中提取的过滤条件。
            explicit_filters: 调用 search() 时显式传入的过滤条件。
            
        Returns:
            合并后的过滤条件字典。
        """
        merged = query_filters.copy() if query_filters else {}
        if explicit_filters:
            merged.update(explicit_filters)
        return merged
    
    def _run_retrievals(
        self,
        processed_query: ProcessedQuery,
        filters: Optional[Dict[str, Any]],
        trace: Optional[Any],
    ) -> Tuple[
        Optional[List[RetrievalResult]],
        Optional[List[RetrievalResult]],
        Optional[str],
        Optional[str],
    ]:
        """执行稠密检索和稀疏检索。

        如果配置允许且两路都需要执行，则优先并行；否则退回到顺序执行。
        
        Args:
            processed_query: 已预处理过、包含关键词的查询对象。
            filters: 要应用的合并后过滤条件。
            trace: 可选的 TraceContext。
            
        Returns:
            返回 (dense_results, sparse_results, dense_error, sparse_error)。
        """
        dense_results: Optional[List[RetrievalResult]] = None
        sparse_results: Optional[List[RetrievalResult]] = None
        dense_error: Optional[str] = None
        sparse_error: Optional[str] = None
        
        # 先判断当前这次搜索到底需要跑哪些通道。
        run_dense = (
            self.config.enable_dense 
            and self.dense_retriever is not None
        )
        run_sparse = (
            self.config.enable_sparse 
            and self.sparse_retriever is not None
            and processed_query.keywords  # 稀疏检索依赖关键词，因此这里必须先确保关键词列表非空。
        )
        
        if not run_dense and not run_sparse:
            # 两条通道都不需要执行时，直接返回配置错误或空结果状态。
            if self.dense_retriever is None and self.sparse_retriever is None:
                dense_error = "No retriever configured"
                sparse_error = "No retriever configured"
            return dense_results, sparse_results, dense_error, sparse_error
        
        if self.config.parallel_retrieval and run_dense and run_sparse:
            # 两路都要跑且允许并行时，尽量利用并发减少等待时间。
            dense_results, sparse_results, dense_error, sparse_error = (
                self._run_parallel_retrievals(processed_query, filters, trace)
            )
        else:
            # 否则按顺序执行，适合单通道或调试场景。
            if run_dense:
                dense_results, dense_error = self._run_dense_retrieval(
                    processed_query.original_query, filters, trace
                )
            
            if run_sparse:
                sparse_results, sparse_error = self._run_sparse_retrieval(
                    processed_query.keywords, filters, trace
                )
        
        return dense_results, sparse_results, dense_error, sparse_error
    
    def _run_parallel_retrievals(
        self,
        processed_query: ProcessedQuery,
        filters: Optional[Dict[str, Any]],
        trace: Optional[Any],
    ) -> Tuple[
        Optional[List[RetrievalResult]],
        Optional[List[RetrievalResult]],
        Optional[str],
        Optional[str],
    ]:
        """使用 ThreadPoolExecutor 并行执行稠密检索与稀疏检索。
        
        Args:
            processed_query: 已预处理的查询对象。
            filters: 要应用的过滤条件。
            trace: 可选的 TraceContext。
            
        Returns:
            返回 (dense_results, sparse_results, dense_error, sparse_error)。
        """
        dense_results: Optional[List[RetrievalResult]] = None
        sparse_results: Optional[List[RetrievalResult]] = None
        dense_error: Optional[str] = None
        sparse_error: Optional[str] = None
        
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {}
            
            # 提交稠密检索任务。
            futures['dense'] = executor.submit(
                self._run_dense_retrieval,
                # 向量检索用original_query
                processed_query.original_query,
                filters,
                trace,
            )
            
            # 提交稀疏检索任务。
            futures['sparse'] = executor.submit(
                self._run_sparse_retrieval,
                processed_query.keywords,
                filters,
                trace,
            )
            
            # 收集两个 future 的结果，并分别保存错误信息。
            for name, future in futures.items():
                try:
                    results, error = future.result(timeout=30)
                    if name == 'dense':
                        dense_results = results
                        dense_error = error
                    else:
                        sparse_results = results
                        sparse_error = error
                except Exception as e:
                    error_msg = f"{name} retrieval failed with exception: {e}"
                    logger.error(error_msg)
                    if name == 'dense':
                        dense_error = error_msg
                    else:
                        sparse_error = error_msg
        
        return dense_results, sparse_results, dense_error, sparse_error
    
    def _run_dense_retrieval(
        self,
        query: str,
        filters: Optional[Dict[str, Any]],
        trace: Optional[Any],
    ) -> Tuple[Optional[List[RetrievalResult]], Optional[str]]:
        """执行稠密检索，并统一处理异常和 trace 记录。
        
        Args:
            query: 原始查询字符串。
            filters: 要应用的过滤条件。
            trace: 可选的 TraceContext。
            
        Returns:
            返回 (results, error)。成功时 error 为 None。
        """
        if self.dense_retriever is None:
            return None, "Dense retriever not configured"
        
        try:
            _t0 = time.monotonic()
            results = self.dense_retriever.retrieve(
                query=query,
                top_k=self.config.dense_top_k,
                filters=filters,
                trace=trace,
            )
            _elapsed = (time.monotonic() - _t0) * 1000.0
            if trace is not None:
                trace.record_stage("dense_retrieval", {
                    "method": "dense",
                    "provider": getattr(self.dense_retriever, 'provider_name', 'unknown'),
                    "top_k": self.config.dense_top_k,
                    "result_count": len(results) if results else 0,
                    "chunks": _snapshot_results(results),
                }, elapsed_ms=_elapsed)
            return results, None
        except Exception as e:
            error_msg = f"Dense retrieval error: {e}"
            logger.error(error_msg)
            if trace is not None:
                trace.record_stage("dense_retrieval", {
                    "method": "dense",
                    "error": error_msg,
                    "result_count": 0,
                })
            return None, error_msg
    
    def _run_sparse_retrieval(
        self,
        keywords: List[str],
        filters: Optional[Dict[str, Any]],
        trace: Optional[Any],
    ) -> Tuple[Optional[List[RetrievalResult]], Optional[str]]:
        """执行稀疏检索，并统一处理异常和 trace 记录。
        
        Args:
            keywords: QueryProcessor 产出的关键词列表。
            filters: 要应用的过滤条件。
            trace: 可选的 TraceContext。
            
        Returns:
            返回 (results, error)。成功时 error 为 None。
        """
        if self.sparse_retriever is None:
            return None, "Sparse retriever not configured"
        
        if not keywords:
            # 没有关键词时不算异常，只是说明这次 query 不适合走关键词检索。
            return [], None
        
        try:
            # 如果 filters 里带了 collection，就把它单独取出来交给稀疏检索层。
            collection = filters.get('collection') if filters else None
            
            _t0 = time.monotonic()
            results = self.sparse_retriever.retrieve(
                keywords=keywords,
                top_k=self.config.sparse_top_k,
                collection=collection,
                trace=trace,
            )
            _elapsed = (time.monotonic() - _t0) * 1000.0
            if trace is not None:
                trace.record_stage("sparse_retrieval", {
                    "method": "bm25",
                    "keyword_count": len(keywords),
                    "top_k": self.config.sparse_top_k,
                    "result_count": len(results) if results else 0,
                    "chunks": _snapshot_results(results),
                }, elapsed_ms=_elapsed)
            return results, None
        except Exception as e:
            error_msg = f"Sparse retrieval error: {e}"
            logger.error(error_msg)
            return None, error_msg
    
    def _fuse_results(
        self,
        dense_results: List[RetrievalResult],
        sparse_results: List[RetrievalResult],
        top_k: int,
        trace: Optional[Any],
    ) -> List[RetrievalResult]:
        """使用 RRF 融合稠密检索与稀疏检索结果。
        
        Args:
            dense_results: 稠密检索结果。
            sparse_results: 稀疏检索结果。
            top_k: 融合后需要返回的结果数量上限。
            trace: 可选的 TraceContext。
            
        Returns:
            融合并重新排序后的 RetrievalResult 列表。
        """
        if self.fusion is None:
            # 如果没有注入 fusion，则退回到简单的交替合并策略。
            # 这不是严格的 RRF，但可以在缺少融合器时尽量保留多路召回的多样性。
            logger.warning("No fusion configured, using simple interleave")
            return self._interleave_results(dense_results, sparse_results, top_k)
        
        # 将两路结果整理成 RRF 需要的 ranking lists。
        ranking_lists = []
        if dense_results:
            ranking_lists.append(dense_results)
        if sparse_results:
            ranking_lists.append(sparse_results)
        
        if not ranking_lists:
            return []
        
        if len(ranking_lists) == 1:
            # 只有一路结果时不需要真正融合，直接截断返回即可。
            return ranking_lists[0][:top_k]
        
        _t0 = time.monotonic()
        fused = self.fusion.fuse(
            ranking_lists=ranking_lists,
            top_k=top_k,
            trace=trace,
        )
        _elapsed = (time.monotonic() - _t0) * 1000.0
        if trace is not None:
            trace.record_stage("fusion", {
                "method": "rrf",
                "input_lists": len(ranking_lists),
                "top_k": top_k,
                "result_count": len(fused),
                "chunks": _snapshot_results(fused),
            }, elapsed_ms=_elapsed)
        return fused
    
    def _interleave_results(
        self,
        dense_results: List[RetrievalResult],
        sparse_results: List[RetrievalResult],
        top_k: int,
    ) -> List[RetrievalResult]:
        """没有 fusion 组件时的简单双路交替回退方案。
        
        Args:
            dense_results: 稠密检索结果。
            sparse_results: 稀疏检索结果。
            top_k: 最多返回的结果数量。
            
        Returns:
            按 chunk_id 去重后的交替合并结果。
        """
        seen_ids = set()
        interleaved = []
        
        d_idx, s_idx = 0, 0
        while len(interleaved) < top_k and (d_idx < len(dense_results) or s_idx < len(sparse_results)):
            # 先取一条稠密结果，再取一条稀疏结果，尽量让两类信号交替出现。
            if d_idx < len(dense_results):
                r = dense_results[d_idx]
                d_idx += 1
                if r.chunk_id not in seen_ids:
                    seen_ids.add(r.chunk_id)
                    interleaved.append(r)
            
            if len(interleaved) >= top_k:
                break
            
            if s_idx < len(sparse_results):
                r = sparse_results[s_idx]
                s_idx += 1
                if r.chunk_id not in seen_ids:
                    seen_ids.add(r.chunk_id)
                    interleaved.append(r)
        
        return interleaved
    
    def _apply_metadata_filters(
        self,
        results: List[RetrievalResult],
        filters: Dict[str, Any],
    ) -> List[RetrievalResult]:
        """对结果做元数据过滤（融合后的兜底过滤）。

        这是一个备用过滤机制，主要用于底层存储对过滤语法支持不完整
        或查询层未能完全下推过滤条件的情况。
        
        Args:
            results: 待过滤的结果列表。
            filters: 要应用的过滤条件。
            
        Returns:
            过滤后的结果列表。
        """
        if not filters:
            return results
        
        filtered = []
        for result in results:
            if self._matches_filters(result.metadata, filters):
                filtered.append(result)
        
        return filtered
    
    def _matches_filters(
        self,
        metadata: Dict[str, Any],
        filters: Dict[str, Any],
    ) -> bool:
        """检查单条结果的 metadata 是否满足全部过滤条件。
        
        Args:
            metadata: 结果携带的元数据。
            filters: 过滤条件。
            
        Returns:
            如果全部条件都匹配则返回 True，否则返回 False。
        """
        for key, value in filters.items():
            if key == "collection":
                # collection 可能存放在不同的 metadata 字段中，做兼容读取。
                meta_collection = (
                    metadata.get("collection") 
                    or metadata.get("source_collection")
                )
                if meta_collection != value:
                    return False
            elif key == "doc_type":
                if metadata.get("doc_type") != value:
                    return False
            elif key == "tags":
                # tags 是列表类型，判断是否有交集即可。
                meta_tags = metadata.get("tags", [])
                if not isinstance(value, list):
                    value = [value]
                if not set(meta_tags) & set(value):
                    return False
            elif key == "source_path":
                # 路径条件通常使用包含匹配，支持更宽松的过滤方式。
                source = metadata.get("source_path", "")
                if value not in source:
                    return False
            else:
                # 其他字段默认使用精确匹配。
                if metadata.get(key) != value:
                    return False
        
        return True


def create_hybrid_search(
    settings: Optional[Settings] = None,
    query_processor: Optional[QueryProcessor] = None,
    dense_retriever: Optional[DenseRetriever] = None,
    sparse_retriever: Optional[SparseRetriever] = None,
    fusion: Optional[RRFFusion] = None,
) -> HybridSearch:
    """工厂函数：创建带默认组件的 HybridSearch。

    这个方法主要用于简化调用方的初始化逻辑。如果没有显式传入 fusion，
    就会根据 settings 中的配置创建默认的 RRFFusion。
    
    Args:
        settings: 应用设置对象。
        query_processor: QueryProcessor 实例。
        dense_retriever: DenseRetriever 实例。
        sparse_retriever: SparseRetriever 实例。
        fusion: RRFFusion 实例；如果为 None，则创建默认实例。
        
    Returns:
        已完成配置的 HybridSearch 实例。
    
    Example:
        >>> hybrid = create_hybrid_search(
        ...     settings=settings,
        ...     query_processor=QueryProcessor(),
        ...     dense_retriever=dense_retriever,
        ...     sparse_retriever=sparse_retriever,
        ... )
    """
    # 如果外部没有提供融合器，就根据配置创建一个默认的 RRF 融合器。
    if fusion is None:
        from src.core.query_engine.fusion import RRFFusion
        rrf_k = 60
        if settings is not None:
            retrieval_config = getattr(settings, 'retrieval', None)
            if retrieval_config is not None:
                rrf_k = getattr(retrieval_config, 'rrf_k', 60)
        fusion = RRFFusion(k=rrf_k)
    
    return HybridSearch(
        settings=settings,
        query_processor=query_processor,
        dense_retriever=dense_retriever,
        sparse_retriever=sparse_retriever,
        fusion=fusion,
    )
