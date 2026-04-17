"""为 golden test set 填充 expected_chunk_ids ground truth.

对已有测试集中的每个 query，使用 HybridSearch 检索 top-5 结果，
将返回的 chunk_ids 写入 expected_chunk_ids 字段，用于后续 ablation 评估。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import json

from src.core.settings import load_settings, resolve_path
from src.core.query_engine.hybrid_search import HybridSearch, HybridSearchConfig
from src.core.query_engine.query_processor import QueryProcessor
from src.core.query_engine.dense_retriever import create_dense_retriever
from src.core.query_engine.sparse_retriever import create_sparse_retriever
from src.core.query_engine.fusion import RRFFusion
from src.ingestion.storage.bm25_indexer import BM25Indexer
from src.libs.embedding.embedding_factory import EmbeddingFactory
from src.libs.vector_store.vector_store_factory import VectorStoreFactory


def build_hybrid_search(settings, collection: str):
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

    config = HybridSearchConfig(
        dense_top_k=10,
        sparse_top_k=10,
        fusion_top_k=5,
        enable_dense=True,
        enable_sparse=True,
    )

    return HybridSearch(
        settings=settings,
        query_processor=query_processor,
        dense_retriever=dense_retriever,
        sparse_retriever=sparse_retriever,
        fusion=fusion,
        config=config,
    )


def main():
    settings = load_settings()
    hybrid = build_hybrid_search(settings, "default")

    test_set_path = Path("tests/fixtures/golden_test_set_v2.json")
    with open(test_set_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    test_cases = data["test_cases"]
    updated = 0

    print(f"Populating chunk IDs for {len(test_cases)} test cases...")

    for i, case in enumerate(test_cases):
        query = case.get("query", "").strip()
        if not query:
            continue

        try:
            results = hybrid.search(query=query)
            results = results if isinstance(results, list) else results.results
            chunk_ids = [r.chunk_id for r in results if getattr(r, "chunk_id", None)]

            case["expected_chunk_ids"] = chunk_ids
            updated += 1

            if (i + 1) % 10 == 0:
                print(f"  Processed {i + 1}/{len(test_cases)}")

        except Exception as e:
            print(f"  [Error] Query '{query[:30]}...': {e}")
            case["expected_chunk_ids"] = []

    data["total_cases"] = len(test_cases)
    data["note"] = "expected_chunk_ids populated by hybrid search (top-5)"

    with open(test_set_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nDone. Updated {updated} cases.")
    print(f"Saved to: {test_set_path}")


if __name__ == "__main__":
    main()
