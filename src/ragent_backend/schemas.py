from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict, Annotated
from pydantic import BaseModel, Field
from langchain_core.messages import AnyMessage, HumanMessage, AIMessage, RemoveMessage
from langgraph.graph import add_messages
import uuid


# ============== API Models ==============

class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, description="User query")
    user_id: str = Field(default="1", description="User identifier")
    conversation_id: Optional[str] = Field(default=None)
    task_id: Optional[str] = Field(default=None)
    collection: Optional[str] = Field(default=None)
    top_k: int = Field(default=5, ge=1, le=20)


class ChatResponse(BaseModel):
    conversation_id: str
    task_id: str
    answer: str
    model_id: str


class RollbackRequest(BaseModel):
    target_message_id: str = Field(..., description="回溯边界消息 ID，删除该消息所在 turn 及之后的所有记录")


class IntentResult(BaseModel):
    """意图识别结果 — 三分类：clarify / rag / tool"""
    intent_type: Literal["clarify", "rag", "tool"] = "rag"
    confidence: float
    rewritten_query: str
    target_tool: Optional[str] = None      # tool 意图时指定目标工具
    tool_args: Optional[Dict[str, Any]] = None  # tool 意图时预解析参数
    need_clarify: bool = False
    clarify_prompt: Optional[str] = None
    reasoning: Optional[str] = None        # LLM 分类理由（可观测）


# ============== State Management ==============

def ensure_message_ids(messages: List[AnyMessage]) -> List[AnyMessage]:
    """
    确保所有消息都有 ID。RemoveMessage 依赖 m.id 来删除消息。
    如果消息没有 ID，会重新创建带 ID 的消息。
    """
    result = []
    for m in messages:
        if not hasattr(m, 'id') or m.id is None:
            # 根据消息类型重新创建带 ID 的消息
            if isinstance(m, HumanMessage):
                new_msg = HumanMessage(content=m.content, id=str(uuid.uuid4()))
            elif isinstance(m, AIMessage):
                new_msg = AIMessage(content=m.content, id=str(uuid.uuid4()))
            else:
                # 通用处理
                new_msg = type(m)(content=m.content, id=str(uuid.uuid4()))
            result.append(new_msg)
        else:
            result.append(m)
    return result


class RAGState(TypedDict, total=False):
    """
    RAG 工作流状态定义。
    
    关键设计：
    1. messages: 使用 Annotated + add_messages 管理消息列表
       - 支持追加新消息
       - 支持 RemoveMessage 删除旧消息（滑动窗口压缩）
    2. summary: 滚动摘要，当消息超出限制时合并更新
    3. _to_archive: 内部标记，本轮要归档到 MySQL 的消息（不存入 checkpoint）
    """
    
    # === 核心：给模型用的记忆（会被 checkpointer 自动管理）===
    messages: Annotated[List[AnyMessage], add_messages]
    summary: str
    
    # === 对话元数据 ===
    user_id: str
    task_id: str
    conversation_id: str
    
    # === 用户输入 ===
    query: str
    rewritten_query: str
    sub_queries: List[str]
    collection: Optional[str]
    top_k: int
    
    # === 意图识别 ===
    intent_confidence: float
    need_clarify: bool
    clarify_prompt: str
    
    # === 检索结果 ===
    retrieval_context: str
    retrieval_contexts: List[str]
    
    # === 生成结果 ===
    final_answer: str
    used_model: str
    
    # === 长期记忆（跨会话认知连续）===
    memories: List[str]
    
    # === 追踪 ===
    trace_events: List[Dict[str, Any]]
    
    # === 本轮标识（用于三层时间裁剪回滚）===
    current_turn_id: str
    
    # === 工具执行结果 ===
    tool_summary: str
    tool_execution_trace: List[Dict[str, Any]]
    
    # === 可用工具（动态注入）===
    available_tools: List[Dict[str, Any]]
    
    # === 内部临时状态（不会存入 checkpoint）===
    _to_archive: List[Dict[str, Any]]  # 本轮要归档的消息


class ArchivedMessage(BaseModel):
    """归档消息的数据结构（存入 MySQL）"""
    role: str
    content: str
    message_id: Optional[str] = None
    ts: float
