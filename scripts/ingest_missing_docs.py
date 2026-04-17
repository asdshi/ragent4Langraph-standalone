"""摄入之前失败的缺失文档到 default collection."""

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
    files = [
        doc_dir / "chinese_long_doc.pdf",
        doc_dir / "chinese_technical_doc.pdf",
        doc_dir / "complex_technical_doc.pdf",
    ]

    success_count = 0
    fail_count = 0

    for file_path in files:
        print(f"\n{'='*60}")
        print(f"Ingesting: {file_path.name}")
        print(f"{'='*60}")

        for attempt in range(3):
            try:
                result = pipeline.run(str(file_path))
                if result.success:
                    print(f"  [OK] Success: {result.chunk_count} chunks, {len(result.vector_ids)} vectors")
                    success_count += 1
                    break
                else:
                    print(f"  [FAIL] Failed (attempt {attempt + 1}/3): {result.error}")
                    if attempt == 2:
                        fail_count += 1
            except Exception as e:
                print(f"  [ERR] Exception (attempt {attempt + 1}/3): {e}")
                if attempt == 2:
                    fail_count += 1

    print(f"\n{'='*60}")
    print(f"DONE: {success_count} succeeded, {fail_count} failed")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
