"""全链路核心数据类型与契约定义。

本模块集中定义各阶段共享的数据结构：
- ingestion：加载、切块、变换、编码、存储。
- retrieval：查询预处理、检索、融合、重排。
- mcp_server：工具出参与响应封装。

设计原则：
- 契约集中：跨模块通过稳定类型交互，减少隐式耦合。
- 可序列化：统一支持 dict/JSON 转换。
- 元数据可扩展：在保留必填字段前提下允许业务扩展。
- 类型安全：完整注解，利于静态分析与重构。
"""

from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional


@dataclass
class Document:
    """原始文档对象（加载阶段输出，切块前输入）。

关键字段：
- id: 文档唯一标识（哈希或路径映射 ID）。
- text: 标准化文本内容（图片以 `[IMAGE:{id}]` 占位）。
- metadata: 文档级元数据，至少包含 `source_path`。

说明：
- `metadata.images` 可记录图片 ID、路径、页码和占位偏移，
  便于后续定位同图多次出现等场景。
    """
    
    id: str
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """校验必需元数据字段。"""
        if "source_path" not in self.metadata:
            raise ValueError("Document metadata must contain 'source_path'")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Document":
        """Create Document from dictionary."""
        return cls(**data)


@dataclass
class Chunk:
    """文本切块对象（切块阶段输出，变换阶段输入）。

关键约束：
- 通过 `source_path` / `source_ref` 保持可追溯性。
- `metadata.images` 仅建议保留当前 chunk 相关图片引用。
    """
    
    id: str
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    source_ref: Optional[str] = None
    
    def __post_init__(self):
        """校验必需元数据字段。"""
        if "source_path" not in self.metadata:
            raise ValueError("Chunk metadata must contain 'source_path'")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Chunk":
        """Create Chunk from dictionary."""
        return cls(**data)


@dataclass
class ChunkRecord:
    """可入库切块记录（变换/编码后产物）。

相较 `Chunk` 增加：
- dense_vector：稠密向量。
- sparse_vector：稀疏向量（如 BM25 权重映射）。

该结构通常直接用于向量库持久化与检索返回映射。
    """
    
    id: str
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    dense_vector: Optional[List[float]] = None
    sparse_vector: Optional[Dict[str, float]] = None
    
    def __post_init__(self):
        """校验必需元数据字段。"""
        if "source_path" not in self.metadata:
            raise ValueError("ChunkRecord metadata must contain 'source_path'")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChunkRecord":
        """Create ChunkRecord from dictionary."""
        return cls(**data)
    
    @classmethod
    def from_chunk(cls, chunk: Chunk, dense_vector: Optional[List[float]] = None,
                   sparse_vector: Optional[Dict[str, float]] = None) -> "ChunkRecord":
        """由 `Chunk` 与向量结果构建 `ChunkRecord`。"""
        return cls(
            id=chunk.id,
            text=chunk.text,
            metadata=chunk.metadata.copy(),
            dense_vector=dense_vector,
            sparse_vector=sparse_vector
        )


# Type aliases for convenience
Metadata = Dict[str, Any]
Vector = List[float]
SparseVector = Dict[str, float]


@dataclass
class ProcessedQuery:
    """查询预处理结果对象。

包含关键词抽取、过滤条件解析及可选扩展词，
供 dense/sparse/hybrid 检索器复用。
    """
    
    original_query: str
    keywords: List[str] = field(default_factory=list)
    filters: Dict[str, Any] = field(default_factory=dict)
    expanded_terms: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProcessedQuery":
        """Create ProcessedQuery from dictionary."""
        return cls(**data)


@dataclass
class RetrievalResult:
    """统一检索结果对象。

用于承接 dense/sparse/hybrid 各检索链路输出，
便于后续重排、评测与展示层处理。
    """
    
    chunk_id: str
    score: float
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """初始化后执行字段合法性校验。"""
        if not self.chunk_id:
            raise ValueError("chunk_id cannot be empty")
        if not isinstance(self.score, (int, float)):
            raise ValueError(f"score must be numeric, got {type(self.score).__name__}")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RetrievalResult":
        """Create RetrievalResult from dictionary."""
        return cls(**data)
