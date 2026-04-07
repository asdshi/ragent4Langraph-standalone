from __future__ import annotations

import asyncio
import importlib
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from langchain_core.prompts import ChatPromptTemplate

from src.core.settings import load_settings
from src.libs.llm import LLMFactory, Message
from src.ragent_backend.intent import detect_intent, split_parallel_subqueries
from src.ragent_backend.mcp_adapter import RAGMCPClient
from src.ragent_backend.schemas import RAGState
from src.ragent_backend.store import RAGSessionStore


class RAGWorkflow:
    def __init__(
        self,
        store: RAGSessionStore,
        mcp_client: RAGMCPClient,
    ) -> None:
        self._store = store
        self._mcp = mcp_client
        self._llm = self._init_llm()
        self._model_candidates = self._load_model_candidates()
        self._compiled = self._build_graph()

    def _build_graph(self):
        graph_module = importlib.import_module("langgraph.graph")
        START = graph_module.START
        END = graph_module.END
        StateGraph = graph_module.StateGraph
        types_module = importlib.import_module("langgraph.types")
        self._command_cls = types_module.Command

        graph = StateGraph(RAGState)

        graph.add_node("session", self._session_node)
        graph.add_node("intent", self._intent_node)
        graph.add_node("clarify", self._clarify_node)
        graph.add_node("retrieve", self._retrieve_node)
        graph.add_node("generate", self._generate_node)
        graph.add_node("output", self._output_node)

        graph.add_edge(START, "session")
        graph.add_edge("session", "intent")
        graph.add_edge("clarify", "output")
        graph.add_edge("retrieve", "generate")
        graph.add_edge("generate", "output")
        graph.add_edge("output", END)

        return graph.compile()

    async def run(self, initial_state: RAGState) -> RAGState:
        return await self._compiled.ainvoke(initial_state)

    async def _session_node(self, state: RAGState) -> RAGState:
        state["task_id"] = state.get("task_id") or str(uuid.uuid4())
        state["conversation_id"] = state.get("conversation_id") or str(uuid.uuid4())
        conversation_id = state["conversation_id"]
        bundle = await self._store.load_memory_bundle(conversation_id)
        state["recent_history"] = bundle.history
        state["memory_summary"] = bundle.memory_summary
        state["long_term_memory"] = bundle.long_term_memory
        state.setdefault("trace_events", []).append(
            {"node": "session", "ts": time.time(), "ok": True}
        )
        return state

    async def _intent_node(self, state: RAGState) -> RAGState:
        intent = detect_intent(state["query"], has_history=bool(state.get("recent_history")))
        sub_queries = split_parallel_subqueries(intent.rewritten_query)
        update: Dict[str, Any] = {
            "rewritten_query": intent.rewritten_query,
            "sub_queries": sub_queries,
            "intent_confidence": intent.confidence,
            "need_clarify": intent.need_clarify,
            "clarify_prompt": intent.clarify_prompt or "",
            "trace_events": [*state.get("trace_events", []),
            {
                "node": "intent",
                "ts": time.time(),
                "confidence": intent.confidence,
                "need_clarify": intent.need_clarify,
                "sub_query_count": len(sub_queries),
            }],
        }
        goto = "clarify" if intent.need_clarify else "retrieve"
        return self._command_cls(update=update, goto=goto)

    async def _clarify_node(self, state: RAGState) -> RAGState:
        state["final_answer"] = state.get("clarify_prompt", "请补充更多信息。")
        state["used_model"] = "clarify-shortcircuit"
        state.setdefault("trace_events", []).append(
            {"node": "clarify", "ts": time.time(), "ok": True}
        )
        return state

    async def _retrieve_node(self, state: RAGState) -> RAGState:
        queries = state.get("sub_queries") or [state.get("rewritten_query") or state["query"]]
        top_k = state.get("top_k", 5)
        collection = state.get("collection")

        if len(queries) == 1:
            context = await self._mcp.query_knowledge_hub(
                query=queries[0],
                top_k=top_k,
                collection=collection,
            )
            contexts = [context]
            merged_context = context
        else:
            tasks = [
                self._mcp.query_knowledge_hub(
                    query=sub_query,
                    top_k=top_k,
                    collection=collection,
                )
                for sub_query in queries
            ]
            raw_contexts = await asyncio.gather(*tasks, return_exceptions=True)

            contexts = []
            for idx, result in enumerate(raw_contexts):
                if isinstance(result, Exception):
                    contexts.append(f"子问题#{idx + 1} 检索失败: {result}")
                else:
                    contexts.append(result)

            merged_parts = []
            for idx, sub_query in enumerate(queries):
                merged_parts.append(f"### 子问题 {idx + 1}: {sub_query}\n{contexts[idx]}")
            merged_context = "\n\n".join(merged_parts)

        state["retrieval_contexts"] = contexts
        state["retrieval_context"] = merged_context
        state.setdefault("trace_events", []).append(
            {
                "node": "retrieve",
                "ts": time.time(),
                "ok": True,
                "parallel": len(queries) > 1,
                "sub_query_count": len(queries),
            }
        )
        return state

    async def _generate_node(self, state: RAGState) -> RAGState:
        prompt = self._build_prompt(state)

        answer = ""
        selected_model = ""
        for model_id in self._model_candidates:
            if await self._store.is_model_blacklisted(model_id):
                continue

            try:
                answer = await asyncio.wait_for(
                    self._generate_once(model_id, prompt),
                    timeout=60.0,
                )
                selected_model = model_id
                break
            except TimeoutError:
                await self._store.blacklist_model(model_id, ttl_seconds=120)
            except Exception:
                await self._store.blacklist_model(model_id, ttl_seconds=120)

        if not answer:
            answer = "生成服务暂时不可用，请稍后重试。"
            selected_model = "none"

        state["final_answer"] = answer
        state["used_model"] = selected_model
        state.setdefault("trace_events", []).append(
            {"node": "generate", "ts": time.time(), "model": selected_model}
        )
        return state

    async def _output_node(self, state: RAGState) -> RAGState:
        memory_summary = await self._generate_memory_summary(state)
        await self._store.save_exchange(
            conversation_id=state["conversation_id"],
            user_query=state["query"],
            answer=state.get("final_answer", ""),
            model_id=state.get("used_model", "unknown"),
            citations=state.get("retrieval_context", ""),
            task_id=state.get("task_id"),
            memory_summary=memory_summary,
        )
        state["memory_summary"] = memory_summary
        state.setdefault("trace_events", []).append(
            {"node": "output", "ts": time.time(), "ok": True}
        )
        return state

    def _build_prompt(self, state: RAGState) -> str:
        prompt = ChatPromptTemplate.from_template(
            """You are a production RAG assistant.
Use retrieval context and conversation history to answer.

Memory Summary:
{memory_summary}

Recent History:
{recent_history}

Context:
{context}

User:
{query}
"""
        )
        return prompt.format(
            memory_summary=state.get("memory_summary", ""),
            recent_history=state.get("recent_history", []),
            context=state.get("retrieval_context", ""),
            query=state.get("query", ""),
        )

    async def _generate_once(self, model_id: str, prompt: str) -> str:
        if self._llm is None:
            await asyncio.sleep(0)
            return f"[{model_id}]\n{prompt}\n\n以上回答基于检索结果生成。"

        def _chat_sync() -> str:
            response = self._llm.chat(
                messages=[
                    Message(role="system", content="你是工业级企业知识库助手。"),
                    Message(role="user", content=prompt),
                ],
                model=model_id,
            )
            return response.content

        return await asyncio.to_thread(_chat_sync)

    def _init_llm(self) -> Optional[Any]:
        try:
            settings = load_settings()
            return LLMFactory.create(settings)
        except Exception:
            return None

    def _load_model_candidates(self) -> List[str]:
        candidates: List[str] = []

        try:
            settings = load_settings()
            if settings.llm.model:
                candidates.append(settings.llm.model)
        except Exception:
            pass

        fallback_raw = os.getenv("RAGENT_FALLBACK_MODELS", "")
        if fallback_raw:
            for name in fallback_raw.split(","):
                normalized = name.strip()
                if normalized:
                    candidates.append(normalized)

        if not candidates:
            return ["primary-llm", "fallback-llm"]

        deduped: List[str] = []
        seen = set()
        for item in candidates:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    async def _generate_memory_summary(self, state: RAGState) -> str:
        recent_history = state.get("recent_history", [])
        existing_summary = state.get("memory_summary", "")
        if not recent_history and not existing_summary:
            return ""

        prompt = ChatPromptTemplate.from_template(
            """Summarize the conversation memory for future retrieval.
Keep it concise, factual, and useful for later turns.

Existing summary:
{existing_summary}

Recent history:
{recent_history}

Return only the updated memory summary.
"""
        )
        summary_prompt = prompt.format(
            existing_summary=existing_summary,
            recent_history=recent_history,
        )

        if self._llm is None:
            return self._fallback_memory_summary(existing_summary, recent_history)

        def _chat_sync() -> str:
            response = self._llm.chat(
                messages=[
                    Message(role="system", content="你是会话记忆摘要助手，只输出摘要。"),
                    Message(role="user", content=summary_prompt),
                ]
            )
            return response.content.strip()

        try:
            result = await asyncio.to_thread(_chat_sync)
            return result or self._fallback_memory_summary(existing_summary, recent_history)
        except Exception:
            return self._fallback_memory_summary(existing_summary, recent_history)

    def _fallback_memory_summary(
        self,
        existing_summary: str,
        recent_history: List[Dict[str, Any]],
    ) -> str:
        snippets: List[str] = []
        if existing_summary:
            snippets.append(existing_summary)
        for message in recent_history[-4:]:
            role = message.get("role", "unknown")
            content = str(message.get("content", ""))
            if content:
                snippets.append(f"{role}: {content[:120]}")
        return "\n".join(snippets).strip()
