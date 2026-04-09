"""
Industrial RAG backend package based on FastAPI + LangGraph.

核心特性：
- 滑动窗口记忆管理（Rolling Memory）
- 分离的 Checkpoint（给模型）和 Archive（给用户）
- 异步归档到 MySQL
"""

from src.ragent_backend.schemas import ChatRequest, ChatResponse, RAGState
from src.ragent_backend.workflow import RAGWorkflow
from src.ragent_backend.store import ConversationArchiveStore, build_archive_store
from src.ragent_backend.memory_manager import RollingMemoryManager

__all__ = [
    "ChatRequest",
    "ChatResponse", 
    "RAGState",
    "RAGWorkflow",
    "ConversationArchiveStore",
    "build_archive_store",
    "RollingMemoryManager",
]

__version__ = "0.2.0"
