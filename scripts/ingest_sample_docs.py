"""批量摄入 tests/fixtures/sample_documents/ 下的文件到 default collection.

逐个文件处理，失败时自动重试，避免 Embedding API 超时导致整批失败。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.core.settings import load_settings
from src.ingestion.pipeline import IngestionPipeline


def main():
    settings = load_settings()
    pipeline = IngestionPipeline(settings, collection="default", force=False)

    doc_dir = Path("tests/fixtures/sample_documents")
    files = sorted(
        f for f in doc_dir.iterdir()
        if f.is_file() and f.suffix.lower() in {".pdf", ".txt", ".docx", ".md"}
    )

    print(f"Found {len(files)} files to ingest:")
    for f in files:
        print(f"  - {f.name}")

    success_count = 0
    fail_count = 0

    for file_path in files:
        print(f"\n{'='*60}")
        print(f"Ingesting: {file_path.name}")
        print(f"{'='*60}")

        result = None
        for attempt in range(3):
            try:
                result = pipeline.run(str(file_path))
                if result.success:
                    print(f"  [OK] Success: {result.chunk_count} chunks, {len(result.vector_ids)} vectors")
                    success_count += 1
                    break
                else:
                    print(f"  [FAIL] Failed (attempt {attempt + 1}/3): {result.error}")
            except Exception as e:
                print(f"  [ERR] Exception (attempt {attempt + 1}/3): {e}")
                if attempt == 2:
                    fail_count += 1

    print(f"\n{'='*60}")
    print(f"DONE: {success_count} succeeded, {fail_count} failed")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
