"""Setup mMARCO Chinese benchmark for ablation evaluation.

1. Load 100 random queries with positive doc_ids from qrels
2. Load 500 random negative passages via reservoir sampling
3. Directly upsert all 600 passages into ChromaDB (collection='mmarco')
   with chunk_id = doc_id (no chunking, each passage is one chunk)
4. Build BM25 index for the same passages
5. Generate test set with expected_chunk_ids = positive docids
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

from src.core.settings import load_settings, resolve_path
from src.core.types import Chunk
from src.libs.embedding.embedding_factory import EmbeddingFactory
from src.libs.vector_store.vector_store_factory import VectorStoreFactory
from src.ingestion.embedding.sparse_encoder import SparseEncoder
from src.ingestion.storage.bm25_indexer import BM25Indexer


RANDOM_SEED = 42
random.seed(RANDOM_SEED)

DATA_DIR = Path("data/mmarco_chinese/data")
QUERIES_PATH = DATA_DIR / "google/queries/dev/chinese_queries.dev.tsv"
QRELS_PATH = DATA_DIR / "qrels.dev.tsv"
COLLECTION_PATH = DATA_DIR / "google/collections/chinese_collection.tsv"

BM25_INDEX_DIR = str(resolve_path("data/db/bm25/mmarco"))


def load_queries() -> Dict[str, str]:
    queries = {}
    with open(QUERIES_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                queries[parts[0]] = parts[1]
    return queries


def load_qrels() -> Dict[str, List[str]]:
    qrels = defaultdict(list)
    with open(QRELS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 4 and parts[3] == "1":
                qrels[parts[0]].append(parts[2])
    return qrels


def select_queries(queries: Dict[str, str], qrels: Dict[str, List[str]], n: int = 100):
    valid_qids = [qid for qid in queries if qid in qrels and qrels[qid]]
    selected = random.sample(valid_qids, min(n, len(valid_qids)))
    positive_doc_ids = set()
    for qid in selected:
        for doc_id in qrels[qid]:
            positive_doc_ids.add(doc_id)
    return selected, positive_doc_ids


def load_passages(target_doc_ids: set, n_negatives: int = 500):
    """Scan collection once: collect positives + reservoir-sample negatives."""
    positives = {}
    negatives = []          # reservoir sample
    scanned = 0

    with open(COLLECTION_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            doc_id, text = parts
            scanned += 1

            if doc_id in target_doc_ids:
                positives[doc_id] = text
            else:
                # Reservoir sampling for negatives
                if len(negatives) < n_negatives:
                    negatives.append((doc_id, text))
                else:
                    j = random.randint(0, scanned - 1)
                    if j < n_negatives:
                        negatives[j] = (doc_id, text)

            if scanned % 1_000_000 == 0:
                print(f"  Scanned {scanned:,} lines, pos={len(positives)}/{len(target_doc_ids)}, neg={len(negatives)}")

    return positives, negatives


def embed_passages(passages: List[tuple], settings, batch_size: int = 10):
    """Generate dense vectors for passages."""
    embedding = EmbeddingFactory.create(settings)
    vectors = []
    texts = [text for _, text in passages]

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        print(f"  Embedding batch {i // batch_size + 1}/{(len(texts) + batch_size - 1) // batch_size} ...")
        batch_vectors = embedding.embed(batch)
        vectors.extend(batch_vectors)

    return vectors


def build_bm25_index(passages: List[tuple]):
    """Build BM25 index for passages."""
    print("\n  Building BM25 index...")
    indexer = BM25Indexer(index_dir=BM25_INDEX_DIR)
    sparse_encoder = SparseEncoder()

    chunks = []
    for doc_id, text in passages:
        chunks.append(Chunk(id=doc_id, text=text, metadata={"source_path": doc_id, "source_ref": doc_id}))

    term_stats = sparse_encoder.encode(chunks)
    indexer.add_documents(term_stats, collection="mmarco")
    print(f"  BM25 index built: {len(passages)} docs")


def main():
    print("=" * 60)
    print("mMARCO Chinese Benchmark Setup")
    print("=" * 60)

    # 1. Load queries & qrels
    print("\n[1/5] Loading queries and qrels...")
    queries = load_queries()
    qrels = load_qrels()
    selected_qids, positive_doc_ids = select_queries(queries, qrels, n=100)
    print(f"  Selected {len(selected_qids)} queries, {len(positive_doc_ids)} positive doc_ids")

    # 2. Load passages from collection
    print("\n[2/5] Loading passages from collection (2.6GB, single scan)...")
    positive_passages, negative_passages = load_passages(positive_doc_ids, n_negatives=500)
    print(f"  Found {len(positive_passages)}/{len(positive_doc_ids)} positives, {len(negative_passages)} negatives")

    # Combine all passages
    all_passages = list(positive_passages.items()) + negative_passages
    print(f"  Total passages: {len(all_passages)}")

    # 3. Generate embeddings
    print("\n[3/5] Generating embeddings (batch_size=10)...")
    settings = load_settings()
    vectors = embed_passages(all_passages, settings, batch_size=10)
    print(f"  Generated {len(vectors)} vectors, dim={len(vectors[0])}")

    # 4. Upsert into ChromaDB
    print("\n[4/5] Upserting into ChromaDB (collection='mmarco')...")
    vector_store = VectorStoreFactory.create(settings, collection_name="mmarco")

    records = []
    for (doc_id, text), vec in zip(all_passages, vectors):
        records.append({
            "id": doc_id,
            "vector": vec,
            "metadata": {
                "source_path": doc_id,
                "text": text[:500],  # Store preview in metadata
            },
        })

    # Batch upsert to avoid overwhelming ChromaDB
    batch_size = 100
    for i in range(0, len(records), batch_size):
        vector_store.upsert(records[i:i + batch_size])
        if (i + batch_size) % 200 == 0:
            print(f"    Upserted {min(i + batch_size, len(records))}/{len(records)}")

    print(f"  Upserted {len(records)} records")

    # 5. Build BM25 index
    build_bm25_index(all_passages)

    # 6. Generate test set
    print("\n[5/5] Generating test set...")
    test_cases = []
    for qid in selected_qids:
        test_cases.append({
            "query": queries[qid],
            "reference_answer": "",
            "expected_chunk_ids": qrels[qid],
            "expected_sources": [],
            "tags": ["factual"],
            "history": [],
        })

    test_set = {
        "description": "mMARCO Chinese dev subset (100 queries) for RAG ablation",
        "version": "1.0",
        "source": "unicamp-dl/mmarco",
        "total_cases": len(test_cases),
        "test_cases": test_cases,
    }

    output_path = Path("tests/fixtures/golden_test_set_mmarco.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(test_set, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Saved to: {output_path}")

    print("\n" + "=" * 60)
    print("DONE! Run ablation with:")
    print(f"  python scripts/run_ablation.py --collection mmarco --test-set {output_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
