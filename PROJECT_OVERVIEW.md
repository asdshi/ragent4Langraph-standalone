# RAG Agent 项目架构说明

> **版本**: 0.3.0  
> **定位**: 从 MCP RAG 工具扩展为支持会话级知识库的 RAG Agent  
> **最后更新**: 2026-04-12

---

## 1. 项目定位与演进

### 1.1 原始形态（MCP RAG Server）
项目最初是一个标准的 **MCP (Model Context Protocol) Server**，暴露以下工具：
- `query_knowledge_hub` - 全局知识库检索（Dense + Sparse + RRF Fusion）
- `list_collections` - 列出知识库集合
- `get_document_summary` - 获取文档摘要

**特点**: 面向 Claude Desktop 等 MCP 客户端，提供标准化的 RAG 能力。

### 1.2 演进形态（RAG Agent Backend）
基于 MCP 核心能力，扩展为**业务级 RAG Agent**，支持：
- **会话级知识库**: 每个对话有独立的文件集合，RAG 检索限定在当前对话内
- **文件实时上传**: 通过 REST API 上传文件，后台异步 ingest
- **多轮对话记忆**: 滑动窗口记忆管理 + MySQL 持久化

---

## 2. 核心架构

### 2.1 混合架构设计

```
┌─────────────────────────────────────────────────────────────────┐
│                        RAG Agent Backend                        │
├──────────────────────────┬──────────────────────────────────────┤
│     REST API (FastAPI)   │        MCP Protocol (stdio)          │
│  ──────────────────────  │  ─────────────────────────────────   │
│  POST /chat              │  query_knowledge_hub                 │
│  POST /conversations/    │    (接收 collection 参数，支持动态)   │
│    {id}/files            │                                      │
│  GET  /conversations/    │                                      │
│    {id}/files            │                                      │
│  GET  /history/{id}      │                                      │
│  GET  /memory/{id}/stats │                                      │
└──────────────────────────┴──────────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
   ┌──────────────┐ ┌──────────┐ ┌──────────────┐
   │   ChromaDB   │ │  MySQL   │ │  SQLite/     │
   │ (conv_{id}   │ │(文件元数据│ │  Postgres    │
   │  collections)│ │ + 归档)  │ │ (checkpoints)│
   └──────────────┘ └──────────┘ └──────────────┘
```

**设计原则**:
- **REST API 负责业务交互**: 文件上传、对话管理、流式对话
- **MCP 负责通用检索**: 保留标准协议能力，供外部客户端使用
- **collection 隔离**: 每个对话独立 collection (`conv_{conversation_id}`)

---

## 3. 核心数据流

### 3.1 文件上传与 Ingest 流程

```
用户上传文件 (POST /conversations/{id}/files)
        │
        ▼
┌──────────────────────┐
│ 1. 保存到磁盘        │  data/uploads/{conversation_id}/{file_id}_{filename}
│    (file_store.py)   │
└──────────────────────┘
        │
        ▼
┌──────────────────────┐
│ 2. 记录元数据到 MySQL│  conversation_files 表
│    (file_store.py)   │  status = 'pending'
└──────────────────────┘
        │
        ▼
┌──────────────────────┐
│ 3. 启动后台任务      │  asyncio.create_task(ingest_file_task)
│    (app.py)          │
└──────────────────────┘
        │
        ▼
┌──────────────────────┐
│ 4. 执行 Ingestion    │  IngestionPipeline(collection=f"conv_{id}")
│    (pipeline.py)     │  - 切块、embedding、存入 Chroma
└──────────────────────┘
        │
        ▼
┌──────────────────────┐
│ 5. 更新状态          │  status = 'ready', doc_id = xxx
│    (file_store.py)   │
└──────────────────────┘
```

### 3.2 对话与检索流程

```
用户提问 (POST /chat)
        │
        ▼
┌──────────────────────────────────────┐
│ RAGWorkflow.run()                    │
│ (workflow.py)                        │
│                                      │
│ 1. session_node                      │
│    - 确保 conversation_id            │
│    - 从 checkpointer 加载历史消息    │
│                                      │
│ 2. intent_node                       │
│    - 意图识别、查询重写              │
│                                      │
│ 3. _retrieve_node ⭐                 │
│    - 构建 collection = f"conv_{id}" │
│    - 调用 QueryKnowledgeHubTool      │
│    - 检索范围限定在当前对话          │
│                                      │
│ 4. generate_node                     │
│    - LLM 生成回答                    │
│    - prompt 包含检索上下文           │
│                                      │
│ 5. memory_manage_node ⭐             │
│    - 滑动窗口压缩                    │
│    - 旧消息合并到 summary            │
│                                      │
│ 6. archive_node                      │
│    - 异步归档到 MySQL                │
└──────────────────────────────────────┘
```

---

## 4. 核心组件说明

### 4.1 记忆管理（双轨制）

| 存储类型 | 用途 | 实现 | 生命周期 |
|---------|------|------|---------|
| **短期记忆** | 给模型用的上下文 | LangGraph Checkpoint (SQLite/Postgres) | 滑动窗口压缩，保留最近 N 条 |
| **长期归档** | 用户可见的完整历史 | MySQL `conversation_archive` 表 | 永久保留，支持对话历史查询 |
| **滚动摘要** | 压缩后的历史摘要 | 存储在 checkpoint 的 `summary` 字段 | 随对话增长，定期重写 |

**滑动窗口机制**:
```python
# RollingMemoryManager
max_messages = 20  # 超过则触发压缩
keep_recent = 4    # 保留最近 4 条

# 当消息数 > 20:
# 1. 保留最近 4 条消息
# 2. 其余 16 条合并到 summary
# 3. 使用 RemoveMessage 从 checkpoint 删除
```

### 4.2 文件存储（双层）

| 存储层 | 用途 | 关键操作 |
|-------|------|---------|
| **磁盘存储** | 保存原始文件 | `data/uploads/{conv_id}/{file_id}_{filename}` |
| **MySQL 元数据** | 文件信息、状态追踪 | `conversation_files` 表 |

**文件状态流转**:
```
pending → ingesting → ready
   ↓          ↓         ↓
  刚上传   正在处理   可检索
           ↓
        error
           ↓
        处理失败
```

### 4.3 检索流程（MCP Tool）

```python
# QueryKnowledgeHubTool.execute()
1. _ensure_initialized(collection)
   - 创建/获取 Chroma collection
   - 初始化 embedding client、retrievers
   
2. _perform_search(query, top_k)
   - Dense retrieval (向量检索)
   - Sparse retrieval (BM25)
   - RRF Fusion (Reciprocal Rank Fusion)
   
3. _apply_rerank (可选)
   - Cross-encoder 重排序
   
4. _response_builder.build()
   - 格式化为带引用的文本
```

---

## 5. 关键配置项

### 5.1 环境变量

| 变量名 | 说明 | 默认值 |
|-------|------|--------|
| `OPENAI_API_KEY` | OpenAI API Key（embedding + LLM）| 必填 |
| `RAGENT_SQLITE_PATH` | Checkpoint 存储路径 | `checkpoints.sqlite` |
| `RAGENT_POSTGRES_URL` | Postgres 连接串（可选）| - |
| `RAGENT_MYSQL_*` | MySQL 连接配置 | localhost/root/... |
| `RAGENT_MAX_MESSAGES` | 记忆窗口大小 | 20 |
| `RAGENT_KEEP_RECENT` | 压缩后保留消息数 | 4 |
| `RAGENT_PORT` | API 端口 | 8000 |

### 5.2 配置文件

`config/settings.yaml`:
- `vector_store.persist_directory` - ChromaDB 存储路径
- `ingestion.chunk_size` - 文档切块大小
- `retrieval.dense_top_k` - 向量检索 Top K
- `rerank.enabled` - 是否启用重排序

---

## 6. API 接口清单

### 6.1 文件管理

```http
# 上传文件
POST /api/v1/conversations/{conversation_id}/files
Content-Type: multipart/form-data

file: <binary>

# 响应
{
  "file_id": "abc123",
  "filename": "document.pdf",
  "size": 1024000,
  "status": "pending",
  "message": "File uploaded successfully, processing in background"
}
```

```http
# 列出文件
GET /api/v1/conversations/{conversation_id}/files

# 响应
{
  "conversation_id": "conv_xxx",
  "file_count": 3,
  "files": [
    {
      "file_id": "abc123",
      "filename": "document.pdf",
      "size": 1024000,
      "status": "ready",
      "doc_id": "doc_hash_xxx",
      "created_at": "2026-04-12T10:00:00"
    }
  ]
}
```

```http
# 删除文件
DELETE /api/v1/conversations/{conversation_id}/files/{file_id}
```

### 6.2 对话接口

```http
# 对话（自动使用 conv_{conversation_id} 作为检索范围）
POST /api/v1/chat
Content-Type: application/json

{
  "query": "总结一下上传的文档",
  "conversation_id": "xxx",  // 可选，不传则创建新对话
  "top_k": 5
}

# 响应
{
  "conversation_id": "xxx",
  "task_id": "yyy",
  "answer": "...",
  "model_id": "gpt-4o"
}
```

```http
# 流式对话
POST /api/v1/chat/stream
# SSE 格式返回

# 获取完整历史（用户可见）
GET /api/v1/history/{conversation_id}

# 获取记忆统计（调试用）
GET /api/v1/memory/{conversation_id}/stats
```

---

## 7. 待办/扩展点

### 7.1 当前限制
1. **Ingestion 是同步阻塞的** - 大文件会占用后台任务较长时间
2. **缺少对话管理** - 没有显式的"创建对话"和"删除对话"接口
3. **文件删除不彻底** - 只删除了元数据和磁盘文件，未从 Chroma 中删除向量
4. **缺少进度反馈** - 文件上传后无法查询 ingest 进度

### 7.2 建议扩展

| 功能 | 实现思路 |
|------|---------|
| **对话管理** | 新增 `conversations` 表，提供 CRUD 接口 |
| **向量级联删除** | 文件删除时，根据 doc_id 从 Chroma 删除相关 chunks |
| **WebSocket 进度** | 文件上传后通过 WebSocket 推送 ingest 进度 |
| **多文件共享** | 支持一个文件被多个对话引用（软链接或复制）|
| **文件预览** | 提供 PDF/图片预览接口 |

---

## 8. 技术栈

| 层级 | 技术 |
|------|------|
| **框架** | FastAPI + LangGraph |
| **向量存储** | ChromaDB |
| **关系存储** | MySQL (aiomysql) |
| **Checkpoint** | SQLite (开发) / Postgres (生产) |
| **检索** | Dense (OpenAI Embedding) + Sparse (BM25) + RRF |
| **协议** | MCP (Model Context Protocol) |
| **文档处理** | MarkItDown, Jieba, LangChain Text Splitters |

---

## 9. 关键文件索引

| 文件 | 职责 |
|------|------|
| `src/ragent_backend/app.py` | FastAPI 入口，路由定义，后台任务 |
| `src/ragent_backend/workflow.py` | LangGraph 工作流，记忆管理，检索节点 |
| `src/ragent_backend/file_store.py` | 文件存储管理（磁盘 + MySQL）|
| `src/ragent_backend/memory_manager.py` | 滑动窗口记忆压缩逻辑 |
| `src/ragent_backend/store.py` | 对话归档存储（MySQL）|
| `src/mcp_server/tools/query_knowledge_hub.py` | MCP 检索工具 |
| `src/ingestion/pipeline.py` | 文档摄取流水线 |
| `src/core/query_engine/hybrid_search.py` | Hybrid Search 实现 |

---

## 10. 启动方式

```bash
# 1. 环境准备
pip install -e .

# 2. 配置环境变量
export OPENAI_API_KEY="sk-..."
export RAGENT_MYSQL_PASSWORD="..."

# 3. 启动服务
python -m src.ragent_backend.app

# 4. 检查配置
python main.py
```

---

**备注**: 本项目处于活跃开发阶段，API 和架构可能持续演进。本文档描述的是 0.3.0 版本的实现状态。
