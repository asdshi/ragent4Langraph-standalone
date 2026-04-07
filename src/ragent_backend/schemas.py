from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, description="User query")
    conversation_id: Optional[str] = Field(default=None)
    task_id: Optional[str] = Field(default=None)
    collection: Optional[str] = Field(default=None)
    top_k: int = Field(default=5, ge=1, le=20)


class ChatResponse(BaseModel):
    conversation_id: str
    task_id: str
    answer: str
    model_id: str


class IntentResult(BaseModel):
    rewritten_query: str
    confidence: float
    need_clarify: bool
    clarify_prompt: Optional[str] = None


class RAGState(TypedDict, total=False):
    task_id: str
    conversation_id: str
    query: str
    rewritten_query: str
    sub_queries: List[str]
    collection: Optional[str]
    top_k: int
    recent_history: List[Dict[str, Any]]
    memory_summary: str
    long_term_memory: List[Dict[str, Any]]
    intent_confidence: float
    need_clarify: bool
    clarify_prompt: str
    retrieval_context: str
    retrieval_contexts: List[str]
    final_answer: str
    used_model: str
    trace_events: List[Dict[str, Any]]
