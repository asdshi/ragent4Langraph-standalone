"""Pre-compute and cache query embeddings for mMARCO benchmark.

This eliminates repeated Embedding API calls during ablation experiments.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import json
import pickle

from src.core.settings import load_settings
from src.libs.embedding.embedding_factory import EmbeddingFactory


def main():
    settings = load_settings()
    embedding = EmbeddingFactory.create(settings)

    with open("tests/fixtures/golden_test_set_mmarco.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    queries = [case["query"] for case in data["test_cases"]]
    print(f"Pre-computing embeddings for {len(queries)} queries...")

    # Batch in groups of 10 (DashScope limit)
    all_vectors = []
    batch_size = 10
    for i in range(0, len(queries), batch_size):
        batch = queries[i:i + batch_size]
        print(f"  Batch {i // batch_size + 1}/{(len(queries) + batch_size - 1) // batch_size} ...")
        vectors = embedding.embed(batch)
        all_vectors.extend(vectors)

    cache = {
        "queries": queries,
        "vectors": all_vectors,
        "dim": len(all_vectors[0]),
    }

    cache_path = Path("data/mmarco_chinese/query_embeddings_cache.pkl")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "wb") as f:
        pickle.dump(cache, f)

    print(f"\nCached {len(all_vectors)} embeddings to: {cache_path}")


if __name__ == "__main__":
    main()
