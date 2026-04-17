"""Dense Encoder for generating embeddings from text chunks.

This module implements the Dense Encoder component of the Ingestion Pipeline,
responsible for converting text chunks into dense vector representations using
configurable embedding providers.

Design Principles:
- Config-Driven: Uses factory pattern to obtain embedding provider from settings
- Batch Processing: Optimizes API calls through batching
- Observable: Accepts TraceContext for future observability integration
- Error Handling: Individual failures shouldn't crash entire batch
- Deterministic: Same inputs produce same outputs
"""

from typing import List, Optional, Any
from src.core.types import Chunk
from src.libs.embedding.base_embedding import BaseEmbedding


class DenseEncoder:
    """Encodes text chunks into dense vectors using BaseEmbedding provider.
    
    This encoder acts as a bridge between the ingestion pipeline and the
    pluggable embedding layer. It handles batching, error recovery, and
    maintains alignment between input chunks and output vectors.
    
    Design:
    - Dependency Injection: Receives BaseEmbedding instance (no direct factory call)
    - Batch-First: Processes all chunks in configurable batch sizes
    - Stateless: No internal state between encode() calls
    
    Example:
        >>> from src.libs.embedding.embedding_factory import EmbeddingFactory
        >>> from src.core.settings import load_settings
        >>> 
        >>> settings = load_settings("config/settings.yaml")
        >>> embedding = EmbeddingFactory.create(settings)
        >>> encoder = DenseEncoder(embedding, batch_size=32)
        >>> 
        >>> chunks = [Chunk(id="1", text="Hello world", metadata={})]
        >>> vectors = encoder.encode(chunks)
        >>> print(len(vectors))  # 1
        >>> print(len(vectors[0]))  # dimension (e.g., 1536)
    """
    
    def __init__(
        self,
        embedding: BaseEmbedding,
        batch_size: int = 100,
    ):
        """Initialize DenseEncoder.
        
        Args:
            embedding: Embedding provider instance (from EmbeddingFactory)
            batch_size: Number of chunks to process per API call (default: 100)
        
        Raises:
            ValueError: If batch_size <= 0
        """
        if batch_size <= 0:
            raise ValueError(f"batch_size must be positive, got {batch_size}")
        
        self.embedding = embedding
        self.batch_size = batch_size
    
    def encode(
        self,
        chunks: List[Chunk],
        trace: Optional[Any] = None,
    ) -> List[List[float]]:
        """Encode chunks into dense vectors.
        
        This method:
        1. Extracts text from each chunk
        2. Batches texts according to batch_size
        3. Calls embedding.embed() for each batch
        4. Concatenates results maintaining chunk order
        
        Args:
            chunks: List of Chunk objects to encode
            trace: Optional TraceContext for observability (reserved for Stage F)
        
        Returns:
            List of dense vectors (one per chunk, in same order).
            Each vector is a list of floats with dimension matching the embedding model.
        
        Raises:
            ValueError: If chunks list is empty
            RuntimeError: If embedding provider fails for all batches
        
        Example:
            >>> chunks = [
            ...     Chunk(id="1", text="First chunk", metadata={}),
            ...     Chunk(id="2", text="Second chunk", metadata={})
            ... ]
            >>> vectors = encoder.encode(chunks)
            >>> len(vectors) == len(chunks)  # True
        """
        if not chunks:
            raise ValueError("Cannot encode empty chunks list")
        
        # Extract text from chunks
        texts = [chunk.text for chunk in chunks]
        
        # Validate that all texts are non-empty
        for i, text in enumerate(texts):
            if not text or not text.strip():
                raise ValueError(
                    f"Chunk at index {i} (id={chunks[i].id}) has empty or whitespace-only text"
                )
        
        # Process in batches
        all_vectors: List[List[float]] = []
        
        for batch_start in range(0, len(texts), self.batch_size):
            batch_end = min(batch_start + self.batch_size, len(texts))
            batch_texts = texts[batch_start:batch_end]
            
            try:
                # Call embedding provider
                batch_vectors = self.embedding.embed(
                    texts=batch_texts,
                    trace=trace,
                )
                
                # Validate output shape
                if len(batch_vectors) != len(batch_texts):
                    raise RuntimeError(
                        f"Embedding provider returned {len(batch_vectors)} vectors "
                        f"for {len(batch_texts)} texts in batch {batch_start}-{batch_end}"
                    )
                
                all_vectors.extend(batch_vectors)
                
            except Exception as batch_err:
                # Fallback: encode one-by-one with retry for transient API failures
                import time
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(
                    f"Batch embedding failed for batch {batch_start}-{batch_end} ({len(batch_texts)} chunks). "
                    f"Falling back to single-chunk encoding. Error: {batch_err}"
                )
                
                for idx, text in enumerate(batch_texts):
                    chunk_idx = batch_start + idx
                    for attempt in range(3):
                        try:
                            single_vector = self.embedding.embed(texts=[text], trace=trace)
                            if len(single_vector) != 1:
                                raise RuntimeError(
                                    f"Expected 1 vector for single text, got {len(single_vector)}"
                                )
                            all_vectors.append(single_vector[0])
                            break
                        except Exception as single_err:
                            if attempt < 2:
                                wait = 2 ** attempt
                                logger.warning(
                                    f"Single-chunk embedding failed for chunk {chunk_idx} "
                                    f"(attempt {attempt + 1}/3). Retrying in {wait}s... Error: {single_err}"
                                )
                                time.sleep(wait)
                            else:
                                raise RuntimeError(
                                    f"Failed to encode chunk {chunk_idx} after 3 attempts: {single_err}"
                                ) from single_err
        
        # Final validation
        if len(all_vectors) != len(chunks):
            raise RuntimeError(
                f"Vector count mismatch: got {len(all_vectors)} vectors "
                f"for {len(chunks)} chunks"
            )
        
        # Validate vector dimensions are consistent
        if all_vectors:
            expected_dim = len(all_vectors[0])
            for i, vec in enumerate(all_vectors):
                if len(vec) != expected_dim:
                    raise RuntimeError(
                        f"Inconsistent vector dimensions: vector {i} has "
                        f"{len(vec)} dimensions, expected {expected_dim}"
                    )
        
        return all_vectors
    
    def get_batch_count(self, num_chunks: int) -> int:
        """Calculate number of batches needed for given chunk count.
        
        Utility method for logging/progress tracking.
        
        Args:
            num_chunks: Number of chunks to encode
        
        Returns:
            Number of batches required
        """
        if num_chunks <= 0:
            return 0
        return (num_chunks + self.batch_size - 1) // self.batch_size
