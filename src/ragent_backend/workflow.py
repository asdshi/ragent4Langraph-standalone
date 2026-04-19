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
from typing import Any, AsyncGenerator, Dict, List, Optional

from langchain_core.messages import HumanMessage, AIMessage, RemoveMessage, AnyMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, START, END

from src.ragent_backend.schemas import RAGState, ensure_message_ids
from src.ragent_backend.memory_manager import RollingMemoryManager
from src.ragent_backend.store import ConversationArchiveStore
from src.ragent_backend.intent import detect_intent, analyze_query
from src.ragent_backend.ltm_store import LTMStore
from src.mcp_server.tools.query_knowledge_hub import QueryKnowledgeHubTool
from src.tool_agent.tool_registry import ToolRegistry, get_default_registry
from src.tool_agent.subgraph import build_tool_subgraph


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
        ltm_store: Optional[LTMStore] = None,
        tool_registry: Optional[ToolRegistry] = None,
    ) -> None:
        self._store = store
        self._llm = llm
        self._checkpointer = checkpointer
        self._ltm_store = ltm_store
        self._token_queue: Optional[asyncio.Queue[str]] = None
        self._trace_queue: Optional[asyncio.Queue[Dict[str, Any]]] = None
        self._memory_manager = RollingMemoryManager(
            max_messages=max_messages,
            keep_recent=keep_recent
        )
        # 初始化 RAG 检索工具
        self._retrieval_tool = QueryKnowledgeHubTool()
        # 工具注册表（可外部传入，或使用默认全局实例）
        self._tool_registry = tool_registry or get_default_registry()
        self._compiled = self._build_graph()

    def _build_graph(self):
        """构建工作流图（三分支：clarify / rag / tool）"""
        graph = StateGraph(RAGState)

        # 添加主图节点
        graph.add_node("session", self._session_node)
        graph.add_node("intent", self._intent_node)
        graph.add_node("clarify", self._clarify_node)
        graph.add_node("retrieve", self._retrieve_node)
        graph.add_node("generate", self._generate_node)
        graph.add_node("memory_manage", self._memory_manage_node)
        graph.add_node("archive", self._archive_node)

        # 添加工具子图节点（子图编译后作为一个节点）
        if self._llm is not None:
            tool_subgraph = build_tool_subgraph(
                tool_registry=self._tool_registry,
                llm=self._llm,
                max_iterations=5,
            )
            graph.add_node("tool_subgraph", tool_subgraph)

        # 添加边
        graph.add_edge(START, "session")
        graph.add_edge("session", "intent")
        graph.add_conditional_edges(
            "intent",
            self._route_after_intent,
            {"clarify": "clarify", "retrieve": "retrieve", "tool_subgraph": "tool_subgraph"}
        )
        # 分支路由：rag/tool 需要 generate，clarify 直接跳过
        graph.add_edge("retrieve", "generate")
        if self._llm is not None:
            graph.add_edge("tool_subgraph", "generate")
        graph.add_edge("generate", "memory_manage")
        # clarify 直接到 memory_manage（跳过 generate，避免重复生成）
        graph.add_edge("clarify", "memory_manage")
        graph.add_edge("memory_manage", "archive")
        graph.add_edge("archive", END)

        return graph.compile(checkpointer=self._checkpointer)

    def _route_after_intent(self, state: RAGState) -> str:
        """根据意图判断结果决定下一步走向（三分支）"""
        intent_type = state.get("intent_type", "rag")
        if intent_type == "clarify" or state.get("need_clarify"):
            return "clarify"
        if intent_type == "tool":
            # 如果 LLM 不可用，无法运行 tool_subgraph，回退到 retrieve
            if self._llm is None:
                return "retrieve"
            return "tool_subgraph"
        return "retrieve"

    def _emit_trace(
        self,
        node: str,
        step: str,
        status: str = "running",
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """将 trace 事件推送到 trace_queue（仅在流式模式下）"""
        if self._trace_queue is not None:
            asyncio.create_task(
                self._trace_queue.put({
                    "type": "trace",
                    "node": node,
                    "step": step,
                    "status": status,
                    "payload": payload or {},
                    "ts": time.time(),
                })
            )

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

    async def run_stream(
        self,
        initial_state: Dict[str, Any],
        thread_id: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        流式运行工作流。
        
        在 generate 节点内部通过 llm.astream() 实时产生 token，
        并通过 asyncio.Queue 逐 token yield 给调用方。
        
        Yields:
            {"type": "token", "content": str}
            {"type": "done", "state": RAGState}
        """
        config = {"configurable": {"thread_id": thread_id}}
        
        # 添加用户输入到 messages
        user_message = HumanMessage(content=initial_state["query"])
        initial_state.setdefault("messages", []).append(user_message)
        
        self._token_queue = asyncio.Queue()
        self._trace_queue = asyncio.Queue()
        graph_task = asyncio.create_task(self._compiled.ainvoke(initial_state, config))
        token_yielded = False
        
        try:
            while True:
                if graph_task.done():
                    # 清空剩余 trace
                    while not self._trace_queue.empty():
                        yield self._trace_queue.get_nowait()
                    # 清空剩余 token
                    while not self._token_queue.empty():
                        token = self._token_queue.get_nowait()
                        yield {"type": "token", "content": token}
                        token_yielded = True
                    break
                
                token_task = asyncio.create_task(self._token_queue.get())
                trace_task = asyncio.create_task(self._trace_queue.get())
                done, pending = await asyncio.wait(
                    [graph_task, token_task, trace_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                
                for t in pending:
                    if t is graph_task:
                        continue
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
                
                if token_task in done:
                    try:
                        token = token_task.result()
                        yield {"type": "token", "content": token}
                        token_yielded = True
                    except asyncio.CancelledError:
                        pass
                
                if trace_task in done:
                    try:
                        yield trace_task.result()
                    except asyncio.CancelledError:
                        pass
            
            final_state = await graph_task
            
            # 兜底：如果 generate 节点被跳过（如 need_clarify）或出错未吐 token，
            # 直接把 final_answer 作为 token 吐出，避免前端空屏卡死
            if not token_yielded and final_state.get("final_answer"):
                yield {"type": "token", "content": final_state["final_answer"]}
            
            yield {"type": "done", "state": final_state}
        finally:
            self._token_queue = None
            self._trace_queue = None
            if graph_task and not graph_task.done():
                graph_task.cancel()
                try:
                    await graph_task
                except asyncio.CancelledError:
                    pass

    async def _session_node(self, state: RAGState) -> Dict[str, Any]:
        """
        会话初始化节点
        
        注意：
        - LangGraph 会自动从 checkpointer 加载 messages 和 summary
        - 这里只需要确保 conversation_id 存在
        - 确保所有消息都有 ID（RemoveMessage 依赖）
        """
        self._emit_trace("session", "node_start", "running")
        
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
        state.setdefault("memories", [])
        # 每轮都生成新的 turn_id，用于后续三层时间裁剪回滚
        state["current_turn_id"] = str(uuid.uuid4())
        
        # 召回长期记忆（跨会话认知连续）
        if self._ltm_store and state.get("user_id"):
            try:
                query = state.get("rewritten_query") or state.get("query", "")
                memories = await self._ltm_store.retrieve_facts(
                    user_id=state["user_id"],
                    query=query,
                    top_k=3,
                )
                if memories:
                    state["memories"] = memories
                    print(f"[Session] Recalled {len(memories)} LTM facts for user {state['user_id']}")
            except Exception as e:
                print(f"[Session] LTM recall failed: {e}")
        
        state.setdefault("trace_events", []).append(
            {"node": "session", "ts": time.time(), "ok": True}
        )
        
        self._emit_trace("session", "node_end", "success")
        return state

    async def _intent_node(self, state: RAGState) -> Any:
        """意图识别节点：结构化 LLM 一次完成指代消解 + 子查询拆分 + 三分类"""
        self._emit_trace("intent", "node_start", "running")
        
        query = state["query"]
        messages = state.get("messages", [])
        
        # 单次结构化调用：重写 + 拆分
        self._emit_trace("intent", "query_rewrite", "running", {"original_query": query})
        try:
            analysis = await analyze_query(
                query=query,
                messages=messages,
                llm=self._llm
            )
            rewritten_query = analysis.rewritten_query
            sub_queries = analysis.sub_queries
        except Exception as e:
            print(f"[Intent] Structured analysis failed: {e}")
            rewritten_query = query
            sub_queries = [query]
        
        # 意图识别（三分支：clarify / rag / tool）
        self._emit_trace("intent", "intent_detect", "running")
        # 从注册表获取可用工具 schema，供 LLM 判断 tool 意图
        available_tools = self._tool_registry.to_openai_tools() if self._tool_registry else []
        intent = await detect_intent(
            rewritten_query=rewritten_query,
            llm=self._llm,
            available_tools=available_tools,
        )
        self._emit_trace("intent", "intent_detect", "success", {
            "intent_type": intent.intent_type,
            "confidence": intent.confidence,
            "need_clarify": intent.need_clarify,
            "target_tool": intent.target_tool,
        })
        
        # 如果 intent_type=tool，以 detect_intent 的结果为准（可能覆盖 analyze_query 的 sub_queries）
        if intent.intent_type == "tool":
            sub_queries = [intent.rewritten_query]
            self._emit_trace("intent", "tool_intent", "running", {
                "target_tool": intent.target_tool or "",
                "reasoning": intent.reasoning or "",
            })
        elif intent.need_clarify:
            sub_queries = [intent.rewritten_query]
            self._emit_trace("intent", "clarify_shortcircuit", "running", {
                "clarify_prompt": intent.clarify_prompt or "",
            })
        
        update = {
            "rewritten_query": rewritten_query,
            "sub_queries": sub_queries,
            "intent_type": intent.intent_type,
            "intent_confidence": intent.confidence,
            "need_clarify": intent.need_clarify,
            "clarify_prompt": intent.clarify_prompt or "",
            "target_tool": intent.target_tool,
            "tool_args": intent.tool_args,
            "available_tools": available_tools,
            "trace_events": [
                *state.get("trace_events", []),
                {
                    "node": "intent",
                    "ts": time.time(),
                    "intent_type": intent.intent_type,
                    "confidence": intent.confidence,
                    "need_clarify": intent.need_clarify,
                    "target_tool": intent.target_tool,
                    "sub_query_count": len(sub_queries),
                    "rewritten": rewritten_query != query,
                    "original_query": query,
                    "reasoning": intent.reasoning,
                }
            ],
        }
        
        # 如果需要澄清，提前设置 final_answer
        if intent.need_clarify:
            update["final_answer"] = intent.clarify_prompt or "请补充更多信息。"
            update["used_model"] = "intent-shortcircuit"
        
        self._emit_trace("intent", "node_end", "success", {
            "intent_type": intent.intent_type,
            "need_clarify": intent.need_clarify,
            "sub_query_count": len(sub_queries),
        })
        return update

    async def _clarify_node(self, state: RAGState) -> Dict[str, Any]:
        """澄清节点：当意图为 clarify 时，生成澄清提示并准备进入 generate。"""
        self._emit_trace("clarify", "node_start", "running")
        
        clarify_prompt = state.get("clarify_prompt", "请补充更多信息。")
        
        self._emit_trace("clarify", "node_end", "success", {
            "clarify_prompt": clarify_prompt,
        })
        return {
            "final_answer": clarify_prompt,
            "used_model": "intent-clarify",
            "trace_events": [
                *state.get("trace_events", []),
                {"node": "clarify", "ts": time.time(), "ok": True}
            ],
        }

    async def _retrieve_node(self, state: RAGState) -> Dict[str, Any]:
        """检索节点 - 接入真实的 RAG MCP 检索"""
        self._emit_trace("retrieve", "node_start", "running")
        
        query = state.get("rewritten_query") or state["query"]
        conversation_id = state["conversation_id"]
        # 构建对话级 collection 名称
        collection = f"conv_{conversation_id}"
        
        self._emit_trace("retrieve", "knowledge_retrieval", "running", {
            "query": query,
            "collection": collection,
            "top_k": state.get("top_k", 5),
        })
        
        try:
            # 调用 RAG MCP 检索工具
            retrieval_result = await self._retrieval_tool.execute(
                query=query,
                collection=collection,
                top_k=state.get("top_k", 5),
            )
            
            # retrieval_result 是 MCPToolResponse 对象
            context_text = retrieval_result.content
            
            self._emit_trace("retrieve", "knowledge_retrieval", "success", {
                "collection": collection,
                "result_count": retrieval_result.metadata.get("result_count", 0) if hasattr(retrieval_result, "metadata") else 0,
            })
            self._emit_trace("retrieve", "node_end", "success")
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
            self._emit_trace("retrieve", "knowledge_retrieval", "error", {"error": str(e)})
            self._emit_trace("retrieve", "node_end", "error")
            return {
                "retrieval_context": "该对话暂无文件或检索服务暂时不可用。",
                "retrieval_contexts": [],
                "trace_events": [
                    *state.get("trace_events", []),
                    {"node": "retrieve", "ts": time.time(), "ok": False, "error": str(e)}
                ],
            }

    async def _generate_node(self, state: RAGState) -> Dict[str, Any]:
        """生成回复节点（支持内部流式输出）"""
        self._emit_trace("generate", "node_start", "running")
        
        # 构建 prompt
        self._emit_trace("generate", "prompt_build", "running")
        prompt = self._build_prompt(state)
        self._emit_trace("generate", "prompt_build", "success", {"prompt_length": len(prompt)})
        
        # 调用 LLM（流式收集，同时透传 token）
        self._emit_trace("generate", "llm_stream", "running")
        try:
            chunks = []
            async for chunk in self._llm.astream([HumanMessage(content=prompt)]):
                chunks.append(chunk)
                if self._token_queue is not None:
                    await self._token_queue.put(chunk.content)
            
            answer = "".join(c.content for c in chunks)
            model_name = getattr(self._llm, "model_name", "unknown")
            self._emit_trace("generate", "llm_stream", "success", {
                "model": model_name,
                "token_count": len(chunks),
            })
        except Exception as e:
            answer = f"生成失败：{str(e)}"
            model_name = "error"
            self._emit_trace("generate", "llm_stream", "error", {"error": str(e)})
        
        # 添加助手回复到 messages
        assistant_message = AIMessage(content=answer)
        
        self._emit_trace("generate", "node_end", "success" if model_name != "error" else "error")
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
        
        # 判断是否有工具执行结果需要注入
        tool_summary = state.get("tool_summary", "")
        tool_section = ""
        if tool_summary:
            tool_section = f"""
【工具执行结果】
{tool_summary}
"""
        
        prompt = ChatPromptTemplate.from_template("""你是企业级知识库助手，基于检索结果、工具执行结果、对话历史和用户长期记忆回答用户问题。

【用户长期记忆】
{memories}

【历史摘要】
{summary}

【最近对话】
{recent_history}
{tool_section}
【检索上下文】
{context}

【用户问题】
{query}

请给出准确、有用的回答：""")

        memories_text = "\n".join(f"- {m}" for m in state.get("memories", [])) or "无"
        return prompt.format(
            memories=memories_text,
            summary=state.get("summary", ""),
            recent_history=recent_history,
            tool_section=tool_section,
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
        self._emit_trace("memory_manage", "node_start", "running")
        messages = state.get("messages", [])
        
        # 检查结果
        result = {
            "_to_archive": [],  # 待归档的消息
        }
        
        # 检查是否需要压缩
        self._emit_trace("memory_manage", "compact_check", "running", {"message_count": len(messages)})
        if not self._memory_manager.should_compact(messages):
            # 不需要压缩，但本轮新消息仍需归档
            # archive 节点会处理
            self._emit_trace("memory_manage", "compact_check", "success", {"need_compact": False})
            self._emit_trace("memory_manage", "node_end", "success")
            return result
        
        self._emit_trace("memory_manage", "compact_check", "success", {"need_compact": True})
        
        # 执行压缩
        self._emit_trace("memory_manage", "memory_compact", "running")
        try:
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
            
            self._emit_trace("memory_manage", "memory_compact", "success", {
                "before_count": len(messages),
                "after_count": len(to_keep),
                "archived_count": len(archived_data),
            })
            self._emit_trace("memory_manage", "node_end", "success")
            
            return {
                "messages": delete_ops,           # LangGraph 会处理删除
                "summary": new_summary,           # 更新摘要
                "_to_archive": archived_data,     # 标记待归档
            }
        except Exception as e:
            print(f"[MemoryManage] Compact failed: {e}")
            self._emit_trace("memory_manage", "memory_compact", "error", {"error": str(e)})
            self._emit_trace("memory_manage", "node_end", "error")
            return result

    async def _archive_node(self, state: RAGState) -> Dict[str, Any]:
        """
        归档节点
        
        总是运行，负责：
        1. 将被压缩的消息归档到 MySQL
        2. 将本轮新消息归档到 MySQL
        3. 从本轮 Q&A 中提取长期记忆（LTM）
        
        使用 asyncio.create_task 异步执行，不阻塞响应
        """
        self._emit_trace("archive", "node_start", "running")
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
        
        turn_id = state.get("current_turn_id")
        
        # 4. 异步保存（添加异常处理回调）
        if all_to_archive:
            task = asyncio.create_task(
                self._store.append_to_history(conversation_id, all_to_archive, turn_id=turn_id)
            )
            
            # 添加完成回调，处理异常
            def on_done(t):
                try:
                    t.result()
                    print(f"[Archive] Saved {len(all_to_archive)} messages for {conversation_id}")
                except Exception as e:
                    print(f"[Archive] Failed to save history: {e}")
            
            task.add_done_callback(on_done)
        
        # 5. 长期记忆提取（异步，不阻塞响应）
        user_id = state.get("user_id")
        if self._ltm_store and user_id and len(messages) >= 2:
            query = state.get("query", "")
            answer = state.get("final_answer", "")
            if query and answer:
                async def _extract_and_save():
                    try:
                        facts = await self._ltm_store.extract_facts(query, answer, self._llm)
                        if facts:
                            await self._ltm_store.save_facts(user_id, facts, conversation_id=conversation_id, turn_id=turn_id)
                            print(f"[Archive] Extracted {len(facts)} LTM facts for user {user_id}")
                    except Exception as e:
                        print(f"[Archive] LTM extraction failed: {e}")
                asyncio.create_task(_extract_and_save())
        
        # 添加追踪事件
        state.setdefault("trace_events", []).append(
            {"node": "archive", "ts": time.time(), "ok": True, "archived_count": len(all_to_archive)}
        )
        
        self._emit_trace("archive", "node_end", "success")
        return {}

    def get_memory_stats(self, state: RAGState) -> Dict:
        """获取记忆统计信息"""
        return self._memory_manager.get_stats(
            state.get("messages", []),
            state.get("summary", "")
        )
