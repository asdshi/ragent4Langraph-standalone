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
from typing import AsyncGenerator, Optional
from pathlib import Path

# 加载 .env 文件
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '..', '.env'))
    load_dotenv()  # 也尝试当前目录
except ImportError:
    pass  # python-dotenv 未安装

import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

# LangGraph checkpointer
from langgraph.checkpoint.postgres import PostgresSaver

from src.ragent_backend.schemas import ChatRequest, ChatResponse, RollbackRequest
from src.ragent_backend.store import build_archive_store, ConversationArchiveStore
from src.ragent_backend.workflow import RAGWorkflow
from src.ragent_backend.file_store import build_file_store, ConversationFileStore
from src.ragent_backend.conversation_store import build_conversation_store, ConversationStore
from src.ingestion.pipeline import IngestionPipeline
from src.core.settings import load_settings


def create_checkpointer():
    """
    创建 checkpointer
    Agent 层统一使用 PostgreSQL，不再支持 SQLite / InMemory fallback
    """
    postgres_url = os.getenv("RAGENT_POSTGRES_URL")
    if not postgres_url:
        raise ValueError(
            "RAGENT_POSTGRES_URL is required. "
            "Example: postgresql://user:password@localhost:5432/ragent"
        )
    checkpointer = PostgresSaver.from_conn_string(postgres_url)
    print(f"[Checkpointer] Using PostgreSQL: {postgres_url.replace(postgres_url.split(':')[-1].split('@')[0], '***')}")
    return checkpointer


async def _trim_checkpoints(checkpointer, thread_id: str, keep_checkpoint_id: Optional[str]) -> None:
    """
    物理裁剪 checkpoint：保留 keep_checkpoint_id 对应的状态，删除该 thread 下所有其他 checkpoint 记录。
    支持 AsyncSqliteSaver 和 PostgresSaver，同时清理关联的 blobs/writes 表。
    """
    
    async def _safe_delete(db, sql, params):
        try:
            await db.execute(sql, params)
        except Exception as e:
            if "no such table" in str(e).lower() or "does not exist" in str(e).lower():
                pass  # 表不存在则忽略
            else:
                raise
    
    try:
        # PostgresSaver
        if hasattr(checkpointer, '_async_connection') or type(checkpointer).__name__ == 'PostgresSaver':
            conn = getattr(checkpointer, '_async_connection', None)
            if conn is None:
                return
            if keep_checkpoint_id:
                await conn.execute(
                    "DELETE FROM checkpoints WHERE thread_id = $1 AND checkpoint_id != $2",
                    thread_id, keep_checkpoint_id
                )
                await _safe_delete(conn, "DELETE FROM checkpoint_blobs WHERE thread_id = $1 AND checkpoint_id != $2", (thread_id, keep_checkpoint_id))
                await _safe_delete(conn, "DELETE FROM checkpoint_writes WHERE thread_id = $1 AND checkpoint_id != $2", (thread_id, keep_checkpoint_id))
            else:
                await conn.execute("DELETE FROM checkpoints WHERE thread_id = $1", thread_id)
                await _safe_delete(conn, "DELETE FROM checkpoint_blobs WHERE thread_id = $1", (thread_id,))
                await _safe_delete(conn, "DELETE FROM checkpoint_writes WHERE thread_id = $1", (thread_id,))
            print(f"[TrimCheckpoint] Postgres trimmed for thread={thread_id}, kept={keep_checkpoint_id}")
            return
    except Exception as e:
        print(f"[TrimCheckpoint] Postgres trim failed: {e}")


# 全局并发控制：限制同时执行的 ingest 后台任务数量，防止 LLM API 配额和内存被打爆
INGEST_SEMAPHORE = asyncio.Semaphore(2)

# WebSocket 连接管理：conversation_id -> list[WebSocket]
active_trace_ws: dict[str, list[WebSocket]] = {}

async def broadcast_trace(conversation_id: str, data: dict) -> None:
    """向该对话的所有 WebSocket 客户端广播 trace 事件"""
    sockets = active_trace_ws.get(conversation_id, [])
    if not sockets:
        return
    dead = []
    for ws in sockets:
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        try:
            sockets.remove(ws)
        except ValueError:
            pass


# 允许上传的文件扩展名白名单
ALLOWED_EXTENSIONS = {
    '.pdf', '.docx', '.txt', '.md', '.csv',
    '.xlsx', '.xls', '.pptx', '.html', '.htm',
    '.json', '.yaml', '.yml',
}


async def ingest_file_task(
    file_store: ConversationFileStore,
    conversation_id: str,
    file_id: str,
    file_path: str,
    collection: str,
    settings: Settings,
) -> None:
    """
    后台任务：将文件 ingest 到对话的 collection
    受全局 INGEST_SEMAPHORE 控制，避免无限制并发导致资源耗尽。
    """
    async with INGEST_SEMAPHORE:
        try:
            # 更新状态为 ingesting
            await file_store.update_file_status(conversation_id, file_id, "ingesting")
            
            # 创建 ingestion pipeline，指定 target collection
            pipeline = IngestionPipeline(settings, collection=collection)
            
            # 执行 ingest（在线程池中运行，避免阻塞事件循环）
            result = await asyncio.to_thread(
                pipeline.run,
                file_path=file_path,
            )
            
            # 获取 doc_id（从 result 中提取）
            doc_id = result.doc_id if result.success else None
            
            # 从 loader metadata 中提取额外信息
            meta = result.metadata or {}
            extract_method = meta.get("extract_method")
            page_count = meta.get("page_count")
            word_count = meta.get("word_count")
            
            # 更新状态为 ready
            if result.success and doc_id:
                await file_store.update_file_status(
                    conversation_id, file_id, "ready", doc_id=doc_id,
                    extract_method=extract_method,
                    page_count=page_count,
                    word_count=word_count,
                )
                print(f"[Ingest] File {file_id} ingested successfully to {collection}, doc_id={doc_id}, method={extract_method}")
            else:
                error_msg = result.error or "Unknown error"
                await file_store.update_file_status(
                    conversation_id, file_id, "error", error_message=error_msg,
                    extract_method=extract_method,
                    page_count=page_count,
                    word_count=word_count,
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

    # 添加 CORS 中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 允许所有来源
        allow_credentials=True,
        allow_methods=["*"],  # 允许所有方法
        allow_headers=["*"],  # 允许所有头
    )

    # 加载配置
    settings = load_settings()

    # 初始化组件
    checkpointer = create_checkpointer()
    archive_store: ConversationArchiveStore = build_archive_store()
    file_store: ConversationFileStore = build_file_store()
    conversation_store: ConversationStore = build_conversation_store()
    
    # 初始化 LLM（配置完全来自 settings.yaml + 环境变量覆盖）
    try:
        from langchain_openai import ChatOpenAI
        llm_kwargs = {
            "model": settings.llm.model,
            "temperature": settings.llm.temperature,
            "max_tokens": settings.llm.max_tokens,
        }
        if getattr(settings.llm, "base_url", None):
            llm_kwargs["base_url"] = settings.llm.base_url
        if getattr(settings.llm, "api_key", None):
            llm_kwargs["api_key"] = settings.llm.api_key
        llm = ChatOpenAI(**llm_kwargs)
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
            
            # 扩展名校验
            original_name = file.filename or ""
            file_ext = Path(original_name).suffix.lower()
            if file_ext == '.doc':
                raise HTTPException(
                    status_code=400,
                    detail="旧版 .doc 格式暂不支持，请先转换为 .docx 后上传"
                )
            if file_ext not in ALLOWED_EXTENSIONS:
                raise HTTPException(
                    status_code=400,
                    detail=f"不支持的文件格式: {file_ext}。支持: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
                )
            
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
                    settings=settings,
                )
            )
            
            # 更新对话文件计数
            conv = await conversation_store.get_conversation(conversation_id)
            if conv:
                await conversation_store.update_conversation(
                    conversation_id,
                    file_count=conv.file_count + 1,
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
                        "file_type": f.file_type,
                        "page_count": f.page_count,
                        "extract_method": f.extract_method,
                        "word_count": f.word_count,
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
    
    # ==================== 对话管理 API ====================
    
    @app.post("/api/v1/conversations")
    async def create_conversation_endpoint(request: dict = None) -> dict:
        """创建新对话"""
        try:
            title = request.get("title") if request else None
            conv = await conversation_store.create_conversation(title=title)
            return {
                "conversation_id": conv.conversation_id,
                "title": conv.title,
                "created_at": conv.created_at.isoformat(),
                "updated_at": conv.updated_at.isoformat(),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to create conversation: {e}")
    
    @app.get("/api/v1/conversations")
    async def list_conversations_endpoint(
        status: str = "active",
        limit: int = 100,
        offset: int = 0
    ) -> dict:
        """获取对话列表（按更新时间倒序）"""
        try:
            conversations = await conversation_store.list_conversations(
                status=status, limit=limit, offset=offset
            )
            return {
                "total": len(conversations),
                "conversations": [
                    {
                        "conversation_id": c.conversation_id,
                        "title": c.title,
                        "created_at": c.created_at.isoformat(),
                        "updated_at": c.updated_at.isoformat(),
                        "message_count": c.message_count,
                        "file_count": c.file_count,
                        "status": c.status,
                    }
                    for c in conversations
                ]
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to list conversations: {e}")
    
    @app.get("/api/v1/conversations/{conversation_id}")
    async def get_conversation_endpoint(conversation_id: str) -> dict:
        """获取单个对话详情"""
        try:
            conv = await conversation_store.get_conversation(conversation_id)
            if not conv:
                raise HTTPException(status_code=404, detail="Conversation not found")
            return {
                "conversation_id": conv.conversation_id,
                "title": conv.title,
                "created_at": conv.created_at.isoformat(),
                "updated_at": conv.updated_at.isoformat(),
                "message_count": conv.message_count,
                "file_count": conv.file_count,
                "status": conv.status,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to get conversation: {e}")
    
    @app.patch("/api/v1/conversations/{conversation_id}")
    async def update_conversation_endpoint(conversation_id: str, request: dict) -> dict:
        """更新对话信息（标题、状态等）"""
        try:
            success = await conversation_store.update_conversation(
                conversation_id,
                title=request.get("title"),
                status=request.get("status"),
            )
            if not success:
                raise HTTPException(status_code=404, detail="Conversation not found")
            return {"message": "Conversation updated successfully"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to update conversation: {e}")
    
    @app.delete("/api/v1/conversations/{conversation_id}")
    async def delete_conversation_endpoint(conversation_id: str) -> dict:
        """删除对话（软删除）"""
        try:
            success = await conversation_store.delete_conversation(conversation_id)
            if not success:
                raise HTTPException(status_code=404, detail="Conversation not found")
            # 同时删除关联的文件
            await file_store.delete_conversation_files(conversation_id)
            return {"message": "Conversation deleted successfully"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to delete conversation: {e}")
    
    # ==================== 对话 API ====================
    
    @app.post("/api/v1/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        """
        对话接口
        
        检索范围自动限定为当前对话的 collection（conv_{conversation_id}）
        """
        # 使用 conversation_id 作为 thread_id，或创建新对话
        if request.conversation_id:
            thread_id = request.conversation_id
            conv = await conversation_store.get_conversation(thread_id)
            if not conv:
                raise HTTPException(status_code=404, detail="Conversation not found")
        else:
            # 创建新对话
            conv = await conversation_store.create_conversation()
            thread_id = conv.conversation_id
        
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
        
        # 更新对话消息计数
        await conversation_store.update_conversation(
            thread_id,
            message_count=conv.message_count + 2 if conv else 2,  # user + assistant
        )
        
        return ChatResponse(
            conversation_id=final_state["conversation_id"],
            task_id=final_state["task_id"],
            answer=final_state.get("final_answer", ""),
            model_id=final_state.get("used_model", "unknown"),
        )

    @app.post("/api/v1/chat/stream")
    async def chat_stream(request: ChatRequest, req: Request) -> StreamingResponse:
        """真流式对话接口：token-by-token 输出，客户端断开时自动回滚脏 checkpoint"""
        
        async def event_stream() -> AsyncGenerator[str, None]:
            # 1. 确定 thread / conversation
            if request.conversation_id:
                thread_id = request.conversation_id
                conv = await conversation_store.get_conversation(thread_id)
                if not conv:
                    yield f"data: {json.dumps({'type': 'error', 'error': 'Conversation not found'}, ensure_ascii=False)}\n\n"
                    return
            else:
                conv = await conversation_store.create_conversation()
                thread_id = conv.conversation_id
            
            initial_state = {
                "query": request.query,
                "conversation_id": thread_id,
                "task_id": request.task_id or os.urandom(8).hex(),
                "top_k": request.top_k,
            }
            
            # 2. 记录干净 checkpoint id（流式开始前）
            clean_checkpoint_id = None
            try:
                config = {"configurable": {"thread_id": thread_id}}
                if hasattr(checkpointer, 'aget'):
                    cp = await checkpointer.aget(config)
                elif hasattr(checkpointer, 'get'):
                    cp = checkpointer.get(config)
                else:
                    cp = None
                
                if cp:
                    if hasattr(cp, 'checkpoint_id'):
                        clean_checkpoint_id = cp.checkpoint_id
                    elif hasattr(cp, 'config') and hasattr(cp.config, 'configurable'):
                        clean_checkpoint_id = cp.config.configurable.get('checkpoint_id')
                    elif isinstance(cp, dict):
                        clean_checkpoint_id = cp.get('checkpoint_id') or cp.get('id')
            except Exception as e:
                print(f"[ChatStream] Failed to get clean checkpoint: {e}")
            
            interrupted = False
            final_state = {}
            
            try:
                # 新对话立即通知前端
                if not request.conversation_id:
                    yield f"data: {json.dumps({'type': 'conversation_created', 'conversation_id': thread_id}, ensure_ascii=False)}\n\n"
                
                # 3. 真流式执行（带心跳保活）
                stream = workflow.run_stream(initial_state, thread_id=thread_id)
                event_queue = asyncio.Queue()
                
                async def _pump_stream():
                    try:
                        async for evt in stream:
                            await event_queue.put(evt)
                    finally:
                        await event_queue.put(None)
                
                async def _heartbeat():
                    while True:
                        await asyncio.sleep(2)
                        await event_queue.put({"type": "heartbeat"})
                
                pump_task = asyncio.create_task(_pump_stream())
                hb_task = asyncio.create_task(_heartbeat())
                
                while True:
                    try:
                        event = await asyncio.wait_for(event_queue.get(), timeout=5)
                    except asyncio.TimeoutError:
                        if await req.is_disconnected():
                            interrupted = True
                            break
                        continue
                    
                    if event is None:
                        break
                    
                    if event.get("type") == "heartbeat":
                        yield f"data: {json.dumps({'type': 'heartbeat'}, ensure_ascii=False)}\n\n"
                        if await req.is_disconnected():
                            interrupted = True
                            break
                        continue
                    
                    if await req.is_disconnected():
                        interrupted = True
                        break
                    
                    if event.get("type") == "trace":
                        await broadcast_trace(thread_id, event)
                    elif event.get("type") == "token":
                        yield f"data: {json.dumps({'type': 'token', 'content': event['content']}, ensure_ascii=False)}\n\n"
                    elif event.get("type") == "done":
                        final_state = event.get("state", {})
                
                # 4. 正常结束：发送 done 并更新统计
                if not interrupted and final_state:
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
                    
                    await conversation_store.update_conversation(
                        thread_id,
                        message_count=conv.message_count + 2 if conv else 2,
                    )
                    
            except asyncio.CancelledError:
                interrupted = True
                print(f"[ChatStream] Stream cancelled, thread={thread_id}")
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"
            finally:
                # 5. 中断时回滚脏 checkpoint
                if interrupted and clean_checkpoint_id:
                    await _trim_checkpoints(checkpointer, thread_id, clean_checkpoint_id)
        
        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.websocket("/ws/trace/{conversation_id}")
    async def trace_websocket(websocket: WebSocket, conversation_id: str):
        """LangGraph 实时追踪 WebSocket：推送节点级执行进度"""
        await websocket.accept()
        active_trace_ws.setdefault(conversation_id, []).append(websocket)
        try:
            while True:
                # 保持连接，接收前端心跳/指令（如中断请求可扩展）
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            pass
        finally:
            sockets = active_trace_ws.get(conversation_id, [])
            if websocket in sockets:
                sockets.remove(websocket)
            if not sockets:
                active_trace_ws.pop(conversation_id, None)

    @app.post("/api/v1/conversations/{conversation_id}/rollback")
    async def rollback_conversation(conversation_id: str, request: RollbackRequest) -> dict:
        """
        三位一体时间裁剪：回滚对话到指定消息边界。
        同时清理：
        1. LangGraph Checkpoint（状态层）
        2. MySQL conversation_archive（存储层）
        3. SQLite ltm.db（记忆层）
        """
        # 1. 获取目标 turn
        turn_info = await archive_store.get_turn_by_message_id(conversation_id, request.target_message_id)
        if not turn_info:
            raise HTTPException(status_code=404, detail="Target message not found")
        
        target_turn_id = turn_info["turn_id"]
        if not target_turn_id:
            raise HTTPException(status_code=400, detail="Target message has no associated turn_id")
        
        # 2. 确定要保留的 checkpoint（状态层）
        # 先从历史中提取目标 turn 之前的那个 turn_id
        keep_checkpoint_id = None
        config = {"configurable": {"thread_id": conversation_id}}
        previous_turn_id = None
        try:
            history_msgs = await archive_store.load_full_history(conversation_id)
            # 按 created_at 排序后提取 turn_id 序列
            turn_order = []
            seen_turns = set()
            for m in history_msgs:
                tid = m.get("turn_id")
                if tid and tid not in seen_turns:
                    seen_turns.add(tid)
                    turn_order.append(tid)
            if target_turn_id in turn_order:
                target_idx = turn_order.index(target_turn_id)
                if target_idx > 0:
                    previous_turn_id = turn_order[target_idx - 1]
        except Exception as e:
            print(f"[Rollback] Failed to determine previous turn: {e}")
        
        try:
            if hasattr(checkpointer, 'alist') and previous_turn_id:
                candidates = []
                async for cp in checkpointer.alist(config):
                    # cp 是 CheckpointTuple，包含 config / checkpoint / metadata 等字段
                    cfg = cp.config if isinstance(cp.config, dict) else {}
                    checkpoint_id = cfg.get("configurable", {}).get("checkpoint_id")
                    
                    # 从 CheckpointTuple.checkpoint 提取状态
                    checkpoint_state = cp.checkpoint if hasattr(cp, 'checkpoint') else cp
                    if isinstance(checkpoint_state, dict):
                        channel_values = checkpoint_state.get('channel_values', {})
                    else:
                        channel_values = getattr(checkpoint_state, 'channel_values', {}) or {}
                    turn_id_in_cp = channel_values.get('current_turn_id') if isinstance(channel_values, dict) else None
                    
                    if turn_id_in_cp == previous_turn_id and checkpoint_id:
                        ts = checkpoint_state.get('ts', 0) if isinstance(checkpoint_state, dict) else getattr(checkpoint_state, 'ts', 0)
                        candidates.append((ts, checkpoint_id))
                if candidates:
                    # 取时间戳最大的（即最新的）一个前一 turn 的 checkpoint
                    candidates.sort(key=lambda x: x[0])
                    keep_checkpoint_id = candidates[-1][1]
        except Exception as e:
            print(f"[Rollback] Failed to list checkpoints: {e}")
        
        # 3. 执行三层回滚（互不阻断）
        trimmed = {"checkpoint": False, "messages": 0, "ltm": 0}
        
        try:
            await _trim_checkpoints(checkpointer, conversation_id, keep_checkpoint_id)
            trimmed["checkpoint"] = True
        except Exception as e:
            print(f"[Rollback] Checkpoint trim failed: {e}")
        
        try:
            trimmed["messages"] = await archive_store.delete_messages_from_turn(conversation_id, target_turn_id)
        except Exception as e:
            print(f"[Rollback] Message delete failed: {e}")
        
        try:
            if workflow._ltm_store:
                trimmed["ltm"] = await workflow._ltm_store.delete_facts_from_turn(conversation_id, target_turn_id)
        except Exception as e:
            print(f"[Rollback] LTM delete failed: {e}")
        
        # 4. 更新 conversation 的 message_count
        try:
            history = await archive_store.load_full_history(conversation_id)
            await conversation_store.update_conversation(
                conversation_id,
                message_count=len(history),
                metadata={"last_rollback_turn_id": target_turn_id}
            )
        except Exception as e:
            print(f"[Rollback] Failed to update conversation stats: {e}")
        
        return {
            "success": True,
            "conversation_id": conversation_id,
            "trimmed_turn_id": target_turn_id,
            "kept_checkpoint_id": keep_checkpoint_id,
            "trimmed": trimmed,
        }

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
                "messages": history
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to load history: {e}")

    @app.get("/api/v1/memory/{conversation_id}/stats")
    async def get_memory_stats(conversation_id: str) -> dict:
        """
        获取当前记忆的统计信息（调试用）
        这会从 checkpoint 加载并返回统计
        """
        config = {"configurable": {"thread_id": conversation_id}}
        try:
            if hasattr(checkpointer, 'aget'):
                checkpoint = await checkpointer.aget(config)
            elif hasattr(checkpointer, 'get'):
                checkpoint = checkpointer.get(config)
            else:
                checkpoint = None
        except Exception as e:
            print(f"[MemoryStats] Failed to load checkpoint: {e}")
            checkpoint = None
        
        if not checkpoint:
            return {"error": "Conversation not found"}
        
        state = {}
        if isinstance(checkpoint, dict):
            state = checkpoint.get("channel_values", {})
        elif hasattr(checkpoint, "checkpoint") and isinstance(checkpoint.checkpoint, dict):
            state = checkpoint.checkpoint.get("channel_values", {})
        elif hasattr(checkpoint, "channel_values"):
            state = checkpoint.channel_values
        
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
        await file_store.close()
        await conversation_store.close()

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
        reload=False
    )


if __name__ == "__main__":
    run()
