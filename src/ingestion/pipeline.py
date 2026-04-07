"""文档摄取流水线编排器。

该模块实现完整的摄取主流程：
1. 文件完整性检查（基于 SHA256 的增量跳过）。
2. 文档加载（PDF -> Document）。
3. 文本切块（Document -> Chunk 列表）。
4. 变换处理（精炼 + 元数据增强 + 图片描述）。
5. 编码向量化（Dense + Sparse）。
6. 存储落盘（向量库 + BM25 索引 + 图片索引）。

设计原则：
- 配置驱动：核心参数来自 settings.yaml。
- 可观测：每阶段输出日志与可选 trace。
- 优雅降级：LLM 子阶段失败时尽量不中断主流程。
- 幂等友好：同一文件未变化时可快速跳过。
"""

from pathlib import Path
from typing import Callable, List, Optional, Dict, Any
import time

from src.core.settings import Settings, load_settings, resolve_path
from src.core.types import Document, Chunk
from src.core.trace.trace_context import TraceContext
from src.observability.logger import get_logger

# Libs layer imports
from src.libs.loader.file_integrity import SQLiteIntegrityChecker
from src.libs.loader.pdf_loader import PdfLoader
from src.libs.embedding.embedding_factory import EmbeddingFactory
from src.libs.vector_store.vector_store_factory import VectorStoreFactory

# Ingestion layer imports
from src.ingestion.chunking.document_chunker import DocumentChunker
from src.ingestion.transform.chunk_refiner import ChunkRefiner
from src.ingestion.transform.metadata_enricher import MetadataEnricher
from src.ingestion.transform.image_captioner import ImageCaptioner
from src.ingestion.embedding.dense_encoder import DenseEncoder
from src.ingestion.embedding.sparse_encoder import SparseEncoder
from src.ingestion.embedding.batch_processor import BatchProcessor
from src.ingestion.storage.bm25_indexer import BM25Indexer
from src.ingestion.storage.vector_upserter import VectorUpserter
from src.ingestion.storage.image_storage import ImageStorage

logger = get_logger(__name__)


class PipelineResult:
    """流水线执行结果对象。

字段说明：
- success: 是否执行成功。
- file_path: 输入文件路径。
- doc_id: 文档标识（通常为内容哈希）。
- chunk_count: 切块数量。
- image_count: 处理到的图片数量。
- vector_ids: 已写入向量库的向量 ID 列表。
- error: 失败时的错误信息。
- stages: 分阶段统计信息，便于排查与审计。
    """
    
    def __init__(
        self,
        success: bool,
        file_path: str,
        doc_id: Optional[str] = None,
        chunk_count: int = 0,
        image_count: int = 0,
        vector_ids: Optional[List[str]] = None,
        error: Optional[str] = None,
        stages: Optional[Dict[str, Any]] = None
    ):
        self.success = success
        self.file_path = file_path
        self.doc_id = doc_id
        self.chunk_count = chunk_count
        self.image_count = image_count
        self.vector_ids = vector_ids or []
        self.error = error
        self.stages = stages or {}
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化字典（用于 API 返回或日志输出）。"""
        return {
            "success": self.success,
            "file_path": self.file_path,
            "doc_id": self.doc_id,
            "chunk_count": self.chunk_count,
            "image_count": self.image_count,
            "vector_ids_count": len(self.vector_ids),
            "error": self.error,
            "stages": self.stages
        }


class IngestionPipeline:
    """文档摄取主编排器。

该类负责串联摄取全阶段：
- 增量处理判断（完整性检查）。
- PDF 加载与图片提取。
- 文本切块与后处理。
- 稠密/稀疏编码。
- 向量与 BM25 索引写入。

典型用法：
    >>> from src.core.settings import load_settings
    >>> settings = load_settings("config/settings.yaml")
    >>> pipeline = IngestionPipeline(settings)
    >>> result = pipeline.run("documents/report.pdf")
    >>> print(result.chunk_count)
    """
    
    def __init__(
        self,
        settings: Settings,
        collection: str = "default",
        force: bool = False
    ):
        """初始化流水线及其所有组件。

        参数：
        - settings: 应用配置对象。
        - collection: 文档集合名，用于逻辑隔离存储。
        - force: 为 True 时强制重跑，忽略增量跳过判断。
        """
        self.settings = settings
        self.collection = collection
        self.force = force
        
        # 统一在构造期初始化组件，降低运行期首次调用抖动。
        # 这样做的好处：
        # 1) 首次 run() 不需要再承担额外初始化成本。
        # 2) 组件初始化失败会在启动早期暴露，问题定位更直接。
        # 3) 资源生命周期更清晰：初始化在 __init__，释放在 close。
        logger.info("Initializing Ingestion Pipeline components...")
        
        # 阶段 1：文件完整性检查
        self.integrity_checker = SQLiteIntegrityChecker(db_path=str(resolve_path("data/db/ingestion_history.db")))
        logger.info("  ✓ FileIntegrityChecker initialized")
        
        # 阶段 2：文档加载
        self.loader = PdfLoader(
            extract_images=True,
            image_storage_dir=str(resolve_path(f"data/images/{collection}"))
        )
        logger.info("  ✓ PdfLoader initialized")
        
        # 阶段 3：切块
        self.chunker = DocumentChunker(settings)
        logger.info("  ✓ DocumentChunker initialized")
        
        # 阶段 4：文本/图像变换增强
        self.chunk_refiner = ChunkRefiner(settings)
        logger.info(f"  ✓ ChunkRefiner initialized (use_llm={self.chunk_refiner.use_llm})")
        
        self.metadata_enricher = MetadataEnricher(settings)
        logger.info(f"  ✓ MetadataEnricher initialized (use_llm={self.metadata_enricher.use_llm})")
        
        self.image_captioner = ImageCaptioner(settings)
        has_vision = self.image_captioner.llm is not None
        logger.info(f"  ✓ ImageCaptioner initialized (vision_enabled={has_vision})")
        
        # 阶段 5：编码器
        # DenseEncoder 负责语义向量，SparseEncoder 负责关键词统计。
        # 两者并行存在是为了在召回阶段兼顾“语义相关性”和“关键词精确匹配”。
        embedding = EmbeddingFactory.create(settings)
        batch_size = settings.ingestion.batch_size if settings.ingestion else 100
        self.dense_encoder = DenseEncoder(embedding, batch_size=batch_size)
        logger.info(f"  ✓ DenseEncoder initialized (provider={settings.embedding.provider})")
        
        self.sparse_encoder = SparseEncoder()
        logger.info("  ✓ SparseEncoder initialized")
        
        self.batch_processor = BatchProcessor(
            dense_encoder=self.dense_encoder,
            sparse_encoder=self.sparse_encoder,
            batch_size=batch_size
        )
        logger.info(f"  ✓ BatchProcessor initialized (batch_size={batch_size})")
        
        # 阶段 6：存储
        # - vector_upserter: 将 dense 向量写入向量库（用于语义检索）
        # - bm25_indexer: 写入稀疏索引（用于关键词检索）
        # - image_storage: 维护图片索引（用于图片资产追踪与后续引用）
        self.vector_upserter = VectorUpserter(settings, collection_name=collection)
        logger.info(f"  ✓ VectorUpserter initialized (provider={settings.vector_store.provider}, collection={collection})")
        
        self.bm25_indexer = BM25Indexer(index_dir=str(resolve_path(f"data/db/bm25/{collection}")))
        logger.info("  ✓ BM25Indexer initialized")
        
        self.image_storage = ImageStorage(
            db_path=str(resolve_path("data/db/image_index.db")),
            images_root=str(resolve_path("data/images"))
        )
        logger.info("  ✓ ImageStorage initialized")
        
        logger.info("Pipeline initialization complete!")
    
    def run(
        self,
        file_path: str,
        trace: Optional[TraceContext] = None,
        on_progress: Optional[Callable[[str, int, int], None]] = None,
    ) -> PipelineResult:
        """执行单文件完整摄取流程。

        参数：
        - file_path: 待处理文件路径（通常为 PDF）。
        - trace: 可选链路追踪上下文。
        - on_progress: 可选进度回调，签名为
          `(stage_name, current, total)`。

        返回：
        - `PipelineResult`，包含成功状态、统计信息与分阶段结果。
        """
        # 统一转 Path，避免后续重复做字符串/路径转换。
        file_path = Path(file_path)
        stages: Dict[str, Any] = {}
        _total_stages = 6

        def _notify(stage_name: str, step: int) -> None:
            # 将阶段完成事件回调给上层（CLI / UI / WebSocket 等）。
            # 约定：step 从 1 开始，total 为固定总阶段数，便于进度条计算。
            if on_progress is not None:
                on_progress(stage_name, step, _total_stages)
        
        logger.info(f"=" * 60)
        logger.info(f"Starting Ingestion Pipeline for: {file_path}")
        logger.info(f"Collection: {self.collection}")
        logger.info(f"=" * 60)
        
        try:
            # ─────────────────────────────────────────────────────────────
            # 阶段 1：文件完整性检查（增量跳过）
            # ─────────────────────────────────────────────────────────────
            logger.info("\n📋 Stage 1: File Integrity Check")
            _notify("integrity", 1)
            
            # 使用文件内容哈希作为“版本指纹”，支撑幂等与增量处理。
            file_hash = self.integrity_checker.compute_sha256(str(file_path))
            logger.info(f"  File hash: {file_hash[:16]}...")
            
            if not self.force and self.integrity_checker.should_skip(file_hash):
                #integrity_checker.should_skip(file_hash)判断数据库中是否有该文件,没有的话说明我们的是新文件或者被改了
                # 命中历史记录且未强制重跑：直接返回成功（业务上等价于“已完成”）。
                logger.info(f"  ⏭️  File already processed, skipping (use force=True to reprocess)")
                return PipelineResult(
                    success=True,
                    file_path=str(file_path),
                    doc_id=file_hash,
                    stages={"integrity": {"skipped": True, "reason": "already_processed"}}
                )
            
            stages["integrity"] = {"file_hash": file_hash, "skipped": False}
            logger.info("  ✓ File needs processing")
            
            # ─────────────────────────────────────────────────────────────
            # 阶段 2：文档加载
            # ─────────────────────────────────────────────────────────────
            logger.info("\n📄 Stage 2: Document Loading")
            _notify("load", 2)
            
            # 统计加载耗时，便于后续性能分析。
            _t0 = time.monotonic()
            document = self.loader.load(str(file_path))
            _elapsed = (time.monotonic() - _t0) * 1000.0
            # text_preview只看到前200个字
            text_preview = document.text[:200].replace('\n', ' ') + "..." if len(document.text) > 200 else document.text
            image_count = len(document.metadata.get("images", []))
            
            logger.info(f"  Document ID: {document.id}")
            logger.info(f"  Text length: {len(document.text)} chars")
            logger.info(f"  Images extracted: {image_count}")
            logger.info(f"  Preview: {text_preview[:100]}...")
            
            stages["loading"] = {
                "doc_id": document.id,
                "text_length": len(document.text),
                "image_count": image_count
            }
            if trace is not None:
                # trace 中保留完整文本用于离线诊断。
                # 注意：若文档很大，trace 存储成本也会增加。
                trace.record_stage("load", {
                    "method": "markitdown",
                    "doc_id": document.id,
                    "text_length": len(document.text),
                    "image_count": image_count,
                    "text_preview": document.text,
                }, elapsed_ms=_elapsed)
            
            # ─────────────────────────────────────────────────────────────
            # 阶段 3：文本切块
            # ─────────────────────────────────────────────────────────────
            logger.info("\n✂️  Stage 3: Document Chunking")
            _notify("split", 3)
            
            _t0 = time.monotonic()
            chunks = self.chunker.split_document(document)
            _elapsed = (time.monotonic() - _t0) * 1000.0
            
            logger.info(f"  Chunks generated: {len(chunks)}")
            if chunks:
                logger.info(f"  First chunk ID: {chunks[0].id}")
                logger.info(f"  First chunk preview: {chunks[0].text[:100]}...")
            
            stages["chunking"] = {
                "chunk_count": len(chunks),
                # 平均 chunk 长度是观察切分质量的直观指标之一。
                "avg_chunk_size": sum(len(c.text) for c in chunks) // len(chunks) if chunks else 0
            }
            if trace is not None:
                trace.record_stage("split", {
                    "method": "recursive",
                    "chunk_count": len(chunks),
                    "avg_chunk_size": sum(len(c.text) for c in chunks) // len(chunks) if chunks else 0,
                    "chunks": [
                        {
                            "chunk_id": c.id,
                            "text": c.text,
                            "char_len": len(c.text),
                            "chunk_index": c.metadata.get("chunk_index", i),
                        }
                        for i, c in enumerate(chunks)
                    ],
                }, elapsed_ms=_elapsed)
            
            # ─────────────────────────────────────────────────────────────
            # 阶段 4：变换流水线
            # ─────────────────────────────────────────────────────────────
            logger.info("\n🔄 Stage 4: Transform Pipeline")
            _notify("transform", 4)
            
            # 4a: Chunk Refinement
            logger.info("  4a. Chunk Refinement...")
            _t0_transform = time.monotonic()
            # 保留精炼前文本快照，用于 trace 对比“改写前/改写后”。
            _pre_refine_texts = {c.id: c.text for c in chunks}
            chunks = self.chunk_refiner.transform(chunks, trace)
            refined_by_llm = sum(1 for c in chunks if c.metadata.get("refined_by") == "llm")
            refined_by_rule = sum(1 for c in chunks if c.metadata.get("refined_by") == "rule")
            logger.info(f"      LLM refined: {refined_by_llm}, Rule refined: {refined_by_rule}")
            
            # 4b: Metadata Enrichment
            logger.info("  4b. Metadata Enrichment...")
            chunks = self.metadata_enricher.transform(chunks, trace)
            enriched_by_llm = sum(1 for c in chunks if c.metadata.get("enriched_by") == "llm")
            enriched_by_rule = sum(1 for c in chunks if c.metadata.get("enriched_by") == "rule")
            logger.info(f"      LLM enriched: {enriched_by_llm}, Rule enriched: {enriched_by_rule}")
            
            # 4c: Image Captioning
            logger.info("  4c. Image Captioning...")
            chunks = self.image_captioner.transform(chunks, trace)
            captioned = sum(1 for c in chunks if c.metadata.get("image_captions"))
            logger.info(f"      Chunks with captions: {captioned}")
            
            stages["transform"] = {
                "chunk_refiner": {"llm": refined_by_llm, "rule": refined_by_rule},
                "metadata_enricher": {"llm": enriched_by_llm, "rule": enriched_by_rule},
                "image_captioner": {"captioned_chunks": captioned}
            }
            _elapsed_transform = (time.monotonic() - _t0_transform) * 1000.0
            if trace is not None:
                # transform 阶段记录较重，但对排查“为何召回效果变化”非常有价值。
                trace.record_stage("transform", {
                    "method": "refine+enrich+caption",
                    "refined_by_llm": refined_by_llm,
                    "refined_by_rule": refined_by_rule,
                    "enriched_by_llm": enriched_by_llm,
                    "enriched_by_rule": enriched_by_rule,
                    "captioned_chunks": captioned,
                    "chunks": [
                        {
                            "chunk_id": c.id,
                            "text_before": _pre_refine_texts.get(c.id, ""),
                            "text_after": c.text,
                            "char_len": len(c.text),
                            "refined_by": c.metadata.get("refined_by", ""),
                            "enriched_by": c.metadata.get("enriched_by", ""),
                            "title": c.metadata.get("title", ""),
                            "tags": c.metadata.get("tags", []),
                            "summary": c.metadata.get("summary", ""),
                        }
                        for c in chunks
                    ],
                }, elapsed_ms=_elapsed_transform)
            
            # ─────────────────────────────────────────────────────────────
            # 阶段 5：编码
            # ─────────────────────────────────────────────────────────────
            logger.info("\n🔢 Stage 5: Encoding")
            _notify("embed", 5)
            
            # 通过 BatchProcessor 统一调度 dense/sparse 编码，
            # 以批处理方式降低请求开销并提升吞吐。
            _t0 = time.monotonic()
            batch_result = self.batch_processor.process(chunks, trace)
            _elapsed = (time.monotonic() - _t0) * 1000.0
            
            dense_vectors = batch_result.dense_vectors
            sparse_stats = batch_result.sparse_stats
            
            logger.info(f"  Dense vectors: {len(dense_vectors)} (dim={len(dense_vectors[0]) if dense_vectors else 0})")
            logger.info(f"  Sparse stats: {len(sparse_stats)} documents")
            
            stages["encoding"] = {
                "dense_vector_count": len(dense_vectors),
                "dense_dimension": len(dense_vectors[0]) if dense_vectors else 0,
                "sparse_doc_count": len(sparse_stats)
            }
            if trace is not None:
                # 记录每个 chunk 的编码细节（dense + sparse），用于离线诊断。
                chunk_details = []
                for idx, c in enumerate(chunks):
                    detail: dict = {
                        "chunk_id": c.id,
                        "char_len": len(c.text),
                    }
                    # Dense：记录向量维度（理论上应一致，逐条记录便于发现异常）。
                    if idx < len(dense_vectors):
                        detail["dense_dim"] = len(dense_vectors[idx])
                    # Sparse：记录文档长度、唯一词数和高频词分布。
                    if idx < len(sparse_stats):
                        ss = sparse_stats[idx]
                        detail["doc_length"] = ss.get("doc_length", 0)
                        detail["unique_terms"] = ss.get("unique_terms", 0)
                        # 输出 Top-10 词频，便于判断分词/停用词效果是否合理。
                        tf = ss.get("term_frequencies", {})
                        top_terms = sorted(tf.items(), key=lambda x: x[1], reverse=True)[:10]
                        detail["top_terms"] = [{"term": t, "freq": f} for t, f in top_terms]
                    chunk_details.append(detail)

                trace.record_stage("embed", {
                    "method": "batch_processor",
                    "dense_vector_count": len(dense_vectors),
                    "dense_dimension": len(dense_vectors[0]) if dense_vectors else 0,
                    "sparse_doc_count": len(sparse_stats),
                    "chunks": chunk_details,
                }, elapsed_ms=_elapsed)
            
            # ─────────────────────────────────────────────────────────────
            # 阶段 6：存储
            # ─────────────────────────────────────────────────────────────
            logger.info("\n💾 Stage 6: Storage")
            _notify("upsert", 6)
            
            # 6a: 向量入库（ChromaDB）
            logger.info("  6a. Vector Storage (ChromaDB)...")
            _t0_storage = time.monotonic()
            vector_ids = self.vector_upserter.upsert(chunks, dense_vectors, trace)
            logger.info(f"      Stored {len(vector_ids)} vectors")

            # Align BM25 chunk_ids with Chroma vector IDs so the SparseRetriever
            # can look up BM25 hits in the vector store after retrieval.
            # 说明：BM25 命中后，仍需要通过向量库取回统一结构文本/metadata，
            # 因此这里做一次 ID 对齐，保证跨检索链路可互通。
            for stat, vid in zip(sparse_stats, vector_ids):
                stat["chunk_id"] = vid

            # 6b: BM25 索引构建
            logger.info("  6b. BM25 Index...")
            self.bm25_indexer.add_documents(
                sparse_stats,
                collection=self.collection,
                doc_id=document.id,
                trace=trace,
            )
            logger.info(f"      Index built for {len(sparse_stats)} documents")
            
            # 6c: 图片索引登记
            # 注意：PdfLoader 已负责落盘图片文件，这里只登记“可检索索引”。
            logger.info("  6c. Image Storage Index...")
            images = document.metadata.get("images", [])
            for img in images:
                img_path = Path(img["path"])
                if img_path.exists():
                    self.image_storage.register_image(
                        image_id=img["id"],
                        file_path=img_path,
                        collection=self.collection,
                        doc_hash=file_hash,
                        page_num=img.get("page", 0)
                    )
            logger.info(f"      Indexed {len(images)} images")
            
            stages["storage"] = {
                "vector_count": len(vector_ids),
                "bm25_docs": len(sparse_stats),
                "images_indexed": len(images)
            }
            _elapsed_storage = (time.monotonic() - _t0_storage) * 1000.0
            if trace is not None:
                # 记录 chunk_id -> vector_id 的映射，便于回溯存储一致性问题。
                chunk_storage = [
                    {
                        "chunk_id": c.id,
                        "vector_id": vector_ids[i] if i < len(vector_ids) else "—",
                        "collection": self.collection,
                        "store": "ChromaDB",
                    }
                    for i, c in enumerate(chunks)
                ]
                # 记录图片索引明细，便于定位缺图/错图问题。
                image_storage_details = [
                    {
                        "image_id": img["id"],
                        "file_path": str(img["path"]),
                        "page": img.get("page", 0),
                        "doc_hash": file_hash,
                    }
                    for img in images
                ]
                trace.record_stage("upsert", {
                    "dense_store": {
                        "backend": "ChromaDB",
                        "collection": self.collection,
                        "count": len(vector_ids),
                        "path": "data/db/chroma/",
                    },
                    "sparse_store": {
                        "backend": "BM25",
                        "collection": self.collection,
                        "count": len(sparse_stats),
                        "path": f"data/db/bm25/{self.collection}/",
                    },
                    "image_store": {
                        "backend": "ImageStorage (JSON index)",
                        "count": len(images),
                        "images": image_storage_details,
                    },
                    "chunk_mapping": chunk_storage,
                }, elapsed_ms=_elapsed_storage)
            
            # ─────────────────────────────────────────────────────────────
            # 成功收尾
            # ─────────────────────────────────────────────────────────────
            # 只有当全部阶段完成后才标记成功，避免出现“部分成功但状态已提交”。
            self.integrity_checker.mark_success(file_hash, str(file_path), self.collection)
            
            logger.info("\n" + "=" * 60)
            logger.info("✅ Pipeline completed successfully!")
            logger.info(f"   Chunks: {len(chunks)}")
            logger.info(f"   Vectors: {len(vector_ids)}")
            logger.info(f"   Images: {len(images)}")
            logger.info("=" * 60)
            
            return PipelineResult(
                success=True,
                file_path=str(file_path),
                doc_id=file_hash,
                chunk_count=len(chunks),
                image_count=len(images),
                vector_ids=vector_ids,
                stages=stages
            )
            
        except Exception as e:
            # 统一兜底：记录失败并写入完整性表，供后续重试与排障使用。
            logger.error(f"❌ Pipeline failed: {e}", exc_info=True)
            self.integrity_checker.mark_failed(file_hash, str(file_path), str(e))
            
            return PipelineResult(
                success=False,
                file_path=str(file_path),
                doc_id=file_hash if 'file_hash' in locals() else None,
                error=str(e),
                stages=stages
            )
    
    def close(self) -> None:
        """释放外部资源。

        当前主要关闭图片索引相关句柄；若后续新增连接型组件，
        也应在此统一回收，避免资源泄漏。
        """
        self.image_storage.close()


def run_pipeline(
    file_path: str,
    settings_path: Optional[str] = None,
    collection: str = "default",
    force: bool = False
) -> PipelineResult:
    """流水线便捷函数。

    适用于脚本或测试场景下的一次性调用：
    - 自动加载 settings。
    - 自动创建 pipeline。
    - 自动在 finally 中执行 close()。

    参数：
    - file_path: 待处理文件路径。
    - settings_path: 配置路径（为空时使用默认配置）。
    - collection: 目标集合名。
    - force: 是否强制重跑。

    返回：
    - PipelineResult：包含成功状态、统计信息与阶段数据。
    """
    settings = load_settings(settings_path)
    pipeline = IngestionPipeline(settings, collection=collection, force=force)
    
    try:
        return pipeline.run(file_path)
    finally:
        pipeline.close()
