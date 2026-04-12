"""
RAG 工作流 - 滑动窗口记忆版本

核心改进：
1. 使用 Annotated + add_messages 管理消息列表
2. 使用 RemoveMessage 实现滑动窗口压缩
3. 滚动摘要：旧消息合并到 summary 中
4. 分离 checkpoint（给模型）和 MySQL（给用户）
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, AIMessage, RemoveMessage, AnyMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, START, END

from src.ragent_backend.schemas import RAGState, ensure_message_ids
from src.ragent_backend.memory_manager import RollingMemoryManager
from src.ragent_backend.store import ConversationArchiveStore
from src.ragent_backend.intent import detect_intent, split_parallel_subqueries
from src.mcp_server.tools.query_knowledge_hub import QueryKnowledgeHubTool


class RAGWorkflow:
    """
    RAG 工作流实现
    
    节点流程：
    session -> intent -> retrieve -> generate -> memory_manage -> archive -> END
    
    记忆管理：
    - messages 使用 Annotated[list, add_messages] 管理
    - 超出 max_messages 时，使用 RemoveMessage 删除旧消息
    - 被删除的消息合并到 summary 中
    - 所有消息（包括本轮）异步归档到 MySQL
    """
    
    def __init__(
        self,
        store: ConversationArchiveStore,
        llm: Any,
        checkpointer: Any = None,
        max_messages: int = 20,
        keep_recent: int = 4,
    ) -> None:
        self._store = store
        self._llm = llm
        self._checkpointer = checkpointer
        self._memory_manager = RollingMemoryManager(
            max_messages=max_messages,
            keep_recent=keep_recent
        )
        # 初始化 RAG 检索工具
        self._retrieval_tool = QueryKnowledgeHubTool()
        self._compiled = self._build_graph()

    def _build_graph(self):
        """构建工作流图"""
        from langgraph.types import Command
        self._command_cls = Command

        graph = StateGraph(RAGState)

        # 添加节点
        graph.add_node("session", self._session_node)
        graph.add_node("intent", self._intent_node)
        graph.add_node("retrieve", self._retrieve_node)
        graph.add_node("generate", self._generate_node)
        graph.add_node("memory_manage", self._memory_manage_node)
        graph.add_node("archive", self._archive_node)

        # 添加边
        graph.add_edge(START, "session")
        graph.add_edge("session", "intent")
        graph.add_edge("intent", "retrieve")
        graph.add_edge("retrieve", "generate")
        graph.add_edge("generate", "memory_manage")
        graph.add_edge("memory_manage", "archive")
        graph.add_edge("archive", END)

        return graph.compile(checkpointer=self._checkpointer)

    async def run(
        self, 
        initial_state: Dict[str, Any], 
        thread_id: str
    ) -> RAGState:
        """
        运行工作流
        
        Args:
            initial_state: 初始状态，必须包含 query
            thread_id: 对话 ID（用于 checkpoint 加载）
        """
        config = {"configurable": {"thread_id": thread_id}}
        
        # 添加用户输入到 messages
        user_message = HumanMessage(content=initial_state["query"])
        initial_state.setdefault("messages", []).append(user_message)
        
        return await self._compiled.ainvoke(initial_state, config)

    async def _session_node(self, state: RAGState) -> Dict[str, Any]:
        """
        会话初始化节点
        
        注意：
        - LangGraph 会自动从 checkpointer 加载 messages 和 summary
        - 这里只需要确保 conversation_id 存在
        - 确保所有消息都有 ID（RemoveMessage 依赖）
        """
        # 确保 conversation_id
        if not state.get("conversation_id"):
            state["conversation_id"] = str(uuid.uuid4())
        
        # 确保 task_id
        if not state.get("task_id"):
            state["task_id"] = str(uuid.uuid4())
        
        # 确保所有消息都有 ID（关键！）
        if state.get("messages"):
            state["messages"] = ensure_message_ids(state["messages"])
        
        # 初始化默认值
        state.setdefault("messages", [])
        state.setdefault("summary", "")
        state.setdefault("trace_events", []).append(
            {"node": "session", "ts": time.time(), "ok": True}
        )
        
        return state

    async def _intent_node(self, state: RAGState) -> Any:
        """意图识别节点"""
        query = state["query"]
        has_history = len(state.get("messages", [])) > 1
        
        # 意图识别
        intent = detect_intent(query, has_history=has_history)
        sub_queries = split_parallel_subqueries(intent.rewritten_query)
        
        update = {
            "rewritten_query": intent.rewritten_query,
            "sub_queries": sub_queries,
            "intent_confidence": intent.confidence,
            "need_clarify": intent.need_clarify,
            "clarify_prompt": intent.clarify_prompt or "",
            "trace_events": [
                *state.get("trace_events", []),
                {
                    "node": "intent",
                    "ts": time.time(),
                    "confidence": intent.confidence,
                    "need_clarify": intent.need_clarify,
                    "sub_query_count": len(sub_queries),
                }
            ],
        }
        
        # 如果需要澄清，直接跳过后续节点
        if intent.need_clarify:
            update["final_answer"] = intent.clarify_prompt or "请补充更多信息。"
            update["used_model"] = "intent-shortcircuit"
            return self._command_cls(update=update, goto="archive")
        
        return update

    async def _retrieve_node(self, state: RAGState) -> Dict[str, Any]:
        """检索节点 - 接入真实的 RAG MCP 检索"""
        query = state.get("rewritten_query") or state["query"]
        conversation_id = state["conversation_id"]
        # 构建对话级 collection 名称
        collection = f"conv_{conversation_id}"
        
        try:
            # 调用 RAG MCP 检索工具
            retrieval_result = await self._retrieval_tool.execute(
                query=query,
                collection=collection,
                top_k=state.get("top_k", 5),
            )
            
            # retrieval_result 是 MCPToolResponse 对象
            context_text = retrieval_result.content
            
            return {
                "retrieval_context": context_text,
                "retrieval_contexts": [context_text],
                "trace_events": [
                    *state.get("trace_events", []),
                    {
                        "node": "retrieve", 
                        "ts": time.time(), 
                        "ok": True, 
                        "collection": collection,
                        "result_count": retrieval_result.metadata.get("result_count", 0) if hasattr(retrieval_result, "metadata") else 0,
                    }
                ],
            }
        except Exception as e:
            # 检索失败时返回提示，不中断工作流
            print(f"[Retrieve] Error: {e}")
            return {
                "retrieval_context": "该对话暂无文件或检索服务暂时不可用。",
                "retrieval_contexts": [],
                "trace_events": [
                    *state.get("trace_events", []),
                    {"node": "retrieve", "ts": time.time(), "ok": False, "error": str(e)}
                ],
            }

    async def _generate_node(self, state: RAGState) -> Dict[str, Any]:
        """生成回复节点"""
        
        # 构建 prompt
        prompt = self._build_prompt(state)
        
        # 调用 LLM
        try:
            response = await self._llm.ainvoke([HumanMessage(content=prompt)])
            answer = response.content
            model_name = getattr(self._llm, "model_name", "unknown")
        except Exception as e:
            answer = f"生成失败：{str(e)}"
            model_name = "error"
        
        # 添加助手回复到 messages
        assistant_message = AIMessage(content=answer)
        
        return {
            "messages": [assistant_message],  # add_messages 会追加
            "final_answer": answer,
            "used_model": model_name,
            "trace_events": [
                *state.get("trace_events", []),
                {"node": "generate", "ts": time.time(), "model": model_name}
            ],
        }

    def _build_prompt(self, state: RAGState) -> str:
        """构建生成 prompt"""
        
        # 格式化最近对话历史（仅用于展示，实际 history 通过 messages 传递）
        recent_history = self._format_recent_messages(state.get("messages", []))
        
        prompt = ChatPromptTemplate.from_template("""你是企业级知识库助手，基于检索结果和对话历史回答用户问题。

【历史摘要】
{summary}

【最近对话】
{recent_history}

【检索上下文】
{context}

【用户问题】
{query}

请给出准确、有用的回答：""")

        return prompt.format(
            summary=state.get("summary", ""),
            recent_history=recent_history,
            context=state.get("retrieval_context", ""),
            query=state.get("query", ""),
        )

    def _format_recent_messages(self, messages: List[AnyMessage]) -> str:
        """格式化最近的消息为文本"""
        return "\n".join([
            f"User: {m.content}" if isinstance(m, HumanMessage) else f"Assistant: {m.content}"
            for m in messages[-6:]  # 最近3轮（6条消息）
        ])

    async def _memory_manage_node(self, state: RAGState) -> Dict[str, Any]:
        """
        记忆管理节点
        
        核心逻辑：
        1. 检查消息数量是否超出限制
        2. 如果超出，使用 RemoveMessage 删除旧消息
        3. 将删除的消息合并到 summary 中
        4. 标记待归档的消息供 archive 节点使用
        """
        messages = state.get("messages", [])
        
        # 检查结果
        result = {
            "_to_archive": [],  # 待归档的消息
        }
        
        # 检查是否需要压缩
        if not self._memory_manager.should_compact(messages):
            # 不需要压缩，但本轮新消息仍需归档
            # archive 节点会处理
            return result
        
        # 执行压缩
        to_keep, new_summary, archived_data = await self._memory_manager.compact(
            messages=messages,
            current_summary=state.get("summary", ""),
            llm=self._llm
        )
        
        # 生成 RemoveMessage 操作（关键！）
        keep_ids = {m.id for m in to_keep}
        delete_ops = [
            RemoveMessage(id=m.id)
            for m in messages
            if m.id not in keep_ids
        ]
        
        print(f"[MemoryManage] Compacting: {len(messages)} -> {len(to_keep)} messages, "
              f"archived {len(archived_data)}, summary length {len(new_summary)}")
        
        return {
            "messages": delete_ops,           # LangGraph 会处理删除
            "summary": new_summary,           # 更新摘要
            "_to_archive": archived_data,     # 标记待归档
        }

    async def _archive_node(self, state: RAGState) -> Dict[str, Any]:
        """
        归档节点
        
        总是运行，负责：
        1. 将被压缩的消息归档到 MySQL
        2. 将本轮新消息归档到 MySQL
        
        使用 asyncio.create_task 异步执行，不阻塞响应
        """
        conversation_id = state["conversation_id"]
        
        # 1. 获取被压缩的消息（如果有）
        archived = state.pop("_to_archive", [])
        
        # 2. 准备本轮的新消息（从 messages 中提取本轮的对话）
        messages = state.get("messages", [])
        current_turn_msgs = []
        
        # 本轮最后两条应该是 user query 和 assistant answer
        if len(messages) >= 2:
            for m in messages[-2:]:
                current_turn_msgs.append({
                    "role": "user" if isinstance(m, HumanMessage) else "assistant",
                    "content": m.content,
                    "message_id": m.id,
                    "ts": time.time()
                })
        
        # 3. 合并：压缩的消息 + 本轮消息
        all_to_archive = archived + current_turn_msgs
        
        # 4. 异步保存（添加异常处理回调）
        if all_to_archive:
            task = asyncio.create_task(
                self._store.append_to_history(conversation_id, all_to_archive)
            )
            
            # 添加完成回调，处理异常
            def on_done(t):
                try:
                    t.result()
                    print(f"[Archive] Saved {len(all_to_archive)} messages for {conversation_id}")
                except Exception as e:
                    print(f"[Archive] Failed to save history: {e}")
            
            task.add_done_callback(on_done)
        
        # 添加追踪事件
        state.setdefault("trace_events", []).append(
            {"node": "archive", "ts": time.time(), "ok": True, "archived_count": len(all_to_archive)}
        )
        
        return {}

    def get_memory_stats(self, state: RAGState) -> Dict:
        """获取记忆统计信息"""
        return self._memory_manager.get_stats(
            state.get("messages", []),
            state.get("summary", "")
        )
