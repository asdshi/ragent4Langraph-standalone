"""Universal document loader using MarkItDown with VLM OCR fallback for scanned PDFs.

This module provides a single loader entry-point for multiple document formats:
- PDF (including scanned PDFs via VLM OCR fallback)
- Word (.docx)
- Excel (.xlsx, .xls)
- PowerPoint (.pptx)
- Text (.txt, .md, .csv, .json, .yaml, .yml)
- HTML (.html, .htm)

Design principles:
- Unified parsing via MarkItDown for all supported formats.
- Scanned PDF detection based on low text density per page.
- VLM OCR uses the existing vision_llm configuration (e.g., qwen-vl-max).
- Image extraction for PDFs remains compatible with downstream ImageCaptioner.
"""

from __future__ import annotations

import hashlib
import io
import logging
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from markitdown import MarkItDown, UnsupportedFormatException
    MARKITDOWN_AVAILABLE = True
except ImportError:
    MARKITDOWN_AVAILABLE = False

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

from PIL import Image

from src.core.settings import Settings
from src.core.types import Document
from src.libs.loader.base_loader import BaseLoader
from src.libs.llm.base_vision_llm import BaseVisionLLM, ImageInput
from src.libs.llm.llm_factory import LLMFactory

logger = logging.getLogger(__name__)

# Thresholds for scanned PDF detection
# If total stripped text < 100 chars OR average per page < 30, treat as scanned
SCANNED_PDF_MIN_TOTAL_CHARS = 100
SCANNED_PDF_MIN_AVG_CHARS_PER_PAGE = 30
# Max parallel workers for VLM OCR to avoid API rate limits
VLM_OCR_MAX_WORKERS = 3
# Default VLM prompt for OCR
VLM_OCR_PROMPT = "请提取这张图片中的所有文字，保持原有段落和排版，直接输出文字内容，不要添加任何解释。"


class UniversalLoader(BaseLoader):
    """Universal document loader supporting multiple formats with scanned PDF OCR fallback."""

    SUPPORTED_EXTENSIONS = {
        '.pdf', '.docx', '.txt', '.md', '.csv',
        '.xlsx', '.xls', '.pptx', '.html', '.htm',
        '.json', '.yaml', '.yml',
    }

    def __init__(
        self,
        settings: Optional[Settings] = None,
        extract_images: bool = True,
        image_storage_dir: str | Path = "data/images",
    ):
        if not MARKITDOWN_AVAILABLE:
            raise ImportError(
                "MarkItDown is required for UniversalLoader. "
                "Install with: pip install markitdown"
            )

        self.settings = settings
        self.extract_images = extract_images
        self.image_storage_dir = Path(image_storage_dir)
        self._markitdown = MarkItDown()
        self._vision_llm: Optional[BaseVisionLLM] = None
        self._vision_llm_lock = threading.Lock()

    def load(self, file_path: str | Path) -> Document:
        """Load and parse a document file.

        Args:
            file_path: Path to the document file.

        Returns:
            Document with parsed text and metadata.
        """
        path = self._validate_file(file_path)
        ext = path.suffix.lower()

        if ext not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {ext}. Supported: {', '.join(sorted(self.SUPPORTED_EXTENSIONS))}")

        doc_hash = self._compute_file_hash(path)
        doc_id = f"doc_{doc_hash[:16]}"

        # Parse with MarkItDown
        try:
            md_result = self._markitdown.convert(str(path))
            text_content = md_result.text_content if hasattr(md_result, 'text_content') else str(md_result)
        except UnsupportedFormatException as e:
            logger.error(f"Unsupported format for {path}: {e}")
            raise ValueError(f"Unsupported format: {e}") from e
        except Exception as e:
            logger.error(f"Failed to parse {path} with MarkItDown: {e}")
            raise RuntimeError(f"Document parsing failed: {e}") from e

        metadata: Dict[str, Any] = {
            "source_path": str(path),
            "doc_type": ext.lstrip('.'),
            "doc_hash": doc_hash,
        }

        extract_method = "markitdown"
        page_count = 0
        word_count = len(text_content)

        if ext == '.pdf':
            page_count = self._get_pdf_page_count(path)
            metadata["page_count"] = page_count

            # Scanned PDF detection
            is_scanned = False
            if page_count > 0:
                stripped_len = len(text_content.strip())
                avg_per_page = stripped_len / page_count
                if stripped_len < SCANNED_PDF_MIN_TOTAL_CHARS or avg_per_page < SCANNED_PDF_MIN_AVG_CHARS_PER_PAGE:
                    is_scanned = True

            if is_scanned:
                logger.info(f"Detected scanned PDF ({path}, {page_count} pages, text_chars={len(text_content)}). Using VLM OCR.")
                try:
                    text_content = self._vlm_ocr_pdf(path)
                    extract_method = "vlm_ocr"
                    word_count = len(text_content)
                    if not text_content.strip():
                        raise RuntimeError("VLM OCR returned empty text")
                except Exception as e:
                    logger.error(f"VLM OCR failed for {path}: {e}")
                    raise RuntimeError(
                        f"该 PDF 为扫描件，但 OCR 提取失败：{e}。"
                        f"请检查 vision_llm (当前配置模型: {self.settings.vision_llm.model if self.settings and hasattr(self.settings, 'vision_llm') else 'unknown'}) 是否可用，或尝试上传可搜索 PDF。"
                    ) from e
            else:
                # Normal PDF: extract images if enabled
                if self.extract_images:
                    try:
                        text_content, images_metadata = self._extract_pdf_images(path, text_content, doc_hash)
                        if images_metadata:
                            metadata["images"] = images_metadata
                    except Exception as e:
                        logger.warning(f"Image extraction failed for {path}, continuing with text-only: {e}")

        title = self._extract_title(text_content)
        if title:
            metadata["title"] = title

        metadata["extract_method"] = extract_method
        metadata["page_count"] = page_count
        metadata["word_count"] = word_count

        return Document(
            id=doc_id,
            text=text_content,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _compute_file_hash(self, file_path: Path) -> str:
        sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _get_pdf_page_count(self, path: Path) -> int:
        if not PYMUPDF_AVAILABLE:
            return 0
        try:
            doc = fitz.open(path)
            count = len(doc)
            doc.close()
            return count
        except Exception as e:
            logger.warning(f"Failed to get page count for {path}: {e}")
            return 0

    def _extract_title(self, text: str) -> Optional[str]:
        lines = text.split('\n')
        for line in lines[:20]:
            line = line.strip()
            if line.startswith('# '):
                return line[2:].strip()
        for line in lines[:10]:
            line = line.strip()
            if line and len(line) > 0:
                return line
        return None

    def _get_vision_llm(self) -> Optional[BaseVisionLLM]:
        if self._vision_llm is not None:
            return self._vision_llm
        if self.settings is None:
            return None
        with self._vision_llm_lock:
            if self._vision_llm is not None:
                return self._vision_llm
            try:
                vision_cfg = self.settings.vision_llm if hasattr(self.settings, 'vision_llm') else None
                if vision_cfg and getattr(vision_cfg, 'enabled', False):
                    self._vision_llm = LLMFactory.create_vision_llm(self.settings)
                    logger.info("Vision LLM initialized for OCR fallback.")
                else:
                    logger.warning("Vision LLM not enabled, scanned PDF OCR will not be available.")
            except Exception as e:
                logger.error(f"Failed to initialize Vision LLM for OCR: {e}")
        return self._vision_llm

    def _vlm_ocr_pdf(self, pdf_path: Path) -> str:
        """Render PDF pages to images and run VLM OCR in parallel."""
        if not PYMUPDF_AVAILABLE:
            raise RuntimeError("PyMuPDF is required for VLM OCR on PDFs.")

        vision_llm = self._get_vision_llm()
        if vision_llm is None:
            raise RuntimeError("Vision LLM is not available for scanned PDF OCR.")

        doc = fitz.open(pdf_path)
        page_count = len(doc)

        # Render all pages to temporary PNG files
        page_images: List[tuple[int, str]] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            for page_num in range(page_count):
                page = doc[page_num]
                pix = page.get_pixmap(dpi=200)
                img_path = Path(tmpdir) / f"page_{page_num + 1}.png"
                pix.save(str(img_path))
                page_images.append((page_num, str(img_path)))
            doc.close()

            # Parallel OCR with controlled concurrency
            ocr_results: Dict[int, str] = {}

            def _ocr_page(page_num: int, img_path: str) -> tuple[int, str]:
                try:
                    image_input = ImageInput(path=img_path, mime_type="image/png")
                    response = vision_llm.chat_with_image(
                        text=VLM_OCR_PROMPT,
                        image=image_input,
                    )
                    return page_num, response.content.strip()
                except Exception as e:
                    logger.warning(f"OCR failed for page {page_num + 1} of {pdf_path}: {e}")
                    return page_num, ""

            max_workers = min(VLM_OCR_MAX_WORKERS, len(page_images))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(_ocr_page, pn, ip): pn for pn, ip in page_images
                }
                for future in as_completed(futures):
                    page_num, text = future.result()
                    ocr_results[page_num] = text

            # Assemble page texts in order
            assembled = []
            for page_num in range(page_count):
                page_text = ocr_results.get(page_num, "")
                if page_text:
                    assembled.append(f"\n--- Page {page_num + 1} ---\n{page_text}")

            return "\n".join(assembled).strip()

    def _extract_pdf_images(
        self,
        pdf_path: Path,
        text_content: str,
        doc_hash: str,
    ) -> tuple[str, List[Dict[str, Any]]]:
        """Extract images from a normal PDF and insert placeholders.

        Adapted from the previous PdfLoader implementation.
        """
        if not self.extract_images or not PYMUPDF_AVAILABLE:
            return text_content, []

        images_metadata = []
        modified_text = text_content

        try:
            image_dir = self.image_storage_dir / doc_hash
            image_dir.mkdir(parents=True, exist_ok=True)

            doc = fitz.open(pdf_path)

            for page_num in range(len(doc)):
                page = doc[page_num]
                image_list = page.get_images(full=True)

                for img_index, img_info in enumerate(image_list):
                    try:
                        xref = img_info[0]
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        image_ext = base_image["ext"]

                        image_id = self._generate_image_id(doc_hash, page_num + 1, img_index + 1)
                        image_filename = f"{image_id}.{image_ext}"
                        image_path = image_dir / image_filename

                        with open(image_path, "wb") as img_file:
                            img_file.write(image_bytes)

                        try:
                            img = Image.open(io.BytesIO(image_bytes))
                            width, height = img.size
                        except Exception:
                            width, height = 0, 0

                        placeholder = f"[IMAGE: {image_id}]"
                        insert_position = len(modified_text)
                        modified_text += f"\n{placeholder}\n"

                        try:
                            relative_path = image_path.relative_to(Path.cwd())
                        except ValueError:
                            relative_path = image_path.absolute()

                        images_metadata.append({
                            "id": image_id,
                            "path": str(relative_path),
                            "page": page_num + 1,
                            "text_offset": insert_position + 1,
                            "text_length": len(placeholder),
                            "position": {
                                "width": width,
                                "height": height,
                                "page": page_num + 1,
                                "index": img_index,
                            },
                        })
                    except Exception as e:
                        logger.warning(f"Failed to extract image {img_index} from page {page_num + 1}: {e}")
                        continue

            doc.close()

            if images_metadata:
                logger.info(f"Extracted {len(images_metadata)} images from {pdf_path}")
            return modified_text, images_metadata

        except Exception as e:
            logger.warning(f"Image extraction failed for {pdf_path}: {e}")
            return text_content, []

    @staticmethod
    def _generate_image_id(doc_hash: str, page: int, sequence: int) -> str:
        return f"{doc_hash[:8]}_{page}_{sequence}"
