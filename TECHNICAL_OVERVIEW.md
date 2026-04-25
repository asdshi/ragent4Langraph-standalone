# RAG-Pro 技术全景文档

> 版本：基于当前代码库完整梳理  
> 范围：前端 → FastAPI → LangGraph → Ingestion Pipeline → Hybrid Search → MCP Server → 存储 → 可观测性

---

## 1. 项目定位与架构总览

**RAG-Pro** 是一个面向生产环境设计的**模块化 RAG（Retrieval-Augmented Generation）系统**。它不是一个简单的 demo，而是具备完整端到端链路、可观测性、评估体系、多协议接入（REST API + MCP）的企业级知识库问答系统。

### 1.1 核心能力矩阵

| 能力域 | 实现状态 | 关键技术 |
|:---|:---|:---|
| **对话式 RAG** | 完整 | LangGraph 状态机 + FastAPI SSE 流式输出 |
| **会话级知识库** | 完整 | 每个 conversation 独立 Chroma collection |
| **文件摄取** | 完整 | PDF/TXT 解析 → 分块 → LLM Refine/Enrich/Caption → 向量化 |
| **混合检索** | 完整 | Dense（语义）+ Sparse（BM25）+ RRF 融合 + Rerank |
| **查询优化** | 完整 | 结构化 LLM 一次完成指代消解 + 子查询拆分 |
| **记忆管理** | 完整 | 滑动窗口压缩 + 滚动摘要 + MySQL 归档 + LTM 长期记忆 |
| **MCP 协议** | 完整 | stdio-based MCP Server，3 个 tools |
| **可观测性** | 完整 | Trace 瀑布图 + Streamlit Dashboard + Ragas 评估 |
| **多 Provider** | 完整 | OpenAI/Azure/DeepSeek/Ollama 可切换 |

### 1.2 系统架构图（文字版）

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              前端层 (Vue 3 + Vite)                           │
│  - 流式对话界面 (SSE)                                                       │
│  - 文件上传 (native input + 进度轮询)                                        │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │ HTTP / SSE
┌───────────────────────────────────▼─────────────────────────────────────────┐
│                         后端 API 层 (FastAPI)                               │
│  - POST /api/v1/chat/stream        → RAGWorkflow.run_stream()               │
│  - POST /api/v1/conversations/{id}/files  → ingest_file_task() (后台任务)   │
│  - GET  /health                                                            │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
┌───────────────────────┐ ┌───────────────┐ ┌─────────────────┐
│   LangGraph RAG 工作流 │ │  Ingestion    │ │   MCP Server    │
│   (RAGWorkflow)       │ │   Pipeline    │ │   (stdio)       │
│  session→intent→      │ │  6-stage sync │ │  3 tools        │
│  retrieve→generate→   │ │  pipeline     │ │  query/list/get │
│  memory→archive       │ │               │ │                 │
└──────┬────────────────┘ └───────┬───────┘ └─────────────────┘
       │                          │
       ▼                          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           检索与存储层                                       │
│  - ChromaDB (dense vectors)  +  BM25 (sparse index)  +  ImageStorage        │
│  - SQLite/Postgres (checkpoints)  +  MySQL (conversation archive)           │
│  - SQLite (file store, LTM store, integrity checker)                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 前端层（Vue 3 + Vite）

**入口文件**：`frontend/src/App.vue`

### 2.1 技术栈与结构

- **框架**：Vue 3（Composition API）
- **构建工具**：Vite
- **UI 组件库**：Element Plus
- **网络请求**：原生 `EventSource`（SSE）+ `fetch`（文件上传）

### 2.2 核心功能

#### 2.2.1 流式对话

前端通过 `EventSource` 连接 `/api/v1/chat/stream`，实时接收两种事件：

- `{"type": "token", "content": "..."}`：逐字显示回答内容
- `{"type": "done", "state": {...}}`：流结束，可提取引用信息

**关键修复历史**：
- 曾因为后端 `yield {"token": "..."}` 字段名与前端的 `data.content` 不匹配导致流式不显示，已统一为 `content` 字段。
- SSE 连接完成后主动 `eventSource.close()`，避免内存泄漏。

#### 2.2.2 文件上传

由于 Element Plus 的 `el-upload` 在 `action=""` 时会禁用文件选择框，前端改用原生 `<input type="file">` 触发选择，再通过 `fetch` 发送到 `/api/v1/conversations/{id}/files`。

上传后文件进入后台 `ingest_file_task`，前端通过轮询文件状态接口（`GET /api/v1/conversations/{id}/files`）观察 `status` 字段变化：`uploaded` → `ingesting` → `ready` / `error`。

---

## 3. 后端 API 层（FastAPI）

**入口文件**：`src/ragent_backend/app.py`

### 3.1 应用生命周期

`create_app()` 是一个异步工厂函数，负责初始化：

1. **CORS 中间件**：允许所有来源（开发环境配置）。
2. **配置加载**：`load_settings()` 从 `config/settings.yaml` 读取。
3. **存储组件**：
   - `checkpointer`：优先 Postgres，回退 Sqlite（用于 LangGraph 状态持久化）
   - `archive_store`：MySQL 对话归档
   - `file_store`：SQLite 文件元数据管理
   - `conversation_store`：SQLite 对话元数据管理
4. **LLM 初始化**：`ChatOpenAI`（默认 `qwen3.5-omni-flash`）
5. **RAGWorkflow 实例**：绑定上述所有依赖

### 3.2 核心路由

#### 3.2.1 `/api/v1/chat/stream`（流式对话）

```python
@app.post("/api/v1/chat/stream")
async def chat_stream(request: ChatRequest, req: Request) -> StreamingResponse:
```

**流程**：
1. 获取或生成 `thread_id`（来自 `conversation_id` 或随机生成）
2. **Checkpoint 回滚准备**：调用 `workflow._compiled.aget_state()` 获取当前线程的 clean checkpoint ID。如果后续用户打断（`CancelledError`），会删除该 checkpoint ID 之后的所有脏状态，实现"打断 = 拒绝这次回答，忽略这次对话"。
3. 启动 `event_stream` 生成器，调用 `workflow.run_stream()`
4. 用 `StreamingResponse` 以 `text/event-stream` 格式返回

**关键修复**：`AsyncSqliteSaver` 不支持同步 `get_state()`，已改为异步 `aget_state()`。

#### 3.2.2 文件上传路由

```python
@app.post("/api/v1/conversations/{conversation_id}/files")
async def upload_file(...)
```

- 文件保存到 `data/uploads/{conversation_id}/`
- 生成 `file_id` 和 `file_path`
- 调用 `asyncio.create_task(ingest_file_task(...))` 启动后台摄取任务
- **并发控制**：`ingest_file_task` 外部包了一层全局 `asyncio.Semaphore(2)`，限制同时执行的 ingest 任务最多 2 个，防止大文件并发上传时打爆 LLM API 配额和内存。

### 3.3 Checkpointer 回滚机制

这是本项目在 LangGraph 工程化上的一个亮点。

```python
async def _rollback_checkpoints(...):
    # 1. 获取 thread 的 checkpoint 列表
    # 2. 找到所有 checkpoint_id > clean_checkpoint_id 的记录
    # 3. 从 AsyncSqliteSaver 的 SQLite 表中物理删除
```

语义：当用户主动打断当前流式输出时，本轮对话在 checkpointer 中产生的中间状态（包括用户消息、助手消息、检索上下文等）全部回滚，下一次加载该 conversation 时，状态恢复到本轮开始之前。

---

## 4. RAG 工作流（LangGraph）

**核心文件**：`src/ragent_backend/workflow.py`

### 4.1 状态定义

`RAGState`（`src/ragent_backend/schemas.py`）是一个 TypedDict，使用 `Annotated[list, add_messages]` 管理消息列表，关键字段：

- `messages`：对话历史（HumanMessage / AIMessage / RemoveMessage）
- `summary`：滚动摘要（旧消息被压缩后的内容）
- `memories`：LTM 长期记忆列表
- `query` / `rewritten_query` / `sub_queries`：原始查询、重写后查询、子查询列表
- `retrieval_context`：检索到的文本上下文
- `final_answer`：最终生成的回答
- `trace_events`：节点执行事件追踪

### 4.2 节点流程

```
START → session → intent → [conditional] → retrieve → generate → memory_manage → archive → END
                              │
                              └── need_clarify=True ──→ archive → END
```

#### 4.2.1 `session` 节点

- 确保 `conversation_id`、`task_id` 存在
- 为所有消息分配 `id`（RemoveMessage 依赖）
- 如果配置了 `LTMStore` 且存在 `user_id`，则检索长期记忆（top-k=3）

#### 4.2.2 `intent` 节点

这是查询优化的核心节点：

1. **单次结构化调用**：调用 `analyze_query(query, messages, llm)`，通过 `llm.with_structured_output(QueryAnalysisResult, method="json_mode")` 一次性获得：
   - `rewritten_query`：消除代词指代的完整查询
   - `sub_queries`：如果查询包含多个并列主题，拆分为独立子查询列表
2. **意图检测**：`detect_intent(rewritten_query)` 判断是否需要澄清（如查询为空、恶意注入、极度模糊）
3. **短 circuit**：如果 `need_clarify=True`，设置 `final_answer` 为澄清提示，直接路由到 `archive` 节点，跳过 retrieve 和 generate

**历史修复**：早期使用 `Command(goto="archive")` 实现短 circuit，但 LangGraph 1.1.6 中 `Command` 在节点内部返回存在兼容性问题，导致路由失效。已改为 `add_conditional_edges` 方案，完全稳定。

#### 4.2.3 `retrieve` 节点

- 使用 `rewritten_query` 作为检索输入
- 构建 collection 名称：`conv_{conversation_id}`
- 调用 `QueryKnowledgeHubTool.execute(query, collection, top_k)`
- 将检索结果写入 `retrieval_context`

#### 4.2.4 `generate` 节点

- 构建 prompt：整合 `memories`、`summary`、`recent_history`、`retrieval_context`、`query`
- 调用 `self._llm.astream([HumanMessage(content=prompt)])`
- **流式透传**：每个 token 通过 `asyncio.Queue` 推入队列，`run_stream()` 消费并 `yield`
- 将完整回答封装为 `AIMessage` 返回

#### 4.2.5 `memory_manage` 节点

使用 `RollingMemoryManager` 实现滑动窗口压缩：

- 当 `messages` 数量超过 `max_messages`（默认 20）时触发压缩
- 保留最近 `keep_recent` 条消息（默认 4 条）
- 将旧消息传给 LLM 生成摘要，与现有 `summary` 合并
- 返回 `RemoveMessage(id=...)` 操作，LangGraph 自动从状态中移除这些消息
- 被移除的消息标记到 `_to_archive`，供 `archive` 节点异步写入 MySQL

#### 4.2.6 `archive` 节点

- **归档**：将被压缩的消息 + 本轮新消息（最后两条 user/assistant）异步写入 MySQL `archive_store`
- **LTM 提取**：如果配置了 `ltm_store`，异步调用 `extract_facts(query, answer, llm)`，提取结构化事实并持久化到 SQLite LTM 表
- 两者均使用 `asyncio.create_task` 不阻塞主响应

---

## 5. 查询分析层（Intent & Query Analysis）

**核心文件**：`src/ragent_backend/intent.py`

### 5.1 结构化输出模型

```python
class QueryAnalysisResult(BaseModel):
    rewritten_query: str = Field(..., description="消除所有代词和指代后的完整查询")
    sub_queries: List[str] = Field(..., description="如果查询包含多个并列主题，拆分为独立子查询列表；否则只放一个元素")
```

### 5.2 `analyze_query()` 流程

1. 取最近 4 条消息作为历史上下文
2. 构造 few-shot prompt，明确指示 LLM 完成两项任务：
   - **指代消解**：将"它的性能怎么样"重写为"华为 Mate 60 搭载的麒麟 9000S 芯片的性能怎么样"
   - **子查询拆分**：将"北京上海杭州的天气怎么样"拆分为 3 个子查询
3. 调用 `structured_llm.ainvoke(prompt)`
4. 异常时 fallback 到旧的 `rewrite_query()` + `split_parallel_subqueries()`

### 5.3 `detect_intent()`

基于规则 + LLM 的混合意图检测：

- 空查询 / 纯空白 → `need_clarify=True`
- 明显恶意注入 → `need_clarify=True`
- 模糊查询（如"这个呢？"但无历史）→ `need_clarify=True`
- 正常查询 → `need_clarify=False`

---

## 6. 数据摄取流水线（Ingestion Pipeline）

**核心文件**：`src/ingestion/pipeline.py`

### 6.1 Pipeline 阶段总览

`IngestionPipeline.run()` 执行 6 个阶段：

```
Stage 1: File Integrity Check   → SHA256 哈希 + SQLite 去重表
Stage 2: Document Loading       → PdfLoader / TextLoader
Stage 3: Document Chunking      → RecursiveSplitter
Stage 4: Transform Pipeline     → ChunkRefiner + MetadataEnricher + ImageCaptioner
Stage 5: Encoding               → DenseEncoder + SparseEncoder
Stage 6: Storage                → VectorUpserter (Chroma) + BM25Indexer + ImageStorage
```

### 6.2 关键组件详解

#### 6.2.1 Stage 1：完整性检查

`SQLiteIntegrityChecker` 维护 `data/db/ingestion_history.db`，记录每个文件的 hash、path、collection、status。如果同一文件（相同 SHA256）再次上传到同一 collection，且无 `--force`，则直接跳过，实现幂等性。

#### 6.2.2 Stage 2：文档加载

- **PdfLoader**：基于 `markitdown` 提取 PDF 文本，同时提取嵌入图片保存到 `data/images/{collection}/{doc_hash}/`
- **TextLoader**：处理 `.txt` 文件
- 加载后的 `Document` 对象包含 `text`、`metadata`（含 `images` 列表）

#### 6.2.3 Stage 3：分块

`DocumentChunker` 使用 `RecursiveSplitter`，按 `chunk_size`（默认 1000）和 `chunk_overlap`（默认 200）进行层次化拆分。每个 chunk 生成唯一 ID：`{doc_id}_{chunk_index}_{hash}`。

#### 6.2.4 Stage 4：Transform Pipeline

这是摄取链路中 LLM 调用最密集的阶段，包含三个串行但内部并行的子阶段：

**a) ChunkRefiner（块精炼）**
- 先执行规则清洗（去页眉页脚、HTML 标签、归一化空白）
- 如果 `use_llm=true`，通过 `ThreadPoolExecutor(max_workers=3)` 并行调用 LLM 对每个 chunk 进行内容改写和连贯性增强
- 失败时 fallback 到规则清洗结果

**b) MetadataEnricher（元数据增强）**
- 规则层：提取 title（首行/首句）、summary（前 3 句）、tags（专有名词/代码标识符/Markdown 强调词）
- LLM 层：并行调用 LLM 生成更丰富的 title/summary/tags
- 输出写入 chunk.metadata

**c) ImageCaptioner（图片描述）**
- 扫描 chunk 文本中的 `[IMAGE: id]` 占位符
- 收集所有唯一图片，通过 `ThreadPoolExecutor(max_workers=3)` 并行调用 Vision LLM 生成 caption
- 使用线程安全的 `_caption_cache` 避免同一图片重复调用 API
- 将 caption 插入文本占位符旁边

**并发控制**：
- 三个子阶段之间是**串行**的（因为后一阶段依赖前一阶段修改后的 `chunk.text`）
- 但每个子阶段内部通过 `ThreadPoolExecutor` 对 chunks / images 进行**并行处理**
- 全局并发通过 `settings.yaml` 中的 `ingestion.max_workers` 控制（默认 3）
- 应用层通过 `INGEST_SEMAPHORE(2)` 限制同时运行的 pipeline 实例数

#### 6.2.5 Stage 5：编码

`BatchProcessor` 协调：
- `DenseEncoder`：调用 Embedding API 生成语义向量
- `SparseEncoder`：基于 `jieba` 分词生成 BM25 所需的词频统计

两者结果分别写入 chunk 的 metadata 和独立的 sparse stats 列表。

#### 6.2.6 Stage 6：存储

- **ChromaDB**：`VectorUpserter` 将 dense vectors 写入对应 collection
- **BM25**：将 sparse stats 写入 `data/db/bm25/{collection}/{collection}_bm25.json`
- **ImageStorage**：将图片元数据登记到 SQLite `image_index.db`

**ID 对齐**：BM25 命中后需要通过 `chunk_id` 从向量库取回文本，因此 pipeline 在写入后将 `vector_ids[i]` 回写到 `sparse_stats[i]["chunk_id"]`，保证跨检索链路可互通。

### 6.3 失败回滚

如果任何阶段抛出异常，pipeline 会：
1. 调用 `_rollback_storage()` 删除已写入的 vectors、BM25 索引、图片登记
2. 在 integrity checker 中标记失败状态
3. 返回 `PipelineResult(success=False, error=...)`

### 6.4 异步入口

`pipeline.py` 新增了 `async def arun(...)`：

```python
async def arun(self, file_path, trace=None, on_progress=None):
    return await asyncio.to_thread(self.run, file_path, trace, on_progress)
```

方便上层（如 FastAPI）在不阻塞事件循环的情况下调用同步 pipeline。

---

## 7. 检索引擎（HybridSearch）

**核心文件**：`src/core/query_engine/hybrid_search.py`

### 7.1 整体流程

```
query → QueryProcessor → [parallel] DenseRetriever + SparseRetriever → RRFFusion → optional Rerank → final results
```

### 7.2 QueryProcessor

对原始查询进行预处理：
- 提取关键词（用于 BM25 稀疏检索）
- 提取元数据过滤条件（如 `collection:xxx`）

### 7.3 DenseRetriever

- 将查询文本通过 Embedding API 编码为向量
- 在 ChromaDB 指定 collection 中进行近似最近邻搜索（ANN）
- 返回 `List[RetrievalResult]`，包含 `chunk_id`、`score`、`text`、`metadata`

### 7.4 SparseRetriever

- 使用 `jieba` 对查询关键词进行中文分词
- 加载对应 collection 的 BM25 索引（每次查询都重新从磁盘加载，保证数据新鲜）
- 计算 BM25 分数，返回 top-k 结果

### 7.5 RRFFusion

使用 Reciprocal Rank Fusion 算法融合两路结果：

```
score_rrf(chunk) = sum(1 / (k + rank_in_list))   # k 默认 60
```

- 对 Dense 和 Sparse 各自的排名列表分别计算 RRF 分数
- 按总分降序排列
- 截断到 `fusion_top_k`（默认 10）

### 7.6 优雅降级

`HybridSearch.search()` 设计了完善的降级策略：

- **Dense 失败，Sparse 成功** → 仅返回 Sparse 结果
- **Sparse 失败，Dense 成功** → 仅返回 Dense 结果
- **两路都失败** → 抛出 `RuntimeError`
- **一路无结果** → 使用另一路结果，不走空融合

### 7.7 Reranker（可选）

`src/core/query_engine/reranker.py` 支持两种重排序器：

- **Cross-Encoder**：本地加载 `cross-encoder/ms-marco-MiniLM-L-6-v2`，通过 HuggingFace `sentence-transformers` 计算查询-文档相关性分数
- **LLM Reranker**：调用 LLM 对候选文档进行相关性打分（0-5 分），然后按分数重排

Reranker 失败后也会 graceful fallback 到 RRF 融合结果。

---

## 8. MCP Server 与工具层

**核心文件**：`src/mcp_server/server.py`、`src/mcp_server/tools/query_knowledge_hub.py`

### 8.1 MCP Server 架构

- **传输层**：stdio（标准输入输出）
- **协议层**：官方 Python MCP SDK
- **日志重定向**：所有日志强制输出到 `stderr`，避免污染 `stdout` 上的 JSON-RPC 报文
- **预加载优化**：在主线程预先 import `chromadb`、`hybrid_search` 等重型模块，避免后台线程触发 import lock 竞争导致卡死

### 8.2 注册的工具（3 个）

| 工具名 | 功能 |
|:---|:---|
| `query_knowledge_hub` | 混合检索知识库，返回带引用的格式化结果 |
| `list_collections` | 列出所有 collection 及其文档统计 |
| `get_document_summary` | 根据 doc_id 获取文档摘要 |

### 8.3 `query_knowledge_hub` 执行流程

1. **初始化组件**：根据 `collection` 参数重建 `VectorStore`、`DenseRetriever`、`SparseRetriever`、`HybridSearch`。 embedding client 和 reranker 缓存复用。
2. **Hybrid Search**：`asyncio.to_thread(self._perform_search, ...)`
3. **Rerank**：如果启用，在线程池中执行
4. **ResponseBuilder 格式化**：将 `RetrievalResult` 列表渲染为 Markdown 文本，附带引用信息（`source_file`、`page`、`chunk_id`、`score`）
5. **Trace 记录**：将查询全链路 trace 收集到 `TraceCollector`

---

## 9. 记忆与存储系统

### 9.1 三层记忆架构

```
┌─────────────────┐  最近 N 条消息（LangGraph checkpoint）
│  短期记忆 (STM)  │  → SQLite/Postgres
├─────────────────┤
│  滚动摘要        │  → 旧消息被压缩后存入 summary 字段（checkpoint 内）
├─────────────────┤
│  对话归档        │  → MySQL（完整历史，供用户查看）
├─────────────────┤
│  长期记忆 (LTM)  │  → SQLite ltm.db（跨会话用户事实）
└─────────────────┘
```

### 9.2 Checkpointer（SQLite / Postgres）

- 优先尝试 `PostgresSaver`，如果连接失败则回退 `SqliteSaver`
- 在 `chat_stream` 中，用户打断时会触发 checkpoint 物理回滚，保证状态一致性

### 9.3 MySQL Archive Store

`ConversationArchiveStore` 将每轮对话的 user query 和 assistant answer 异步写入 MySQL。这是"给用户看的历史记录"，与 LangGraph 内部状态分离。

### 9.4 LTM Store

`LTMStore`（`src/ragent_backend/ltm_store.py`）基于 SQLite：

- `extract_facts(query, answer, llm)`：异步调用 LLM 从本轮 Q&A 中提取结构化事实（如"用户是软件工程师"、"用户偏好中文回答"）
- `save_facts(user_id, facts)`：写入 `ltm.db`
- `retrieve_facts(user_id, query, top_k)`：通过 BM25 检索与用户当前查询相关的长期记忆

### 9.5 File Store

`ConversationFileStore`（`src/ragent_backend/file_store.py`）基于 SQLite：

- 记录每个 conversation 上传的文件列表
- 字段包括：`file_id`、`file_name`、`file_path`、`status`（`uploaded`/`ingesting`/`ready`/`error`）、`doc_id`、`error_message`
- 支持文件删除（从 Chroma、BM25、磁盘、数据库四清）

---

## 10. 可观测性与评估

### 10.1 Trace 系统

**核心文件**：`src/core/trace/trace_context.py`、`trace_collector.py`

每个 query 和 ingestion 都会生成一个 `TraceContext`，记录各阶段耗时与数据快照：

- `trace.record_stage(name, data, elapsed_ms)`
- `TraceCollector().collect(trace)` 将 trace 追加到 `logs/traces.jsonl`

### 10.2 Streamlit Dashboard

**入口**：`scripts/start_dashboard.py`

提供 6 个页面：

1. **Overview**：组件配置卡片、集合统计、Trace 统计
2. **Data Browser**：按 collection 浏览文档、chunk、metadata、图片预览
3. **Ingestion Manager**：文件上传、摄取进度、文档删除
4. **Ingestion Traces**：摄取历史列表、阶段瀑布图、各 Tab 详情
5. **Query Traces**：查询历史列表、关键词过滤、Ragas Evaluate 按钮
6. **Evaluation Panel**：选择 evaluator（ragas/custom/composite）、运行评估、查看历史记录

### 10.3 评估体系

- **RagasEvaluator**：集成 `ragas` 库，计算 `faithfulness`、`answer_relevancy`、`context_precision`
- **CustomEvaluator**：基于 hit_rate 和 MRR 的检索质量评估
- **EvalRunner**：读取 `tests/fixtures/golden_test_set.json`，自动对每条查询执行检索并打分
- **Benchmark 脚本**：`benchmark_rag.py`（项目根目录），用于回归测试，覆盖：
  - Query Analysis（指代消解 + 子查询拆分）
  - Ingestion Pipeline + Trace
  - RAG Retrieval Quality

### 10.4 检索策略消融实验
| 对比维度 | 检索层评估（Hit Rate / MRR） | 生成层评估（Faithfulness / Answer Relevancy） |
| :--- | :--- | :--- |
| 是否需要金标 | ✅ 必须有预期文档标签/Chunk ID | ❌ 不需要标准答案、无人工金标 |
| 核心输入 | query + 真实关联文档ID + 召回文档列表 | query + retrieved_contexts + LLM生成答案 |
| 评判方式 | 文档ID/标签 规则匹配、位置统计 | 大模型作为裁判 + Embedding相似度计算 |
| 依赖模型 | 仅检索模型（Embedding/BM25），无生成LLM | 依赖打分LLM、Embedding模型 |
| 计算成本 | 极低，纯数值运算，无大模型推理 | 较高，每条样本需多次LLM调用 |
| 评估目标 | 检索召回完整性、排序优劣 | 答案幻觉程度、问答相关性、生成质量 |
| 数据依赖 | 必须标注数据集（如mMARCO） | 无需标注数据集，纯RAG输出即可计算 |

基于 [**mMARCO Chinese**](https://huggingface.co/datasets/unicamp-dl/mmarco)（真实 Bing 搜索日志人工标注数据集）的消融对比：

| 策略 | Avg Latency (ms) | Hit Rate | MRR | Coverage | Avg Results | 说明 |
|:---|:---:|:---:|:---:|:---:|:---:|:---|
| dense_only | 4597.7 | **0.9800** | 0.9537 | 1.00 | 10.00 | 纯 Dense 检索 (Chroma ANN) |
| sparse_only | **56.1** | 0.8300 | 0.7683 | 0.95 | 6.29 | 纯 Sparse 检索 (BM25) |
| hybrid | 4929.8 | 0.9700 | 0.8908 | 1.00 | 10.00 | Dense + Sparse + RRF 融合 |
| hybrid_rerank | 11123.8 | 0.9700 | **0.9570** | 1.00 | 10.00 | Hybrid + DashScope `qwen3-rerank` 精排 |

**实验设置**：
- 数据集：mMARCO Chinese dev 子集（100 queries，604 passages，人工标注相关 docid）
- 评估指标：Hit Rate（top-10 是否命中任意 positive）、MRR（positive 的 reciprocal rank）
- Ground truth：mMARCO 官方 qrels（非自生成，避免 circular evaluation）

**结论**：
- **Dense 检索质量最高**：`dense_only` hit_rate=0.9800、MRR=0.9537，纯向量语义检索在短 passage 场景下表现优异。
- **BM25 速度碾压但质量下降**：`sparse_only` 仅 **56.1 ms**（比 dense 快 **82 倍**），但 hit_rate 降至 0.83、MRR 降至 0.7683——适合关键词明确、对延迟极度敏感的场景。
- **RRF 融合在此场景下反而稀释排名**：`hybrid` 的 MRR（0.8908）低于 `dense_only`（0.9537），说明稀疏分支召回了与 query 相关度较低的 passage，拉低了最相关 passage 的排名。
- **Rerank 拉回质量**：`hybrid_rerank` MRR 回升至 **0.9570**（接近 dense_only），说明 DashScope `qwen3-rerank` API 能有效纠正 RRF 融合带来的排名稀释。代价是延迟增加约 **6.2 秒**。
- **综合推荐**：
  - 延迟敏感 + 关键词明确 → `sparse_only`
  - 质量优先 + 短 passage → `dense_only`
  - 兼顾两者 + 可接受 API 成本 → `hybrid_rerank`

> 运行方式：`python scripts/run_ablation.py --collection mmarco --test-set tests/fixtures/golden_test_set_mmarco.json`

---

## 11. 关键工程决策与已知限制

### 11.1 关键工程决策

| 决策 | 原因 | 效果 |
|:---|:---|:---|
| **LangGraph checkpoint 回滚** | 用户打断后需要语义上"忽略本轮" | 物理删除脏 checkpoint，下一次加载完全干净 |
| **analyze_query 合并为一次结构化调用** | 减少 LLM 调用次数，降低延迟和成本 | benchmark 0.67 → 0.96 |
| **对话级 collection 隔离** | 不同对话的知识库必须互不干扰 | 通过 `conv_{conversation_id}` 命名实现 |
| **BM25 chunk_id 与 vector_id 对齐** | 稀疏检索命中后需要从向量库取文本 | 保证 HybridSearch 融合阶段数据一致 |
| **全局 INGEST_SEMAPHORE** | 防止并发 ingest 打爆 LLM API 配额 | 限制同时执行 ingest 任务数为 2 |

### 11.2 已知限制

1. **Ingestion 大文件仍有延迟**：已增加 Semaphore 和 max_workers 控制，但 50+ 页 PDF 的 LLM transform 总量仍然可观。后续如需彻底解耦，应引入 Celery/Redis 持久化队列。
2. **Transform 子阶段间串行**：`refiner → enricher → captioner` 存在数据依赖，无法简单并行。如需进一步优化，需将 `BaseLLM.chat` 改造为异步 `achat()`。
3. **Embedding API 偶发超时**：DashScope 兼容模式下 embedding 超时问题与代码无关，需检查 API key 或切换 endpoint。
4. **MySQL archive 任务取消**：`_archive_node` 使用 `asyncio.create_task` 写 MySQL，事件 loop 过早关闭时可能导致部分归档丢失。

---

## 12. 技术栈清单

| 层级 | 技术 |
|:---|:---|
| 前端 | Vue 3, Vite, Element Plus |
| 后端框架 | FastAPI, Uvicorn |
| Agent 框架 | LangGraph 1.1.6, LangChain OpenAI |
| 向量数据库 | ChromaDB |
| 稀疏检索 | BM25 + jieba |
| 重排序 | Cross-Encoder (sentence-transformers) / LLM Reranker |
| 文档解析 | markitdown (PDF), 自定义 TextLoader |
| Embedding | OpenAI/Azure/DashScope 兼容 API |
| LLM | OpenAI/Azure/DeepSeek/Ollama 可切换 |
| Vision LLM | Azure OpenAI GPT-4V / Qwen-VL |
| 存储 | SQLite (checkpoints, file store, LTM, integrity), MySQL (archive), Postgres (checkpoint fallback) |
| 可观测性 | Streamlit, JSONL trace logs, Ragas |
| 协议 | MCP (stdio), SSE |
| 评估 | Ragas, Custom Evaluator, pytest |
