"""Text file loader for simple text documents."""

import hashlib
from pathlib import Path
from typing import Union

from src.core.types import Document
from src.libs.loader.base_loader import BaseLoader


class TextLoader(BaseLoader):
    """Simple text file loader.
    
    Supports .txt, .md, .csv files.
    """
    
    SUPPORTED_EXTENSIONS = {'.txt', '.md', '.csv', '.json', '.yaml', '.yml'}
    
    def _compute_file_hash(self, file_path: Path) -> str:
        """Compute SHA-256 hash of file content."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    
    def load(self, file_path: Union[str, Path]) -> Document:
        """Load a text file.
        
        Args:
            file_path: Path to the text file.
            
        Returns:
            Document with text content.
        """
        path = self._validate_file(file_path)
        
        if path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {path.suffix}")
        
        # Read text file
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        # Compute hash
        doc_hash = self._compute_file_hash(path)
        doc_id = f"doc_{doc_hash[:16]}"
        
        return Document(
            id=doc_id,
            text=content,
            metadata={
                "source_path": str(path),
                "filename": path.name,
                "file_type": path.suffix.lower(),
            }
        )
