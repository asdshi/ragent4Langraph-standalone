"""跨存储文档生命周期管理。

该模块提供统一入口，在多个后端之间协同完成：
- 列出文档（含分块/图片统计）。
- 查询文档详情（分块 ID、图片 ID）。
- 删除文档（级联 Chroma、BM25、图片索引、完整性记录）。

设计原则：
- 协同一致：一次调用触发多存储联动。
- 容错优先：局部失败不阻断剩余清理步骤。
- 读写分离：查询类接口不产生副作用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result data-classes
# ---------------------------------------------------------------------------

@dataclass
class DocumentInfo:
    """已摄取文档的摘要信息。"""

    source_path: str
    source_hash: str
    collection: Optional[str] = None
    chunk_count: int = 0
    image_count: int = 0
    processed_at: Optional[str] = None


@dataclass
class DocumentDetail(DocumentInfo):
    """文档详情信息（含 chunk/image 标识列表）。"""

    chunk_ids: List[str] = field(default_factory=list)
    image_ids: List[str] = field(default_factory=list)


@dataclass
class DeleteResult:
    """删除操作结果。"""

    success: bool
    chunks_deleted: int = 0
    bm25_removed: bool = False
    images_deleted: int = 0
    integrity_removed: bool = False
    errors: List[str] = field(default_factory=list)


@dataclass
class CollectionStats:
    """集合级聚合统计。"""

    collection: Optional[str] = None
    document_count: int = 0
    chunk_count: int = 0
    image_count: int = 0


# ---------------------------------------------------------------------------
# DocumentManager
# ---------------------------------------------------------------------------

class DocumentManager:
    """在多后端之间协调文档生命周期。

参数：
- chroma_store: 向量存储。
- bm25_indexer: 稀疏索引存储。
- image_storage: 图片存储与索引。
- file_integrity: 摄取历史与文件完整性记录。
    """

    def __init__(
        self,
        chroma_store: Any,
        bm25_indexer: Any,
        image_storage: Any,
        file_integrity: Any,
    ) -> None:
        self.chroma = chroma_store
        self.bm25 = bm25_indexer
        self.images = image_storage
        self.integrity = file_integrity

    # ------------------------------------------------------------------
    # list_documents
    # ------------------------------------------------------------------

    def list_documents(
        self, collection: Optional[str] = None
    ) -> List[DocumentInfo]:
        """列出文档并汇总统计信息。"""
        records = self.integrity.list_processed(collection)

        docs: List[DocumentInfo] = []
        for rec in records:
            source_hash = rec["file_hash"]
            source_path = rec["file_path"]
            coll = rec.get("collection")

            # 统计向量库中的分块数量
            chunk_count = self._count_chunks(source_hash)

            # 统计图片数量
            image_count = self._count_images(source_hash)

            docs.append(
                DocumentInfo(
                    source_path=source_path,
                    source_hash=source_hash,
                    collection=coll,
                    chunk_count=chunk_count,
                    image_count=image_count,
                    processed_at=rec.get("processed_at"),
                )
            )

        return docs

    # ------------------------------------------------------------------
    # get_document_detail
    # ------------------------------------------------------------------

    def get_document_detail(self, doc_id: str) -> Optional[DocumentDetail]:
        """获取单文档详情。"""
        # 先通过完整性记录定位文档主键（source_hash）
        all_records = self.integrity.list_processed()
        record = None
        for rec in all_records:
            if rec["file_hash"] == doc_id:
                record = rec
                break

        if record is None:
            return None

        source_hash = record["file_hash"]

        # 汇总分块 ID
        chunk_ids = self._get_chunk_ids(source_hash)

        # 汇总图片 ID
        image_ids = self._get_image_ids(source_hash)

        return DocumentDetail(
            source_path=record["file_path"],
            source_hash=source_hash,
            collection=record.get("collection"),
            chunk_count=len(chunk_ids),
            image_count=len(image_ids),
            processed_at=record.get("processed_at"),
            chunk_ids=chunk_ids,
            image_ids=image_ids,
        )

    # ------------------------------------------------------------------
    # delete_document
    # ------------------------------------------------------------------

    def delete_document(
        self,
        source_path: str,
        collection: str = "default",
        source_hash: Optional[str] = None,
    ) -> DeleteResult:
        """跨后端级联删除文档。"""
        result = DeleteResult(success=True)

        # 优先使用外部传入 hash；否则尝试读文件计算；最后回退到历史记录反查。
        if source_hash is None:
            try:
                source_hash = self.integrity.compute_sha256(source_path)
            except Exception as e:
                source_hash = self._hash_from_path(source_path)
                if source_hash is None:
                    result.success = False
                    result.errors.append(f"Cannot identify document: {e}")
                    return result

        # 1) 向量库：删除属于该文档 hash 的全部分块
        try:
            count = self.chroma.delete_by_metadata(
                {"doc_hash": source_hash}
            )
            result.chunks_deleted = count
        except Exception as e:
            result.errors.append(f"ChromaDB delete failed: {e}")

        # 2) BM25：删除该文档倒排记录
        try:
            result.bm25_removed = self.bm25.remove_document(
                source_hash, collection
            )
        except Exception as e:
            result.errors.append(f"BM25 remove failed: {e}")

        # 3) 图片索引：按文档 hash 清理图片及索引
        try:
            images = self.images.list_images(doc_hash=source_hash)
            deleted_imgs = 0
            for img in images:
                if self.images.delete_image(img["image_id"]):
                    deleted_imgs += 1
            result.images_deleted = deleted_imgs
        except Exception as e:
            result.errors.append(f"ImageStorage delete failed: {e}")

        # 4) 完整性记录：删除摄取历史
        try:
            result.integrity_removed = self.integrity.remove_record(
                source_hash
            )
        except Exception as e:
            result.errors.append(f"FileIntegrity remove failed: {e}")

        if result.errors:
            result.success = False

        return result

    # ------------------------------------------------------------------
    # get_collection_stats
    # ------------------------------------------------------------------

    def get_collection_stats(
        self, collection: Optional[str] = None
    ) -> CollectionStats:
        """获取集合聚合统计。"""
        docs = self.list_documents(collection)
        chunk_total = sum(d.chunk_count for d in docs)
        image_total = sum(d.image_count for d in docs)

        return CollectionStats(
            collection=collection,
            document_count=len(docs),
            chunk_count=chunk_total,
            image_count=image_total,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _count_chunks(self, source_hash: str) -> int:
        """统计指定文档 hash 的分块数。"""
        try:
            results = self.chroma.collection.get(
                where={"doc_hash": source_hash}, include=[]
            )
            return len(results.get("ids", []))
        except Exception:
            return 0

    def _get_chunk_ids(self, source_hash: str) -> List[str]:
        """获取指定文档 hash 的分块 ID 列表。"""
        try:
            results = self.chroma.collection.get(
                where={"doc_hash": source_hash}, include=[]
            )
            return results.get("ids", [])
        except Exception:
            return []

    def _count_images(self, source_hash: str) -> int:
        """统计指定文档 hash 的图片数量。"""
        try:
            return len(self.images.list_images(doc_hash=source_hash))
        except Exception:
            return 0

    def _get_image_ids(self, source_hash: str) -> List[str]:
        """获取指定文档 hash 的图片 ID 列表。"""
        try:
            imgs = self.images.list_images(doc_hash=source_hash)
            return [img["image_id"] for img in imgs]
        except Exception:
            return []

    def _hash_from_path(self, source_path: str) -> Optional[str]:
        """通过历史记录从文件路径反查 source_hash。"""
        try:
            for rec in self.integrity.list_processed():
                if rec["file_path"] == source_path:
                    return rec["file_hash"]
        except Exception:
            pass
        return None
