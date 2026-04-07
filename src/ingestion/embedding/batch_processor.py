"""批处理器：协调稠密与稀疏编码的编排器。

本模块实现摄取流水线中的 BatchProcessor，负责将 `Chunk` 列表按照
配置分批（batch）发送到稠密编码器（DenseEncoder）和稀疏编码器（SparseEncoder），
并收集处理结果与统计信息。

设计要点：
- 编排职责：统一触发 dense/sparse 两套编码器并聚合结果。
- 配置驱动：批次大小来自 settings，不在代码中硬编码。
- 可观测：支持通过 `trace` 记录每个批次的耗时与统计信息。
- 错误隔离：单个批次失败不应使整个流水线崩溃，尽可能记录错误并继续处理。
- 顺序保证：输出顺序与输入 chunk 顺序一致，便于后续与存储对齐。
"""

from typing import List, Dict, Any, Optional, Tuple
import time
from dataclasses import dataclass

from src.core.types import Chunk
from src.ingestion.embedding.dense_encoder import DenseEncoder
from src.ingestion.embedding.sparse_encoder import SparseEncoder


@dataclass
class BatchResult:
    """批处理结果对象。

    属性：
        dense_vectors: 每个 chunk 对应的稠密向量列表（List[List[float]）
        sparse_stats: 每个 chunk 的稀疏统计信息列表（词频、长度等）
        batch_count: 处理的批次数量
        total_time: 总处理耗时（秒）
        successful_chunks: 成功处理的 chunk 数
        failed_chunks: 处理失败的 chunk 数
    """
    dense_vectors: List[List[float]]
    sparse_stats: List[Dict[str, Any]]
    batch_count: int
    total_time: float
    successful_chunks: int
    failed_chunks: int


class BatchProcessor:
    """批处理编排器，用于统一触发稠密和稀疏编码。

    说明：
    - 每次调用 `process()` 为一次无状态操作（不在实例上保存跨调用状态）。
    - 稠密向量（用于语义检索）与稀疏统计（用于关键词检索）在逻辑上
      是并列的两个输出，最终需要按输入顺序对齐，用于后续存储与检索链路。
    - 通过 `trace` 可以记录批次级别的耗时与异常信息，便于离线诊断。
    """
    
    def __init__(
        self,
        dense_encoder: DenseEncoder,
        sparse_encoder: SparseEncoder,
        batch_size: int = 100,
    ):
        """Initialize BatchProcessor.
        
        Args:
            dense_encoder: DenseEncoder instance for embedding generation
            sparse_encoder: SparseEncoder instance for term statistics
            batch_size: Number of chunks to process per batch (default: 100)
        
        Raises:
            ValueError: If batch_size <= 0
        """
        if batch_size <= 0:
            raise ValueError(f"batch_size must be positive, got {batch_size}")
        
        self.dense_encoder = dense_encoder
        self.sparse_encoder = sparse_encoder
        self.batch_size = batch_size
    
    def process(
        self,
        chunks: List[Chunk],
        trace: Optional[Any] = None,
    ) -> BatchResult:
        """执行批处理编码。

        执行流程：
        1. 校验输入非空
        2. 将 chunks 切分为若干批次
        3. 依次对每个批次调用 dense_encoder 与 sparse_encoder
        4. 聚合每个批次的结果与统计信息
        5. 将批次级与全局统计写入 `trace`（若提供）

        错误处理策略：单个批次若抛出异常，会记录失败但继续处理后续批次，
        最终返回的 `failed_chunks` 表示失败数量。
        """
        if not chunks:
            raise ValueError("Cannot process empty chunks list")
        
        start_time = time.time()
        
        # Create batches
        batches = self._create_batches(chunks)
        batch_count = len(batches)
        
        # Process all batches
        dense_vectors: List[List[float]] = []
        sparse_stats: List[Dict[str, Any]] = []
        successful_chunks = 0
        failed_chunks = 0
        
        for batch_idx, batch in enumerate(batches):
            batch_start = time.time()
            
            try:
                # Dense encoding
                batch_dense = self.dense_encoder.encode(batch, trace=trace)
                dense_vectors.extend(batch_dense)
                
                # Sparse encoding
                batch_sparse = self.sparse_encoder.encode(batch, trace=trace)
                sparse_stats.extend(batch_sparse)
                
                successful_chunks += len(batch)
                
            except Exception as e:
                # 遇到批次级异常时记录错误并继续，避免单个批次导致整体失败
                failed_chunks += len(batch)
                if trace:
                    trace.record_stage(
                        f"batch_{batch_idx}_error",
                        {"error": str(e), "batch_size": len(batch)}
                    )
            
            batch_duration = time.time() - batch_start
            
            # 若提供 trace，则记录每个批次的耗时与大小，便于性能分析
            if trace:
                trace.record_stage(
                    f"batch_{batch_idx}",
                    {
                        "batch_size": len(batch),
                        "duration_seconds": batch_duration,
                        "chunks_processed": len(batch)
                    }
                )
        
        total_time = time.time() - start_time
        
        # 记录整体处理统计信息到 trace
        if trace:
            trace.record_stage(
                "batch_processing",
                {
                    "total_chunks": len(chunks),
                    "batch_count": batch_count,
                    "batch_size": self.batch_size,
                    "successful_chunks": successful_chunks,
                    "failed_chunks": failed_chunks,
                    "total_time_seconds": total_time
                }
            )
        
        return BatchResult(
            dense_vectors=dense_vectors,
            sparse_stats=sparse_stats,
            batch_count=batch_count,
            total_time=total_time,
            successful_chunks=successful_chunks,
            failed_chunks=failed_chunks
        )
    
    def _create_batches(self, chunks: List[Chunk]) -> List[List[Chunk]]:
        """将 chunk 列表按 `batch_size` 切分为若干批次并返回。

        保证顺序不变：批次内部及批次间的相对顺序与输入保持一致，
        这一点在后续将编码结果与原始 chunk 映射时非常关键。
        """
        batches = []
        for i in range(0, len(chunks), self.batch_size):
            batch = chunks[i:i + self.batch_size]
            batches.append(batch)
        return batches
    
    def get_batch_count(self, total_chunks: int) -> int:
        """计算给定 chunk 总数会被划分为多少个批次。

        用于测试或在 UI 上展示预计批次数量。
        """
        if total_chunks <= 0:
            return 0
        return (total_chunks + self.batch_size - 1) // self.batch_size
