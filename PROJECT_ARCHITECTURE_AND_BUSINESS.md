# RAG-Pro 项目全量架构与业务介绍

> **版本**: 基于当前代码库完整梳理 (v0.3.0+)  
> **定位**: 面向生产环境的模块化 RAG（Retrieval-Augmented Generation）系统  
> **范围**: 前端 → FastAPI → LangGraph → Ingestion Pipeline → Hybrid Search → MCP Server → 存储 → 可观测性 → 评估体系

---

## 一、项目定位与演进历程

### 1.1 项目定位

**RAG-Pro** 是一个**企业级对话式知识库问答系统**，不是一个简单的 RAG Demo，而是具备完整端到端链路、多协议接入（REST API + MCP）、可观测性、评估体系的模块化生产系统。

其核心差异化能力是**"对话级知识库"**：每个对话（Conversation）拥有独立的文档集合，RAG 检索严格限定在当前对话范围内，实现真正的会话隔离。

### 1.2 演进历程

| 阶段 | 形态 | 能力 |
|:---|:---|:---|
| **原始形态** | MCP RAG Server | 暴露 `query_knowledge_hub`、`list_collections`、`get_document_summary` 三个工具，供 Claude Desktop 等 MCP 客户端调用 |
| **演进形态** | RAG Agent Backend | 在 MCP 核心能力之上，扩展出 REST API、会话级知识库、文件实时上传、滑动窗口记忆管理、真流式 SSE 输出 |

### 1.3 核心能力矩阵

| 能力域 | 实现状态 | 关键技术 |
|:---|:---|:---|
| **对话式 RAG** | 完整 | LangGraph 状态机 + FastAPI SSE 流式输出 |
| **会话级知识库** | 完整 | 每个 conversation 独立 Chroma collection (`conv_{id}`) |
| **文件摄取** | 完整 | 6 阶段 Pipeline：解析 → 分块 → Refine → Enrich → Caption → 向量化 |
| **混合检索** | 完整 | Dense（语义）+ Sparse（BM25）+ RRF 融合 + 可选 Rerank |
| **查询优化** | 完整 | 结构化 LLM 一次完成指代消解 + 子查询拆分 |
| **记忆管理** | 完整 | 滑动窗口压缩 + 滚动摘要 + MySQL 归档 + LTM 长期记忆 |
| **MCP 协议** | 完整 | stdio-based MCP Server，3 个 tools |
| **可观测性** | 完整 | Trace 瀑布图 + Streamlit Dashboard + Ragas 评估 |
| **多 Provider** | 完整 | OpenAI / Azure / DeepSeek / Ollama 可切换 |

---

## 二、业务介绍

### 2.1 目标用户与场景

- **企业员工**：上传内部文档（PDF、Word、Excel、PPT 等），通过自然语言问答快速获取信息
- **知识管理**：每个项目/话题创建一个对话，独立维护专属知识库
- **开发者/Agent 集成**：通过 MCP 协议将混合检索能力嵌入到其他 AI 应用中

### 2.2 核心业务功能

#### 2.2.1 对话管理
- 创建、列出、更新、删除对话
- 每个对话有独立的 `conversation_id`，对应独立的 Chroma collection
- 支持对话软删除，同步清理关联文件

#### 2.2.2 文件实时上传与处理
- 支持格式：PDF、DOCX、TXT、MD、CSV、XLSX、PPTX、HTML、JSON、YAML
- 文件上传后立即返回，后台异步执行 Ingestion Pipeline
- 前端通过轮询观察文件状态：`pending` → `ingesting` → `ready` / `error`
- 全局并发控制（`INGEST_SEMAPHORE=2`），防止大文件并发上传打爆 LLM API 配额

#### 2.2.3 对话式问答（流式输出）
- 真流式 SSE 输出：`token-by-token` 实时推送到前端
- 检索范围自动限定为当前对话的 collection
- 支持用户中断生成，中断后自动回滚脏 checkpoint，语义上"忽略本轮"
- **Checkpoint Timeline 交互**：消息列表中每轮对话之间以横向虚线分隔，线中点有小圆点表示 checkpoint。点击圆点可"回溯到此处"，触发三层时间裁剪（Checkpoint + Archive + LTM），永久删除该消息及之后的所有记录

#### 2.2.4 检索来源展示
- AI 回复下方可展开"检索来源"
- 显示来源文档、chunk 内容预览、相关度得分

---

## 三、系统架构总览

### 3.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              前端层 (Vue 3 + Vite)                           │
│  - 流式对话界面 (SSE EventSource)                                           │
│  - 文件上传 (原生 input + fetch 轮询)                                        │
│  - 对话列表管理、LangGraph Trace 可视化侧栏                                   │
│  - WebSocket 实时接收 trace 事件                                             │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │ HTTP / SSE / WebSocket
┌───────────────────────────────────▼─────────────────────────────────────────┐
│                         后端 API 层 (FastAPI)                               │
│  - POST /api/v1/chat/stream        → RAGWorkflow.run_stream()               │
│  - POST /api/v1/conversations/{id}/files  → ingest_file_task()              │
│  - 对话/文件/历史/记忆统计 CRUD 接口                                          │
│  - WebSocket /ws/trace/{conversation_id}                                    │
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
│  - SQLite/Postgres (LangGraph checkpoints)  +  MySQL (conversation archive) │
│  - SQLite (file store, LTM store, integrity checker)                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 设计原则

- **REST API 负责业务交互**：文件上传、对话管理、流式对话、历史查询
- **MCP 负责通用检索**：保留标准协议能力，供外部客户端（如 Claude Desktop）调用
- **Collection 隔离**：每个对话独立 collection (`conv_{conversation_id}`)，知识库互不干扰
- **双轨制记忆**：LangGraph Checkpoint（给模型的短期记忆）与 MySQL Archive（给用户看的完整历史）分离

---

## 四、核心数据流

### 4.1 文件上传与 Ingest 流程

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
│ 2. 记录元数据到 SQLite│  conversation_files 表
│    (file_store.py)   │  status = 'pending'
└──────────────────────┘
        │
        ▼
┌──────────────────────┐
│ 3. 启动后台任务      │  asyncio.create_task(ingest_file_task)
│    (app.py)          │  受全局 Semaphore(2) 控制并发
└──────────────────────┘
        │
        ▼
┌──────────────────────┐
│ 4. 执行 Ingestion    │  IngestionPipeline(collection=f"conv_{id}")
│    (pipeline.py)     │  6 阶段同步处理（在线程池中运行）
└──────────────────────┘
        │
        ▼
┌──────────────────────┐
│ 5. 更新状态          │  status = 'ready', doc_id = xxx
│    (file_store.py)   │  提取 page_count、word_count、extract_method
└──────────────────────┘
```

### 4.2 对话与检索流程（LangGraph）

```
用户提问 (POST /chat/stream)
        │
        ▼
┌──────────────────────────────────────┐
│ RAGWorkflow.run_stream()             │
│ (workflow.py)                        │
│                                      │
│ 1. session_node                      │
│    - 确保 conversation_id / task_id  │
│    - 从 checkpointer 加载历史消息    │
│    - 召回 LTM 长期记忆               │
│    - 生成 current_turn_id            │
│                                      │
│ 2. intent_node                       │
│    - analyze_query: 指代消解 + 子查询拆分（单次结构化调用）
│    - detect_intent: 判断是否需要澄清 │
│    - need_clarify=True → 短 circuit 到 archive│
│                                      │
│ 3. retrieve_node                     │
│    - collection = f"conv_{id}"      │
│    - 调用 QueryKnowledgeHubTool      │
│    - HybridSearch (Dense + BM25 + RRF)
│                                      │
│ 4. generate_node                     │
│    - 构建 Prompt（memories + summary + recent_history + retrieval_context）
│    - llm.astream() 流式生成          │
│    - token 通过 asyncio.Queue 透传给 SSE│
│                                      │
│ 5. memory_manage_node                │
│    - 滑动窗口压缩                    │
│    - 旧消息合并到 summary            │
│    - 返回 RemoveMessage 删除旧消息   │
│                                      │
│ 6. archive_node                      │
│    - 异步归档到 MySQL                │
│    - 异步提取 LTM 事实到 SQLite      │
└──────────────────────────────────────┘
```

---

## 五、分层架构详解

### 5.1 前端层（Vue 3 + Vite + Element Plus）

**入口**: `frontend/src/App.vue`

#### 核心模块

| 模块 | 说明 |
|:---|:---|
| **聊天界面** | 消息气泡、Markdown 简易渲染、思考中动画、快捷操作按钮 |
| **对话管理** | 左侧边栏展示历史对话，支持新建/切换/删除，本地存储 `currentConversationId` |
| **文件管理** | 每个对话独立的文件列表，显示状态标签（等待中/处理中/就绪/错误）、OCR 标识 |
| **Trace 面板** | 右侧可折叠面板，通过 WebSocket 实时展示 LangGraph 节点执行进度 |
| **Checkpoint Timeline** | 每轮对话间以横向虚线+圆点分隔，hover 显示"回溯到此处"，点击触发消息级 rollback |
| **设置弹窗** | API 地址、Top K、流式开关、模型选择 |

#### 关键技术细节

- **真流式 SSE**：使用原生 `fetch` + `ReadableStream` 读取 `/api/v1/chat/stream`，逐字追加到 assistant message
- **文件上传**：使用原生 `<input type="file">` 绕过 Element Plus `el-upload` 的兼容问题
- **状态轮询**：上传后每 3 秒轮询文件列表，最多 20 次，观察 `status` 变化
- **WebSocket Trace**：连接 `/ws/trace/{conversation_id}`，接收 `type: trace` 事件并实时渲染
- **Checkpoint Timeline**：消息按轮次（turn）分组，组间渲染横向虚线分隔线，中点有绿色圆点表示当前 checkpoint。点击圆点弹出确认，调用 `POST /conversations/{id}/rollback` 进行三层回滚

---

### 5.2 后端 API 层（FastAPI）

**入口**: `src/ragent_backend/app.py`

#### 应用生命周期

`create_app()` 是异步工厂函数，初始化以下内容：

1. **CORS 中间件**：允许所有来源（开发环境配置）
2. **配置加载**：`load_settings()` 从 `config/settings.yaml` 读取
3. **存储组件**：
   - `checkpointer`：优先 PostgresSaver，回退 AsyncSqliteSaver，最后兜底 InMemorySaver
   - `archive_store`：MySQL 对话归档
   - `file_store`：SQLite 文件元数据管理
   - `conversation_store`：SQLite 对话元数据管理
4. **LLM 初始化**：`ChatOpenAI`（默认 `qwen3.5-omni-flash`）
5. **RAGWorkflow 实例**：绑定上述所有依赖

#### 核心路由

| 路由 | 功能 |
|:---|:---|
| `POST /api/v1/conversations` | 创建新对话 |
| `GET /api/v1/conversations` | 获取对话列表（按更新时间倒序） |
| `DELETE /api/v1/conversations/{id}` | 软删除对话及关联文件 |
| `POST /api/v1/conversations/{id}/files` | 上传文件，启动后台 ingest |
| `GET /api/v1/conversations/{id}/files` | 列出对话文件 |
| `DELETE /api/v1/conversations/{id}/files/{file_id}` | 删除文件 |
| `POST /api/v1/chat` | 非流式对话 |
| `POST /api/v1/chat/stream` | **真流式对话（SSE）** |
| `GET /api/v1/history/{id}` | 获取完整对话历史（从 MySQL） |
| `GET /api/v1/memory/{id}/stats` | 获取 checkpoint 记忆统计 |
| `POST /api/v1/conversations/{id}/rollback` | 回溯到指定消息 |
| `WS /ws/trace/{id}` | LangGraph 实时追踪 |

#### Checkpoint 回滚机制

这是本项目在 LangGraph 工程化上的一个亮点，包含两个层面的回滚：

**1. 流式中断回滚（自动）**
- 在 `chat_stream` 开始时，记录"干净 checkpoint id"
- 如果用户中断流式输出（关闭连接或点击停止），调用 `_trim_checkpoints()`
- **物理删除**该 thread 下所有在干净 checkpoint 之后产生的脏状态
- 语义效果：下一轮加载该 conversation 时，状态完全恢复到本轮开始之前

**2. 消息级 rollback（手动）**
- 接口：`POST /api/v1/conversations/{id}/rollback`
- 请求体：`{ "target_message_id": "..." }`
- 执行逻辑（三层裁剪）：
  1. **Checkpoint 层**：通过 `checkpointer.alist()` 找到目标消息对应的 checkpoint，物理删除该 checkpoint 及之后的所有 checkpoints/writes
  2. **Storage 层**：在 MySQL `conversation_archive` 中按 `turn_id` 删除该消息所在轮次及之后的所有归档记录
  3. **Memory 层**：在 SQLite `ltm.db` 中按 `conversation_id` + `turn_id` 删除对应的长期记忆事实
- 语义：点击第 N 轮的 checkpoint 圆点，保留前 N-1 轮，删除第 N 轮及之后的全部记录

---

### 5.3 RAG 工作流层（LangGraph）

**核心文件**: `src/ragent_backend/workflow.py`

#### 状态定义（RAGState）

`RAGState` 是 TypedDict，关键字段：

| 字段 | 说明 |
|:---|:---|
| `messages` | 对话历史（HumanMessage / AIMessage / RemoveMessage） |
| `summary` | 滚动摘要（旧消息压缩后的内容） |
| `memories` | LTM 长期记忆列表 |
| `query` / `rewritten_query` / `sub_queries` | 原始查询、重写后查询、子查询列表 |
| `retrieval_context` | 检索到的文本上下文 |
| `final_answer` | 最终生成的回答 |
| `trace_events` | 节点执行事件追踪 |
| `current_turn_id` | 每轮生成的唯一 ID，用于后续回滚 |

#### 节点流程

```
START → session → intent → [conditional] → retrieve → generate → memory_manage → archive → END
                              │
                              └── need_clarify=True ──→ archive → END
```

#### 各节点职责

| 节点 | 职责 |
|:---|:---|
| **session** | 初始化 conversation_id、task_id、turn_id；召回 LTM；确保消息 ID |
| **intent** | 结构化调用 `analyze_query()` 完成指代消解和子查询拆分；`detect_intent()` 判断是否需要澄清 |
| **retrieve** | 构建 `conv_{conversation_id}` collection，调用 `QueryKnowledgeHubTool.execute()` 执行混合检索 |
| **generate** | 整合 memories/summary/recent_history/retrieval_context 构建 Prompt；`llm.astream()` 流式生成 |
| **memory_manage** | 滑动窗口压缩：当消息数 > 20 时，保留最近 4 条，其余合并到 summary，返回 `RemoveMessage` |
| **archive** | 异步归档被压缩的消息 + 本轮新消息到 MySQL；异步提取 LTM 事实到 SQLite |

---

### 5.4 查询分析层（Intent & Query Analysis）

**核心文件**: `src/ragent_backend/intent.py`

#### 结构化输出模型

```python
class QueryAnalysisResult(BaseModel):
    rewritten_query: str   # 消除所有代词和指代后的完整查询
    sub_queries: List[str] # 拆分为独立子查询列表；否则只放一个元素
```

#### `analyze_query()` 流程

1. 取最近 4 条消息作为历史上下文
2. 构造 few-shot prompt，指示 LLM 完成：
   - **指代消解**：如将"它的性能怎么样"重写为"华为 Mate 60 搭载的麒麟 9000S 芯片的性能怎么样"
   - **子查询拆分**：如将"北京上海杭州的天气怎么样"拆分为 3 个子查询
3. 调用 `structured_llm.ainvoke(prompt)`
4. 异常时 fallback 到旧的 `rewrite_query()` + `split_parallel_subqueries()`

#### `detect_intent()`

基于规则 + LLM 的混合意图检测：
- 空查询 / 纯空白 → `need_clarify=True`
- 明显恶意注入 → `need_clarify=True`
- 模糊查询（如"这个呢？"但无历史）→ `need_clarify=True`
- 正常查询 → `need_clarify=False`

---

### 5.5 数据摄取流水线（Ingestion Pipeline）

**核心文件**: `src/ingestion/pipeline.py`

#### Pipeline 阶段总览

```
Stage 1: File Integrity Check   → SHA256 哈希 + SQLite 去重表
Stage 2: Document Loading       → UniversalLoader（PDF/DOCX/XLSX/PPTX/TXT/MD/HTML）
Stage 3: Document Chunking      → RecursiveSplitter
Stage 4: Transform Pipeline     → ChunkRefiner + MetadataEnricher + ImageCaptioner
Stage 5: Encoding               → DenseEncoder + SparseEncoder（BatchProcessor 调度）
Stage 6: Storage                → VectorUpserter (Chroma) + BM25Indexer + ImageStorage
```

#### 关键组件详解

##### Stage 1：完整性检查

`SQLiteIntegrityChecker` 维护 `data/db/ingestion_history.db`，记录每个文件的 hash、path、collection、status。如果同一文件（相同 SHA256）再次上传到同一 collection，且无 `--force`，则直接跳过，实现**幂等性**。

##### Stage 2：文档加载

`UniversalLoader`（`src/libs/loader/universal_loader.py`）是所有格式的统一入口，核心策略是：**先用 MarkItDown 统一解析为 Markdown 文本，再对 PDF 做特殊增强（扫描件检测 + VLM OCR fallback + 图片提取）**。

**通用流程（所有格式）**
1. 扩展名校验：`.pdf`, `.docx`, `.xlsx`, `.pptx`, `.txt`, `.md`, `.csv`, `.html`, `.json`, `.yaml`, `.yml`
2. 计算 SHA256 生成 `doc_id`
3. `MarkItDown` 统一解析为 Markdown 文本
4. 提取 title（优先一级标题 `# Title`，否则取首行非空文本）

**PDF 的差异化处理（两条互斥分支）**

*分支 A：扫描件 PDF → VLM OCR*
- **检测逻辑**：若 PDF 全文去空后字符数 `< 100` 或平均每页字符数 `< 30`，则判定为扫描件/图片型 PDF
- **处理流程**：
  1. `PyMuPDF` 将每页按 **200 DPI** 渲染为临时 PNG
  2. 并发调用 `Vision LLM`（如 `qwen-vl-max`）做 OCR，Prompt 为 `"请提取这张图片中的所有文字..."`
  3. 按页组装为纯文本（带 `--- Page X ---` 分隔）
  4. `extract_method = "vlm_ocr"`
- **结果特点**：只得到文字，**不保留页面截图**，**不生成 `[IMAGE: id]` 占位符**，后续 `ImageCaptioner` 直接跳过

*分支 B：正常 PDF → 文本提取 + 内嵌图片提取*
- `MarkItDown` 提取可搜索文本层
- `PyMuPDF` 遍历 `page.get_images()`，将 PDF **内嵌图片资源**提取到：
  ```
  data/images/{collection}/{doc_hash}/{image_id}.{ext}
  ```
- 在 Markdown 文本**末尾**插入占位符：`[IMAGE: {image_id}]`
- `metadata["images"]` 记录图片的 id、path、page、尺寸等元数据
- `extract_method = "markitdown"`

**其他格式处理**
- **DOCX**：`MarkItDown` 调用 `python-docx` 转 Markdown，保留标题和表格结构
- **XLSX/XLS**：表格转为 Markdown Table，多 sheet 按 sheet 名分段
- **PPTX**：按幻灯片顺序提取，每页标题+正文转 Markdown
- **HTML**：去标签保留正文和标题层级
- **TXT/MD/CSV/JSON/YAML**：直接读取或保留结构

##### Stage 3：分块

`DocumentChunker` 使用 `RecursiveSplitter`，按 `chunk_size=1000` 和 `chunk_overlap=200` 进行层次化拆分。每个 chunk 生成唯一 ID：`{doc_id}_{chunk_index}_{hash}`。

##### Stage 4：Transform Pipeline

这是摄取链路中 LLM 调用最密集的阶段，包含三个串行但**内部并行**的子阶段：

**a) ChunkRefiner（块精炼）**
- 规则清洗：去页眉页脚、HTML 标签、归一化空白
- LLM 改写：通过 `ThreadPoolExecutor(max_workers=3)` 并行调用 LLM 对每个 chunk 进行内容改写和连贯性增强
- 失败时 fallback 到规则清洗结果

**b) MetadataEnricher（元数据增强）**
- 规则层：提取 title（首行/首句）、summary（前 3 句）、tags（专有名词/代码标识符/Markdown 强调词）
- LLM 层：并行调用 LLM 生成更丰富的 title/summary/tags
- 输出写入 `chunk.metadata`

**c) ImageCaptioner（图片描述）**
- 扫描 chunk 文本中的 `[IMAGE: id]` 占位符
- 收集所有唯一图片，通过 `ThreadPoolExecutor(max_workers=3)` 并行调用 Vision LLM 生成 caption
- 使用线程安全的 `_caption_cache` 避免同一图片重复调用 API
- 将 caption 插入文本占位符旁边

**并发控制**：
- 三个子阶段之间是**串行**的（存在数据依赖）
- 每个子阶段内部通过 `ThreadPoolExecutor` 对 chunks/images 并行处理
- 全局并发：`settings.yaml` 中 `ingestion.max_workers=3`
- 应用层：`INGEST_SEMAPHORE(2)` 限制同时运行的 pipeline 实例数

##### Stage 5：编码

`BatchProcessor` 协调：
- `DenseEncoder`：调用 Embedding API 生成语义向量
- `SparseEncoder`：基于 `jieba` 分词生成 BM25 所需的词频统计

##### Stage 6：存储

- **ChromaDB**：`VectorUpserter` 将 dense vectors 写入对应 collection
- **BM25**：将 sparse stats 写入 `data/db/bm25/{collection}/{collection}_bm25.json`
- **ImageStorage**：将图片元数据登记到 SQLite `image_index.db`

**ID 对齐**：BM25 命中后需要通过 `chunk_id` 从向量库取回文本，因此 pipeline 在写入后将 `vector_ids[i]` 回写到 `sparse_stats[i]["chunk_id"]`。

##### 失败回滚

如果任何阶段抛出异常：
1. 调用 `_rollback_storage()` 删除已写入的 vectors、BM25 索引、图片登记
2. 在 integrity checker 中标记失败状态
3. 返回 `PipelineResult(success=False, error=...)`

---

### 5.6 检索引擎（HybridSearch）

**核心文件**: `src/core/query_engine/hybrid_search.py`

#### 整体流程

```
query → QueryProcessor → [parallel] DenseRetriever + SparseRetriever → RRFFusion → optional Rerank → final results
```

#### QueryProcessor

对原始查询进行预处理：
- 提取关键词（用于 BM25 稀疏检索）
- 提取元数据过滤条件（如 `collection:xxx`）

#### DenseRetriever

- 将查询文本通过 Embedding API 编码为向量
- 在 ChromaDB 指定 collection 中进行近似最近邻搜索（ANN）
- 返回 `List[RetrievalResult]`

#### SparseRetriever

- 使用 `jieba` 对查询关键词进行中文分词
- 加载对应 collection 的 BM25 索引（每次查询都重新从磁盘加载，保证数据新鲜）
- 计算 BM25 分数，返回 top-k 结果

#### RRFFusion

使用 Reciprocal Rank Fusion 算法融合两路结果：

```
score_rrf(chunk) = sum(1 / (k + rank_in_list))   # k 默认 60
```

#### 优雅降级

| 场景 | 处理策略 |
|:---|:---|
| Dense 失败，Sparse 成功 | 仅返回 Sparse 结果 |
| Sparse 失败，Dense 成功 | 仅返回 Dense 结果 |
| 两路都失败 | 抛出 RuntimeError |
| 一路无结果 | 使用另一路结果，不走空融合 |

#### Reranker（可选）

`src/core/query_engine/reranker.py` 支持两种：
- **Cross-Encoder**：本地加载 `cross-encoder/ms-marco-MiniLM-L-6-v2`
- **LLM Reranker**：调用 LLM 对候选文档进行相关性打分（0-5 分）

Reranker 失败后也会 graceful fallback 到 RRF 融合结果。

---

### 5.7 MCP Server 与工具层

**核心文件**: `src/mcp_server/server.py`、`src/mcp_server/tools/query_knowledge_hub.py`

#### MCP Server 架构

- **传输层**：stdio（标准输入输出）
- **协议层**：官方 Python MCP SDK
- **日志重定向**：所有日志强制输出到 `stderr`，避免污染 `stdout` 上的 JSON-RPC 报文
- **预加载优化**：在主线程预先 import `chromadb`、`hybrid_search` 等重型模块，避免后台线程触发 import lock 竞争导致卡死

#### 注册的工具（3 个）

| 工具名 | 功能 |
|:---|:---|
| `query_knowledge_hub` | 混合检索知识库，返回带引用的格式化 Markdown 结果 |
| `list_collections` | 列出所有 collection 及其文档统计 |
| `get_document_summary` | 根据 doc_id 获取文档摘要 |

#### `query_knowledge_hub` 执行流程

1. **初始化组件**：根据 `collection` 参数重建 VectorStore、DenseRetriever、SparseRetriever、HybridSearch；embedding client 和 reranker 缓存复用
2. **Hybrid Search**：`asyncio.to_thread(self._perform_search, ...)`
3. **Rerank**：如果启用，在线程池中执行
4. **ResponseBuilder 格式化**：将 `RetrievalResult` 列表渲染为 Markdown 文本，附带引用信息（`source_file`、`page`、`chunk_id`、`score`）
5. **Trace 记录**：将查询全链路 trace 收集到 `TraceCollector`

---

### 5.8 记忆与存储系统

#### 三层记忆架构

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

#### 滑动窗口机制

```python
# RollingMemoryManager
max_messages = 20  # 超过则触发压缩
keep_recent = 4    # 保留最近 4 条

# 当消息数 > 20:
# 1. 保留最近 4 条消息
# 2. 其余 16 条传给 LLM 生成摘要，与现有 summary 合并
# 3. 返回 RemoveMessage(id=...)，LangGraph 自动从 checkpoint 中删除
```

#### 三层回滚与 `current_turn_id`

`RAGWorkflow` 在 `session_node` 中为每轮对话生成唯一的 `current_turn_id`（UUID），并在 `archive_node` 中将该 `turn_id` 写入：
- MySQL `conversation_archive` 表（每条消息都带 `turn_id`）
- SQLite `ltm.db`（每条长期记忆事实都带 `conversation_id` + `turn_id`）

这使得消息级 rollback 成为可能：前端点击 checkpoint 圆点时，传入的是该 turn 的 user 消息 `message_id`，后端通过它反查 `turn_id`，然后同步清理 checkpoint、archive、ltm 三层数据。

#### 各存储层说明

| 存储 | 用途 | 实现 | 技术 |
|:---|:---|:---|:---|
| **Checkpoint** | 给模型用的上下文（短期记忆） | LangGraph Checkpointer | SQLite（开发）/ Postgres（生产） |
| **Archive Store** | 用户可见的完整历史 | `ConversationArchiveStore` | MySQL (`conversation_archive` 表) |
| **LTM Store** | 跨会话长期记忆 | `LTMStore` | SQLite (`ltm.db`) |
| **File Store** | 文件元数据与状态追踪 | `ConversationFileStore` | SQLite |
| **Conversation Store** | 对话元数据（标题、消息数、文件数、状态） | `ConversationStore` | SQLite |

#### LTM Store

`LTMStore`（`src/ragent_backend/ltm_store.py`）基于 SQLite：

- `extract_facts(query, answer, llm)`：异步调用 LLM 从本轮 Q&A 中提取结构化事实（如"用户是软件工程师"、"用户偏好中文回答"）
- `save_facts(user_id, facts)`：写入 `ltm.db`
- `retrieve_facts(user_id, query, top_k)`：通过 BM25 检索与用户当前查询相关的长期记忆

---

### 5.9 可观测性与评估

#### Trace 系统

**核心文件**: `src/core/trace/trace_context.py`、`trace_collector.py`

每个 query 和 ingestion 都会生成一个 `TraceContext`，记录各阶段耗时与数据快照：
- `trace.record_stage(name, data, elapsed_ms)`
- `TraceCollector().collect(trace)` 将 trace 追加到 `logs/traces.jsonl`

#### Streamlit Dashboard

**入口**: `scripts/start_dashboard.py`

提供 6 个页面：

1. **Overview**：组件配置卡片、集合统计、Trace 统计
2. **Data Browser**：按 collection 浏览文档、chunk、metadata、图片预览
3. **Ingestion Manager**：文件上传、摄取进度、文档删除
4. **Ingestion Traces**：摄取历史列表、阶段瀑布图、各 Tab 详情
5. **Query Traces**：查询历史列表、关键词过滤、Ragas Evaluate 按钮
6. **Evaluation Panel**：选择 evaluator（ragas/custom/composite）、运行评估、查看历史记录

#### 评估体系

- **RagasEvaluator**：集成 `ragas` 库，计算 `faithfulness`、`answer_relevancy`、`context_precision`
- **CustomEvaluator**：基于 hit_rate 和 MRR 的检索质量评估
- **EvalRunner**：读取 `tests/fixtures/golden_test_set.json`，自动对每条查询执行检索并打分
- **Benchmark 脚本**：`benchmark_rag.py`（项目根目录），用于回归测试，覆盖：
  - Query Analysis（指代消解 + 子查询拆分）
  - Ingestion Pipeline + Trace
  - RAG Retrieval Quality

---

## 六、技术栈清单

| 层级 | 技术 |
|:---|:---|
| **前端** | Vue 3, Vite, Element Plus |
| **后端框架** | FastAPI, Uvicorn |
| **Agent 框架** | LangGraph 1.1.6, LangChain OpenAI |
| **向量数据库** | ChromaDB |
| **稀疏检索** | BM25 + jieba |
| **重排序** | Cross-Encoder (sentence-transformers) / LLM Reranker |
| **文档解析** | markitdown (PDF), python-docx, openpyxl, python-pptx, 自定义 TextLoader |
| **Embedding** | OpenAI/Azure/DashScope 兼容 API |
| **LLM** | OpenAI/Azure/DeepSeek/Ollama 可切换 |
| **Vision LLM** | Azure OpenAI GPT-4V / Qwen-VL |
| **关系存储** | MySQL (aiomysql), SQLite, Postgres |
| **可观测性** | Streamlit, JSONL trace logs, Ragas |
| **协议** | MCP (stdio), SSE, WebSocket |
| **评估** | Ragas, Custom Evaluator, pytest |

---

## 七、项目目录结构

```
rag-pro/
├── frontend/                         # Vue 3 前端
│   ├── src/
│   │   ├── App.vue                  # 主界面（聊天+文件+Trace面板）
│   │   ├── components/
│   │   │   └── TracePanel.vue       # LangGraph Trace 可视化
│   │   ├── main.js
│   │   └── views/
│   └── package.json
│
├── src/
│   ├── ragent_backend/              # FastAPI 后端
│   │   ├── app.py                   # API 入口，路由定义，后台任务
│   │   ├── workflow.py              # LangGraph RAG 工作流
│   │   ├── file_store.py            # 文件存储管理（SQLite + 磁盘）
│   │   ├── conversation_store.py    # 对话元数据管理（SQLite）
│   │   ├── memory_manager.py        # 滑动窗口记忆压缩逻辑
│   │   ├── store.py                 # 对话归档存储（MySQL）
│   │   ├── ltm_store.py             # 长期记忆存储（SQLite）
│   │   ├── intent.py                # 意图识别与查询分析
│   │   └── schemas.py               # Pydantic 模型与 RAGState
│   │
│   ├── core/                        # 核心引擎
│   │   ├── query_engine/            # 混合检索引擎
│   │   │   ├── hybrid_search.py     # HybridSearch 主编排
│   │   │   ├── dense_retriever.py   # 稠密检索
│   │   │   ├── sparse_retriever.py  # 稀疏检索（BM25）
│   │   │   ├── fusion.py            # RRF 融合
│   │   │   ├── reranker.py          # 重排序器
│   │   │   └── query_processor.py   # 查询预处理
│   │   ├── response/                # 响应构建
│   │   │   ├── response_builder.py  # MCP Tool Response 格式化
│   │   │   ├── citation_generator.py
│   │   │   └── multimodal_assembler.py
│   │   ├── trace/                   # Trace 系统
│   │   │   ├── trace_context.py
│   │   │   └── trace_collector.py
│   │   ├── settings.py              # 配置加载
│   │   └── types.py                 # 核心类型定义
│   │
│   ├── ingestion/                   # 文档摄取流水线
│   │   ├── pipeline.py              # 6 阶段 IngestionPipeline
│   │   ├── chunking/
│   │   │   └── document_chunker.py
│   │   ├── transform/
│   │   │   ├── chunk_refiner.py     # 块精炼
│   │   │   ├── metadata_enricher.py # 元数据增强
│   │   │   └── image_captioner.py   # 图片描述
│   │   ├── embedding/
│   │   │   ├── dense_encoder.py
│   │   │   ├── sparse_encoder.py
│   │   │   └── batch_processor.py
│   │   └── storage/
│   │       ├── vector_upserter.py   # Chroma 向量写入
│   │       ├── bm25_indexer.py      # BM25 索引管理
│   │       └── image_storage.py     # 图片索引
│   │
│   ├── mcp_server/                  # MCP 协议实现
│   │   ├── server.py                # MCP Server 启动入口
│   │   ├── protocol_handler.py      # 协议处理器
│   │   └── tools/
│   │       ├── query_knowledge_hub.py
│   │       ├── list_collections.py
│   │       └── get_document_summary.py
│   │
│   ├── libs/                        # 底层库抽象
│   │   ├── embedding/               # Embedding 工厂（OpenAI/Azure/Ollama）
│   │   ├── llm/                     # LLM 工厂（OpenAI/Azure/DeepSeek/Ollama）
│   │   ├── loader/                  # 文档加载器
│   │   ├── vector_store/            # 向量存储工厂（Chroma/Qdrant/Pinecone）
│   │   ├── splitter/                # 文本切分器
│   │   └── evaluator/               # 评估器基类
│   │
│   └── observability/               # 可观测性与评估
│       ├── logger.py
│       ├── dashboard/
│       │   ├── app.py               # Streamlit 入口
│       │   ├── pages/               # 6 个 Dashboard 页面
│       │   └── services/            # 数据服务
│       └── evaluation/
│           ├── ragas_evaluator.py
│           ├── custom_evaluator.py
│           ├── composite_evaluator.py
│           └── eval_runner.py
│
├── tests/                           # 测试套件
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   └── fixtures/
│
├── scripts/                         # 工具脚本
│   ├── init_mysql.py                # 数据库初始化
│   └── start_dashboard.py           # 启动观测面板
│
├── config/
│   └── settings.yaml                # 主配置文件
│
├── data/                            # 数据存储
│   ├── uploads/                     # 上传的原始文件
│   ├── db/                          # SQLite / Chroma / BM25
│   └── images/                      # 提取的图片
│
├── logs/                            # Trace 日志
├── benchmark_rag.py                 # 回归测试基准脚本
├── pyproject.toml                   # Python 项目配置
└── README.md / PROJECT_OVERVIEW.md / TECHNICAL_OVERVIEW.md
```

---

## 八、关键工程决策与已知限制

### 8.1 关键工程决策

| 决策 | 原因 | 效果 |
|:---|:---|:---|
| **LangGraph checkpoint 回滚** | 用户打断后需要语义上"忽略本轮" | 物理删除脏 checkpoint，下一次加载完全干净 |
| **analyze_query 合并为一次结构化调用** | 减少 LLM 调用次数，降低延迟和成本 | benchmark 准确率 0.67 → 0.96 |
| **对话级 collection 隔离** | 不同对话的知识库必须互不干扰 | 通过 `conv_{conversation_id}` 命名实现 |
| **BM25 chunk_id 与 vector_id 对齐** | 稀疏检索命中后需要从向量库取文本 | 保证 HybridSearch 融合阶段数据一致 |
| **全局 INGEST_SEMAPHORE** | 防止并发 ingest 打爆 LLM API 配额 | 限制同时执行 ingest 任务数为 2 |
| **MCP 预加载重型模块** | 避免后台线程 import lock 竞争卡死 | stdio 传输稳定，无 JSON-RPC 污染 |

### 8.2 已知限制

1. **Ingestion 大文件仍有延迟**：已增加 Semaphore 和 max_workers 控制，但 50+ 页 PDF 的 LLM transform 总量仍然可观。后续如需彻底解耦，应引入 Celery/Redis 持久化队列。
2. **Transform 子阶段间串行**：`refiner → enricher → captioner` 存在数据依赖，无法简单并行。
3. **流式输出为模拟（非 LLM 原生流式限制）**：当前通过 `llm.astream()` 实现真流式，但部分场景下仍可能是完整生成后分块发送（取决于底层模型提供商）。
4. **跨页表格无特殊处理**：当前 `MarkItDown` + `RecursiveSplitter` 对跨页表格是"硬切"的，没有合并、补全或结构保护机制。表格头部可能只出现在第一个 chunk，导致检索时召回半截表格。
5. **扫描件 PDF 的图片不保留**：VLM OCR 路径只提取文字，不保存页面截图，也不生成 `[IMAGE: id]` 占位符，因此扫描件中的插图/图表无法被 `ImageCaptioner` 描述。
6. **图片未在前端显示**：已提取并建立索引，但前端尚未实现图片预览功能。

---

## 九、总结

RAG-Pro 是一个从**底层检索引擎**到**上层业务交互**都经过精心设计的生产级 RAG 系统：

- **检索层**：Dense + Sparse + RRF + Rerank 的混合检索，优雅降级
- **摄取层**：6 阶段 Pipeline，支持多格式文档、图片提取、LLM 增强
- **对话层**：LangGraph 状态机驱动，真流式 SSE，滑动窗口记忆，LTM 长期记忆
- **接入层**：同时提供 REST API 和 MCP 协议，兼顾 Web 应用和桌面 AI 客户端
- **可观测性**：Trace 系统 + Streamlit Dashboard + Ragas 评估，完整闭环

它不仅是"能跑通 RAG 的 Demo"，更是具备**会话隔离、记忆管理、并发控制、回滚机制、多协议接入、可观测性**的企业级知识库 Agent 底座。
