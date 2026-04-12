"""
RAG Backend API - 会话级知识库版本

主要变更：
1. 每个对话有独立的 collection（conv_{conversation_id}）
2. 支持文件上传和实时 ingest
3. RAG 检索限定在当前对话的文件范围内
4. 保持滑动窗口记忆管理
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse

# LangGraph checkpointer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.postgres import PostgresSaver

from src.ragent_backend.schemas import ChatRequest, ChatResponse
from src.ragent_backend.store import build_archive_store, ConversationArchiveStore
from src.ragent_backend.workflow import RAGWorkflow
from src.ragent_backend.file_store import build_file_store, ConversationFileStore
from src.ingestion.pipeline import IngestionPipeline


def create_checkpointer():
    """
    创建 checkpointer
    优先使用 Postgres，回退到 Sqlite
    """
    # 检查 PostgreSQL 配置
    postgres_url = os.getenv("RAGENT_POSTGRES_URL")
    if postgres_url:
        try:
            # PostgresSaver 需要 psycopg 或 psycopg2
            return PostgresSaver.from_conn_string(postgres_url)
        except Exception as e:
            print(f"[Checkpointer] Failed to init Postgres: {e}, fallback to Sqlite")
    
    # 使用 Sqlite（本地开发）
    db_path = os.getenv("RAGENT_SQLITE_PATH", "checkpoints.sqlite")
    print(f"[Checkpointer] Using Sqlite: {db_path}")
    return SqliteSaver.from_conn_string(db_path)


async def ingest_file_task(
    file_store: ConversationFileStore,
    conversation_id: str,
    file_id: str,
    file_path: str,
    collection: str,
) -> None:
    """
    后台任务：将文件 ingest 到对话的 collection
    """
    try:
        # 更新状态为 ingesting
        await file_store.update_file_status(conversation_id, file_id, "ingesting")
        
        # 创建 ingestion pipeline，指定 target collection
        pipeline = IngestionPipeline(collection=collection)
        
        # 执行 ingest
        result = await asyncio.to_thread(
            pipeline.run,
            file_path=file_path,
        )
        
        # 获取 doc_id（从 result 中提取）
        doc_id = result.doc_id if result.success else None
        
        # 更新状态为 ready
        if result.success and doc_id:
            await file_store.update_file_status(
                conversation_id, file_id, "ready", doc_id=doc_id
            )
            print(f"[Ingest] File {file_id} ingested successfully to {collection}, doc_id={doc_id}")
        else:
            error_msg = result.error or "Unknown error"
            await file_store.update_file_status(
                conversation_id, file_id, "error", error_message=error_msg
            )
            print(f"[Ingest] Failed to ingest file {file_id}: {error_msg}")
        
    except Exception as e:
        print(f"[Ingest] Failed to ingest file {file_id}: {e}")
        await file_store.update_file_status(
            conversation_id, file_id, "error", error_message=str(e)
        )


def create_app() -> FastAPI:
    app = FastAPI(
        title="RAG Agent Backend", 
        version="0.3.0",
        description="支持会话级知识库的 RAG Agent"
    )

    # 初始化组件
    checkpointer = create_checkpointer()
    archive_store: ConversationArchiveStore = build_archive_store()
    file_store: ConversationFileStore = build_file_store()
    
    # 初始化 LLM
    try:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(
            model=os.getenv("RAGENT_LLM_MODEL", "gpt-4o"),
            temperature=0.7,
        )
    except Exception as e:
        print(f"[Init] Failed to init LLM: {e}")
        llm = None

    # 创建工作流
    workflow = RAGWorkflow(
        store=archive_store,
        llm=llm,
        checkpointer=checkpointer,
        max_messages=int(os.getenv("RAGENT_MAX_MESSAGES", "20")),
        keep_recent=int(os.getenv("RAGENT_KEEP_RECENT", "4")),
    )

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "version": "0.3.0",
            "features": ["rolling_memory", "conversation_kb", "file_upload"]
        }

    # ==================== 文件管理 API ====================
    
    @app.post("/api/v1/conversations/{conversation_id}/files")
    async def upload_file(
        conversation_id: str,
        file: UploadFile = File(...),
    ) -> dict:
        """
        上传文件到对话
        
        文件会被：
        1. 保存到磁盘
        2. 记录到数据库
        3. 后台异步 ingest 到对话的 collection
        """
        try:
            # 读取文件内容
            content = await file.read()
            
            if not content:
                raise HTTPException(status_code=400, detail="Empty file")
            
            # 保存文件
            file_info = await file_store.save_file(
                conversation_id=conversation_id,
                file_content=content,
                original_filename=file.filename,
                mime_type=file.content_type or "application/octet-stream",
            )
            
            # 构建 collection 名称
            collection = f"conv_{conversation_id}"
            
            # 启动后台 ingest 任务
            asyncio.create_task(
                ingest_file_task(
                    file_store=file_store,
                    conversation_id=conversation_id,
                    file_id=file_info.file_id,
                    file_path=file_info.file_path,
                    collection=collection,
                )
            )
            
            return {
                "file_id": file_info.file_id,
                "filename": file_info.original_name,
                "size": file_info.file_size,
                "status": file_info.status,
                "message": "File uploaded successfully, processing in background"
            }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to upload file: {e}")
    
    @app.get("/api/v1/conversations/{conversation_id}/files")
    async def list_files(conversation_id: str) -> dict:
        """列出对话的所有文件"""
        try:
            files = await file_store.list_files(conversation_id)
            return {
                "conversation_id": conversation_id,
                "file_count": len(files),
                "files": [
                    {
                        "file_id": f.file_id,
                        "filename": f.original_name,
                        "size": f.file_size,
                        "status": f.status,
                        "doc_id": f.doc_id,
                        "created_at": f.created_at.isoformat() if f.created_at else None,
                    }
                    for f in files
                ]
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to list files: {e}")
    
    @app.delete("/api/v1/conversations/{conversation_id}/files/{file_id}")
    async def delete_file(conversation_id: str, file_id: str) -> dict:
        """删除对话中的文件"""
        try:
            success = await file_store.delete_file(conversation_id, file_id)
            if not success:
                raise HTTPException(status_code=404, detail="File not found")
            return {"message": "File deleted successfully"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to delete file: {e}")
    
    # ==================== 对话 API ====================
    
    @app.post("/api/v1/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        """
        对话接口
        
        检索范围自动限定为当前对话的 collection（conv_{conversation_id}）
        """
        # 使用 conversation_id 作为 thread_id，或生成新的
        thread_id = request.conversation_id or os.urandom(16).hex()
        
        # 准备初始状态
        initial_state = {
            "query": request.query,
            "conversation_id": thread_id,
            "task_id": request.task_id or os.urandom(8).hex(),
            "top_k": request.top_k,
            # collection 由 workflow 内部自动构建为 f"conv_{thread_id}"
        }
        
        # 运行工作流
        final_state = await workflow.run(initial_state, thread_id=thread_id)
        
        return ChatResponse(
            conversation_id=final_state["conversation_id"],
            task_id=final_state["task_id"],
            answer=final_state.get("final_answer", ""),
            model_id=final_state.get("used_model", "unknown"),
        )

    @app.post("/api/v1/chat/stream")
    async def chat_stream(request: ChatRequest) -> StreamingResponse:
        """流式对话接口（简化版，实际应逐 token 流式）"""
        
        async def event_stream() -> AsyncGenerator[str, None]:
            thread_id = request.conversation_id or os.urandom(16).hex()
            
            initial_state = {
                "query": request.query,
                "conversation_id": thread_id,
                "task_id": request.task_id or os.urandom(8).hex(),
                "collection": request.collection,
                "top_k": request.top_k,
            }
            
            final_state = await workflow.run(initial_state, thread_id=thread_id)
            answer = final_state.get("final_answer", "")

            # 模拟流式输出（实际应接入 LLM 的 stream 接口）
            for chunk in _chunk_text(answer, 32):
                event = {"type": "token", "content": chunk}
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            # 发送完成事件，包含记忆统计
            memory_stats = workflow.get_memory_stats(final_state)
            done_event = {
                "type": "done",
                "conversation_id": final_state.get("conversation_id"),
                "task_id": final_state.get("task_id"),
                "model_id": final_state.get("used_model"),
                "trace": final_state.get("trace_events", []),
                "memory_stats": memory_stats,
            }
            yield f"data: {json.dumps(done_event, ensure_ascii=False)}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/api/v1/history/{conversation_id}")
    async def get_history(conversation_id: str) -> dict:
        """
        获取完整对话历史（从 MySQL 加载）
        这是用户可见的完整历史，不是 checkpoint 中的精简状态
        """
        try:
            history = await archive_store.load_full_history(conversation_id)
            return {
                "conversation_id": conversation_id,
                "message_count": len(history),
                "history": history
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to load history: {e}")

    @app.get("/api/v1/memory/{conversation_id}/stats")
    async def get_memory_stats(conversation_id: str) -> dict:
        """
        获取当前记忆的统计信息（调试用）
        这会从 checkpoint 加载并返回统计
        """
        # 从 checkpointer 加载状态
        config = {"configurable": {"thread_id": conversation_id}}
        checkpoint = checkpointer.get(config)
        
        if not checkpoint:
            return {"error": "Conversation not found"}
        
        state = checkpoint.get("channel_values", {})
        messages = state.get("messages", [])
        summary = state.get("summary", "")
        
        return {
            "conversation_id": conversation_id,
            "message_count": len(messages),
            "summary_length": len(summary),
            "summary_preview": summary[:200] + "..." if len(summary) > 200 else summary,
            "recent_messages": [
                {"role": "user" if m.type == "human" else "assistant", "content": m.content[:100]}
                for m in messages[-4:]
            ]
        }

    @app.on_event("shutdown")
    async def shutdown():
        """关闭时清理资源"""
        await archive_store.close()
        # file_store 不需要显式关闭（没有连接池）

    return app


def _chunk_text(text: str, size: int):
    """将文本分块，用于模拟流式输出"""
    for i in range(0, len(text), size):
        yield text[i : i + size]


def run() -> None:
    """运行服务器"""
    uvicorn.run(
        "src.ragent_backend.app:create_app", 
        factory=True, 
        host="0.0.0.0", 
        port=int(os.getenv("RAGENT_PORT", "8000")),
        reload=os.getenv("RAGENT_DEBUG", "false").lower() == "true"
    )


if __name__ == "__main__":
    run()
