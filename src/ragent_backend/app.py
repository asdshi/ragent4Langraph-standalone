from __future__ import annotations

import json
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from src.ragent_backend.mcp_adapter import RAGMCPClient
from src.ragent_backend.schemas import ChatRequest, ChatResponse, RAGState
from src.ragent_backend.store import build_session_store
from src.ragent_backend.workflow import RAGWorkflow


def create_app() -> FastAPI:
    app = FastAPI(title="Industrial RAG Backend", version="0.1.0")

    store = build_session_store()
    mcp_client = RAGMCPClient()
    workflow = RAGWorkflow(store=store, mcp_client=mcp_client)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/v1/collections")
    async def list_collections() -> dict:
        collections_text = await mcp_client.list_collections(include_stats=True)
        return {"data": collections_text}

    @app.post("/api/v1/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        state: RAGState = {
            "query": request.query,
            "conversation_id": request.conversation_id,
            "task_id": request.task_id,
            "collection": request.collection,
            "top_k": request.top_k,
        }
        final_state = await workflow.run(state)
        return ChatResponse(
            conversation_id=final_state["conversation_id"],
            task_id=final_state["task_id"],
            answer=final_state.get("final_answer", ""),
            model_id=final_state.get("used_model", "unknown"),
        )

    @app.post("/api/v1/chat/stream")
    async def chat_stream(request: ChatRequest) -> StreamingResponse:
        async def event_stream() -> AsyncGenerator[str, None]:
            state: RAGState = {
                "query": request.query,
                "conversation_id": request.conversation_id,
                "task_id": request.task_id,
                "collection": request.collection,
                "top_k": request.top_k,
            }
            final_state = await workflow.run(state)
            answer = final_state.get("final_answer", "")

            for chunk in _chunk_text(answer, 32):
                event = {"type": "token", "content": chunk}
                yield f"data: {json.dumps(event, ensure_ascii=False)}\\n\\n"

            done_event = {
                "type": "done",
                "conversation_id": final_state.get("conversation_id"),
                "task_id": final_state.get("task_id"),
                "model_id": final_state.get("used_model"),
                "trace": final_state.get("trace_events", []),
            }
            yield f"data: {json.dumps(done_event, ensure_ascii=False)}\\n\\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return app


def _chunk_text(text: str, size: int):
    for i in range(0, len(text), size):
        yield text[i : i + size]


def run() -> None:
    uvicorn.run("src.ragent_backend.app:create_app", factory=True, host="0.0.0.0", port=8000)
