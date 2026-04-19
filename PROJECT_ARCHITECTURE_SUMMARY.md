# RAGent / Modular RAG MCP Server — 全量架构总结

> 本文档基于对项目完整源码的逐行阅读与推导生成，不引用任何既有文档，完全从代码层面还原系统架构。

---

## 一、项目定位与核心能力

**RAGent**（项目内部代号 `rag-pro`，PyPI 包名 `modular-rag-mcp-server`）是一个**企业级模块化 RAG（Retrieval-Augmented Generation）知识库系统**。其核心定位是：

- **会话级知识隔离**：每个对话（Conversation）拥有独立的向量集合（`conv_{conversation_id}`），文件上传后仅在该对话范围内可被检索。
- **多模态文档摄取**：支持 PDF、Word、Excel、PPT、Markdown、HTML、TXT、CSV、JSON、YAML 等格式的解析，支持图片提取与 VLM OCR 回退。
- **混合检索（Hybrid Search）**：稠密向量检索（Dense）+ 稀疏关键词检索（BM25）+ RRF 融合 + 重排序（Rerank）四级检索链路。
- **Agentic 对话工作流**：基于 LangGraph 的状态机工作流，集成意图识别、查询重写、子查询拆分、检索、生成、记忆压缩、归档的全闭环。
- **MCP 协议原生支持**：内置 MCP Server，可通过 stdio 与任意 MCP Client（如 Claude Desktop、Cursor）对接。
- **长期记忆（LTM）**：跨会话的用户事实记忆自动提取与召回。
- **三层时间裁剪回滚**：支持对话回溯到任意消息节点，同时清理 LangGraph Checkpoint、PostgreSQL 归档消息、LTM 记忆三层数据。
- **全链路可观测**：Trace 上下文贯穿摄取与查询全流程，支持 WebSocket 实时推送、Streamlit Dashboard、Ragas 自动评估。

---

## 二、完整技术栈

| 层级 | 技术选型 | 说明 |
|------|---------|------|
| **Python 运行时** | Python 3.10+ | 项目要求 `>=3.10`，实际开发基于 3.12 |
| **Web 框架** | FastAPI + Uvicorn | 后端 API 服务，异步 ASGI |
| **Workflow 引擎** | LangGraph + LangChain Core | Agent 状态图编排，checkpoint 持久化 |
| **LLM 接入** | LangChain OpenAI + 自定义 Provider | 支持 OpenAI、Azure、DeepSeek、Ollama、DashScope（阿里云百炼）等 |
| **Embedding 接入** | 工厂模式封装 | 支持 OpenAI、Azure、Ollama 等 Embedding 服务 |
| **向量数据库** | ChromaDB（PersistentClient） | 本地 SQLite 持久化，支持多 collection |
| **稀疏索引** | 自研 BM25（基于 `rank-bm25`） | 每 collection 独立 JSON 索引文件 |
| **关系数据库** | PostgreSQL（`asyncpg` 异步连接池） | 对话元数据、归档消息、文件元数据、LTM 记忆 |
| **Checkpoint 存储** | PostgreSQL（`AsyncPostgresSaver`） | LangGraph 状态持久化 |
| **前端框架** | Vue 3（Composition API）+ Vite | SPA 单页应用 |
| **UI 组件库** | Element Plus + Element Plus Icons Vue | 企业级组件库 |
| **前端构建** | Vite（含 rollup、esbuild） | 开发服务器与生产打包 |
| **MCP 协议** | 官方 Python MCP SDK（`mcp>=1.0.0`） | JSON-RPC 2.0 over stdio |
| **文档解析** | `markitdown[pdf,docx]` + `python-docx` + `openpyxl` + `python-pptx` | 多格式统一转 Markdown |
| **中文分词** | `jieba` | QueryProcessor 与 SparseEncoder 共用 |
| **Dashboard** | Streamlit | 多页可观测性面板 |
| **评估框架** | Ragas + 自定义 Evaluator | 端到端 RAG 质量评估 |
| **配置管理** | PyYAML + 环境变量覆盖 | `config/settings.yaml` 为主配置 |
| **日志与 Trace** | 结构化日志 + JSON Lines 文件 | `logs/traces.jsonl` |
| **项目管理** | `pyproject.toml`（Hatchling 构建后端） | 现代 Python 包管理 |

---

## 三、目录结构全景

```
rag-pro/
├── main.py                          # MCP Server 入口（配置校验与启动占位）
├── benchmark_rag.py                 # 全链路基准测试脚本
├── pyproject.toml                   # 包定义与依赖
├── start.bat                        # Windows 快速启动脚本
│
├── config/
│   ├── settings.yaml                # 主配置文件（LLM/Embedding/检索/摄取等）
│   ├── test_credentials.yaml        # 测试凭据（独立文件，不提交）
│   └── prompts/                     # LLM Prompt 模板文件
│       ├── chunk_refinement.txt
│       ├── image_captioning.txt
│       ├── metadata_enrichment.txt
│       └── rerank.txt
│
├── src/                             # ===== 主源码 =====
│   ├── core/                        # 核心层：类型定义、配置、检索引擎、响应构建、Trace
│   │   ├── settings.py              # Settings 配置加载与校验（dataclass + YAML）
│   │   ├── types.py                 # 全链路核心数据类型（Document/Chunk/ChunkRecord/RetrievalResult/ProcessedQuery）
│   │   ├── query_engine/            # 查询引擎
│   │   │   ├── query_processor.py   # 查询预处理（jieba 分词、停用词过滤、过滤器语法解析）
│   │   │   ├── dense_retriever.py   # 稠密检索器（Embedding 相似度）
│   │   │   ├── sparse_retriever.py  # 稀疏检索器（BM25 关键词检索）
│   │   │   ├── hybrid_search.py     # 混合检索编排（并行 Dense+Sparse → RRF 融合 → 元数据过滤）
│   │   │   ├── fusion.py            # RRF（Reciprocal Rank Fusion）算法实现
│   │   │   └── reranker.py          # CoreReranker（封装 libs.reranker，带降级策略）
│   │   ├── response/                # 响应构建
│   │   │   ├── response_builder.py  # MCP 格式响应构建（Markdown + 引用 + 多模态图片）
│   │   │   ├── citation_generator.py# 引用标记生成器
│   │   │   └── multimodal_assembler.py # 多模态内容组装（图片块提取）
│   │   └── trace/                   # 可观测性 Trace
│   │       ├── trace_context.py     # TraceContext（阶段计时、元数据、序列化）
│   │       └── trace_collector.py   # TraceCollector（持久化到 JSON Lines）
│   │
│   ├── ingestion/                   # 摄取层：文档摄入全流水线
│   │   ├── pipeline.py              # IngestionPipeline（6 阶段编排主控）
│   │   ├── document_manager.py      # 跨存储文档生命周期管理（级联删除）
│   │   ├── chunking/
│   │   │   └── document_chunker.py  # Document → Chunk（ID 生成、元数据继承）
│   │   ├── embedding/
│   │   │   ├── dense_encoder.py     # 稠密向量编码（调用 EmbeddingFactory）
│   │   │   ├── sparse_encoder.py    # 稀疏向量编码（jieba 分词 + TF 统计）
│   │   │   └── batch_processor.py   # 批处理调度（Dense + Sparse 并行）
│   │   ├── storage/
│   │   │   ├── vector_upserter.py   # 向量入库（ChromaDB）
│   │   │   ├── bm25_indexer.py      # BM25 索引构建与维护
│   │   │   └── image_storage.py     # 图片索引登记（SQLite 元数据）
│   │   └── transform/
│   │       ├── base_transform.py    # 变换基类
│   │       ├── chunk_refiner.py     # Chunk 精炼（LLM/规则：段落合并、连贯性增强）
│   │       ├── metadata_enricher.py # 元数据增强（LLM/规则：标题、标签、摘要提取）
│   │       └── image_captioner.py   # 图片 caption 生成（VLM 调用）
│   │
│   ├── libs/                        # 基础设施层：第三方能力封装
│   │   ├── llm/                     # LLM Provider 工厂与实现
│   │   │   ├── llm_factory.py       # LLM 工厂（文本 + Vision 双注册表）
│   │   │   ├── base_llm.py          # 文本 LLM 抽象基类
│   │   │   ├── base_vision_llm.py   # Vision LLM 抽象基类
│   │   │   ├── openai_llm.py        # OpenAI 兼容接口实现
│   │   │   ├── azure_llm.py         # Azure OpenAI 实现
│   │   │   ├── deepseek_llm.py      # DeepSeek 实现
│   │   │   ├── ollama_llm.py        # Ollama 本地模型实现
│   │   │   ├── openai_vision_llm.py # OpenAI Vision 实现
│   │   │   └── azure_vision_llm.py  # Azure Vision 实现
│   │   ├── embedding/               # Embedding Provider 工厂与实现
│   │   │   ├── embedding_factory.py # Embedding 工厂
│   │   │   ├── base_embedding.py    # 抽象基类
│   │   │   ├── openai_embedding.py  # OpenAI 兼容 Embedding
│   │   │   ├── azure_embedding.py   # Azure Embedding
│   │   │   └── ollama_embedding.py  # Ollama Embedding
│   │   ├── vector_store/            # 向量存储工厂与实现
│   │   │   ├── vector_store_factory.py
│   │   │   ├── base_vector_store.py
│   │   │   └── chroma_store.py      # ChromaDB 实现
│   │   ├── reranker/                # 重排序器工厂与实现
│   │   │   ├── reranker_factory.py  # Reranker 工厂（llm/cross_encoder/dashscope/none）
│   │   │   ├── base_reranker.py     # 抽象基类 + NoneReranker（透传）
│   │   │   ├── llm_reranker.py      # 基于 LLM 的重排序
│   │   │   ├── cross_encoder_reranker.py # 交叉编码器重排序
│   │   │   └── dashscope_reranker.py # 阿里云 DashScope 重排序
│   │   ├── splitter/                # 文本切分器工厂与实现
│   │   │   ├── splitter_factory.py  # Splitter 工厂
│   │   │   ├── base_splitter.py     # 抽象基类
│   │   │   └── recursive_splitter.py # 递归字符切分（LangChain TextSplitters 封装）
│   │   ├── loader/                  # 文档加载器
│   │   │   ├── base_loader.py       # 加载器抽象基类
│   │   │   ├── universal_loader.py  # 通用加载器（按扩展名分发）
│   │   │   ├── pdf_loader.py        # PDF 加载（markitdown + VLM OCR 回退）
│   │   │   ├── text_loader.py       # 纯文本加载
│   │   │   └── file_integrity.py    # 文件完整性校验（SHA256 + SQLite 历史表）
│   │   └── evaluator/               # 评估器工厂与实现
│   │       ├── evaluator_factory.py
│   │       ├── base_evaluator.py
│   │       ├── custom_evaluator.py
│   │       └── source_evaluator.py
│   │
│   ├── ragent_backend/              # 后端 API 与 Agent 工作流
│   │   ├── app.py                   # FastAPI 应用工厂（全部 API 端点定义）
│   │   ├── workflow.py              # RAGWorkflow（LangGraph 状态图编排）
│   │   ├── schemas.py               # Pydantic 模型与 RAGState TypedDict
│   │   ├── intent.py                # 意图识别（analyze_query + detect_intent + 子查询拆分）
│   │   ├── memory_manager.py        # RollingMemoryManager（滑动窗口记忆压缩）
│   │   ├── ltm_store.py             # LTMStore（长期记忆提取/召回/存储）
│   │   ├── store.py                 # ConversationArchiveStore（PostgreSQL 归档）
│   │   ├── conversation_store.py    # ConversationStore（PostgreSQL 对话元数据）
│   │   ├── file_store.py            # ConversationFileStore（PostgreSQL + 磁盘文件）
│   │   └── mcp_adapter.py           # RAGMCPClient（本地 MCP 工具桥接适配器）
│   │
│   ├── mcp_server/                  # MCP 协议服务器
│   │   ├── server.py                # stdio 服务入口（日志重定向 + 预加载）
│   │   ├── protocol_handler.py      # ProtocolHandler（工具注册、JSON-RPC 路由）
│   │   └── tools/                   # MCP 工具实现
│   │       ├── query_knowledge_hub.py   # 知识检索工具（HybridSearch + Rerank）
│   │       ├── list_collections.py      # 集合列表工具
│   │       └── get_document_summary.py  # 文档摘要工具
│   │
│   └── observability/               # 可观测性与评估
│       ├── logger.py                # 结构化日志封装
│       ├── evaluation/              # 评估执行器
│       │   ├── rag_eval_runner.py   # RAGEvalRunner（端到端评估编排）
│       │   ├── ragas_evaluator.py   # RagasEvaluator（Ragas 指标封装）
│       │   ├── composite_evaluator.py
│       │   └── eval_runner.py
│       └── dashboard/               # Streamlit 可观测面板
│           ├── app.py               # 多页导航入口
│           ├── pages/               # 各页面实现
│           │   ├── overview.py
│           │   ├── data_browser.py
│           │   ├── ingestion_manager.py
│           │   ├── ingestion_traces.py
│           │   ├── query_traces.py
│           │   └── evaluation_panel.py
│           └── services/            # Dashboard 数据服务层
│               ├── data_service.py
│               ├── trace_service.py
│               └── config_service.py
│
├── frontend/                        # ===== 前端应用 =====
│   ├── index.html                   # HTML 入口
│   ├── package.json                 # Node 依赖（Vue 3 + Element Plus + Axios）
│   ├── vite.config.js               # Vite 配置
│   └── src/
│       ├── main.js                  # Vue 应用入口（全局图标注册）
│       ├── App.vue                  # 根组件（完整聊天界面：侧边栏+聊天区+Trace面板）
│       └── components/
│           └── TracePanel.vue       # LangGraph 节点级追踪面板
│
├── scripts/                         # ===== 运维与测试脚本 =====
│   ├── ingest.py                    # 单文件摄取 CLI
│   ├── query.py                     # 单查询检索 CLI
│   ├── evaluate.py                  # 评估执行 CLI
│   ├── run_ablation.py              # 消融实验（检索组件对比）
│   ├── run_ragas_evaluation.py      # Ragas 批量评估
│   ├── setup_mmarco_benchmark.py    # MS MARCO 中文基准设置
│   ├── precache_mmarco_embeddings.py# 预缓存 Embedding
│   ├── start_dashboard.py           # 启动 Streamlit Dashboard
│   ├── check_metrics_threshold.py   # 指标阈值检查
│   ├── check_writes_schema.py       # 数据库 Schema 检查
│   ├── generate_test_set.py         # 生成黄金测试集
│   ├── ingest_sample_docs.py        # 批量摄入示例文档
│   ├── ingest_missing_docs.py       # 补漏摄入
│   ├── populate_chunk_ids.py        # 补全 chunk ID
│   ├── init_postgres.py             # PostgreSQL 初始化
│   ├── list_tables.py               # 数据库表列表
│   ├── init_mysql.sql               # MySQL 初始化 SQL（历史兼容）
│   └── test_*.py                    # 各类测试脚本（流式、WebSocket、Checkpoint 回滚等）
│
├── data/                            # ===== 数据目录 =====
│   ├── uploads/                     # 用户上传原始文件（按 conversation_id 隔离）
│   ├── images/                      # 提取的图片（按 collection / doc_hash 隔离）
│   ├── db/
│   │   ├── chroma/                  # ChromaDB 向量库持久化文件
│   │   ├── bm25/                    # BM25 索引（每 collection 一个 JSON）
│   │   ├── file_store.db            # SQLite：文件完整性历史
│   │   ├── image_index.db           # SQLite：图片索引
│   │   ├── ingestion_history.db     # SQLite：摄取历史
│   │   └── ltm.db                   # SQLite：长期记忆（LTM）
│   ├── benchmark_test_doc.txt       # 基准测试用文档
│   └── mmarco_chinese/              # MS MARCO 中文评测数据集
│
├── logs/                            # 日志与 Trace 输出
├── reports/                         # 评估报告（ablation 结果等）
├── docs/                            # 项目文档（api.md, TODO.md）
├── .github/                         # GitHub Skills（auto-coder、qa-tester 等 Agent 技能）
├── .claude/                         # Claude Skills（与 .github/skills 镜像）
└── .agent.md                        # Agent 元数据
```

---

## 四、分层架构详解

### 4.1 整体分层模型

系统采用**严格四层架构**，自上而下为：

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 4: 交互层（Presentation）                               │
│  - Frontend（Vue 3 SPA）                                      │
│  - MCP Client（stdio JSON-RPC）                               │
│  - Streamlit Dashboard                                        │
├─────────────────────────────────────────────────────────────┤
│  Layer 3: 应用层（Application）                                │
│  - FastAPI（REST + WebSocket）                                │
│  - RAGWorkflow（LangGraph 状态机）                             │
│  - MCP Server（ProtocolHandler + Tools）                      │
├─────────────────────────────────────────────────────────────┤
│  Layer 2: 核心域层（Core Domain）                              │
│  - IngestionPipeline（6 阶段摄取编排）                         │
│  - HybridSearch（Dense + Sparse + RRF + Rerank）              │
│  - ResponseBuilder（MCP 响应格式化）                           │
│  - TraceContext（全链路追踪）                                  │
├─────────────────────────────────────────────────────────────┤
│  Layer 1: 基础设施层（Infrastructure / Libs）                  │
│  - LLM / Embedding / VectorStore / Reranker / Splitter        │
│  - Document Loader / File Integrity                           │
│  - Evaluator                                                  │
└─────────────────────────────────────────────────────────────┘
```

---

### 4.2 配置层（`src/core/settings.py`）

配置系统是整个系统的**唯一真相源**，设计极度严谨：

- **路径解析与 CWD 无关**：通过 `Path(__file__).resolve().parents[2]` 锚定仓库根目录，确保无论从何处启动都能正确找到 `config/settings.yaml`。
- **强类型 dataclass**：`Settings` 由 9 个子配置组成，全部为 `frozen=True` 不可变对象：
  - `LLMSettings`：provider、model、temperature、max_tokens、api_key、base_url 等
  - `EmbeddingSettings`：provider、model、dimensions 等
  - `VectorStoreSettings`：provider、persist_directory、collection_name
  - `RetrievalSettings`：dense_top_k、sparse_top_k、fusion_top_k、rrf_k
  - `RerankSettings`：enabled、provider、model、top_k
  - `EvaluationSettings`：enabled、provider、metrics 列表
  - `ObservabilitySettings`：log_level、trace_enabled、trace_file、structured_logging
  - `VisionLLMSettings`：enabled、provider、model、max_image_size
  - `IngestionSettings`：chunk_size、chunk_overlap、splitter、batch_size、chunk_refiner、metadata_enricher
- **环境变量覆盖**：支持 `RAGENT_LLM_*`、`RAGENT_EMBEDDING_*`、`RAGENT_VISION_LLM_*` 等环境变量动态覆盖 YAML 配置，便于容器化部署。
- **Fail-Fast 校验**：`validate_settings()` 在启动早期检查关键 provider 必填项，缺失时立即抛出 `SettingsError`。

---

### 4.3 类型契约层（`src/core/types.py`）

全链路共享的数据结构，所有跨模块交互均通过以下类型：

| 类型 | 职责 | 关键字段 |
|------|------|---------|
| `Document` | 加载阶段输出 | `id`, `text`, `metadata`（必须含 `source_path`） |
| `Chunk` | 切分阶段输出 | `id`, `text`, `metadata`, `start_offset`, `end_offset`, `source_ref` |
| `ChunkRecord` | 编码后入库记录 | 在 Chunk 基础上增加 `dense_vector`, `sparse_vector` |
| `ProcessedQuery` | 查询预处理结果 | `original_query`, `keywords`, `filters`, `expanded_terms` |
| `RetrievalResult` | 统一检索结果 | `chunk_id`, `score`, `text`, `metadata` |

所有类型均提供 `to_dict()` / `from_dict()` / `from_chunk()` 方法，保证序列化一致性。

---

### 4.4 摄取流水线（`src/ingestion/pipeline.py`）

**`IngestionPipeline`** 是文档摄入的**主编排器**，执行严格的 6 阶段流程：

```
文件路径 → [Stage 1: 完整性检查] → [Stage 2: 文档加载] → [Stage 3: 文本切分]
    → [Stage 4: 变换流水线] → [Stage 5: 编码] → [Stage 6: 存储落盘]
```

#### Stage 1 — 文件完整性检查（`SQLiteIntegrityChecker`）
- 计算文件 SHA256 作为“版本指纹”。
- 查询 `data/db/ingestion_history.db`，若该文件已处理且未强制重跑（`force=False`），则直接跳过。
- 支持幂等性：同一文件多次摄入不会产生重复数据。

#### Stage 2 — 文档加载（`UniversalLoader`）
- 按扩展名分发到具体加载器：PDF → `PdfLoader`（markitdown + VLM OCR 回退）、DOCX → `DocxLoader`、TXT/MD → `TextLoader` 等。
- PDF 加载特殊逻辑：`markitdown` 优先提取文本；若文本过短（<100 字符）或为空，自动降级到 VLM OCR（`qwen3.5-omni-flash` 多模态模型）。
- 图片提取：PDF 中的图片被提取为独立文件，存储到 `data/images/{collection}/{doc_hash}/`，并在 `Document.metadata["images"]` 中登记路径、页码、ID。
- 输出：`Document` 对象（含 `extract_method`、`page_count`、`word_count` 等元数据）。

#### Stage 3 — 文本切分（`DocumentChunker`）
- 通过 `SplitterFactory` 创建底层切分器（当前仅实现 `RecursiveSplitter`，基于 LangChain `RecursiveCharacterTextSplitter`）。
- **Chunk ID 生成**：格式为 `{doc_id}_{index:04d}_{content_hash_8chars}`，确保确定性、唯一性、可调试性。
- **元数据继承**：每个 Chunk 继承 `Document.metadata`，并追加 `chunk_index`、`source_ref`、从 `[IMAGE: xxx]` 占位符解析出的 `image_refs`、对应的 `images` 列表、`page_num`。

#### Stage 4 — 变换流水线（Transform Pipeline）
三个变换器顺序执行，均支持 **LLM 增强 / 规则降级** 双模式：

1. **`ChunkRefiner`**（Chunk 精炼）
   - LLM 模式：调用配置的大模型对文本块进行清洗、段落合并、连贯性增强。
   - 规则降级：若 LLM 不可用或失败，执行基础规则（去除多余空白、合并短段落）。
   - 并发控制：通过 `ThreadPoolExecutor` 多 workers 并发处理 chunks，`max_workers` 来自配置。

2. **`MetadataEnricher`**（元数据增强）
   - LLM 模式：从 chunk 文本中提取 `title`、`tags`、`summary`。
   - 规则降级：基于正则/关键词提取基础标签。

3. **`ImageCaptioner`**（图片 Caption）
   - 扫描 chunk 中的 `[IMAGE: {id}]` 占位符。
   - 调用 Vision LLM（`OpenAIVisionLLM` 等）生成图片描述。
   - 将占位符替换为 `"[IMAGE: {id}] {caption}"`，增强检索时的语义丰富度。

#### Stage 5 — 编码（`BatchProcessor`）
- **Dense 编码**：通过 `EmbeddingFactory` 创建 Embedding Client，批量调用 API，生成稠密向量（dim=1536，默认）。
- **Sparse 编码**：通过 `SparseEncoder` 进行 jieba 分词 + TF（词频）统计，生成稀疏表示（用于 BM25）。
- **批处理**：`batch_size=100`（可配置），降低 API 调用次数。
- **数量校验**：若编码结果与 chunk 数量不匹配，立即抛错终止，防止下游 upsert 崩溃。

#### Stage 6 — 存储落盘
1. **向量入库**（`VectorUpserter`）：将 chunk + dense_vector 写入 ChromaDB，collection 名由 Pipeline 构造函数传入（默认 `"default"`，实际运行时通常为 `"conv_{conversation_id}"`）。
2. **BM25 索引构建**（`BM25Indexer`）：将 sparse_stats 写入 `data/db/bm25/{collection}/`，每个 collection 一个 JSON 文件，内部维护 `doc_id → token → tf` 映射。
3. **图片索引登记**（`ImageStorage`）：将提取的图片信息写入 SQLite `image_index.db`。
4. **完整性标记**：成功后调用 `integrity_checker.mark_success(file_hash, file_path, collection)`。

#### 失败回滚机制
Pipeline `run()` 方法的 `except` 块内调用 `_rollback_storage()`：
- 删除已写入 ChromaDB 的 vectors。
- 删除已构建的 BM25 索引条目。
- 删除已登记的图片索引。
- 调用 `integrity_checker.mark_failed()` 记录失败原因。

---

### 4.5 查询引擎（`src/core/query_engine/`）

查询引擎采用 **四级检索链路**，是系统的核心召回能力：

```
用户 Query → QueryProcessor（分词/过滤）→ DenseRetriever（语义相似度）
    → SparseRetriever（BM25 关键词）→ RRFFusion（融合排序）→ CoreReranker（精排）
```

#### QueryProcessor（`query_processor.py`）
- **分词**：`jieba.lcut()` 处理中文，`re.fullmatch(r'[\s\W]+')` 过滤纯标点 token。
- **停用词过滤**：内置中英文混合停用词表（`DEFAULT_STOPWORDS` = `CHINESE_STOPWORDS | ENGLISH_STOPWORDS`），涵盖疑问词、助词、介词、代词、常见动词等 100+ 词。
- **过滤器语法解析**：支持 `collection:api-docs`、`type:pdf`、`tag:important` 等查询内过滤语法，通过正则 `r'(\w+):([^\s]+)'` 提取并从 query 中剥离。
- **输出**：`ProcessedQuery`（original_query、keywords 列表、filters 字典）。

#### DenseRetriever（`dense_retriever.py`）
- 将 query 文本通过 Embedding Client 编码为稠密向量。
- 调用 ChromaDB `collection.query()` 进行相似度搜索，返回 `top_k` 个结果。
- 结果转换为 `RetrievalResult` 列表。

#### SparseRetriever（`sparse_retriever.py`）
- 接收 `QueryProcessor` 产出的 `keywords` 列表。
- 加载对应 collection 的 BM25 JSON 索引文件（每次查询前 `_ensure_index_loaded()` 重新加载，保证其他进程写入的数据可见）。
- 计算 query tokens 与文档的 BM25 分数，返回 `top_k` 个结果。
- **ID 对齐**：BM25 结果中的 `chunk_id` 与 ChromaDB vector IDs 对齐，保证跨检索链路可互通。

#### HybridSearch（`hybrid_search.py`）
- **并行检索**：当 dense 和 sparse 均可用时，通过 `ThreadPoolExecutor(max_workers=2)` 并行执行两路检索，超时 30 秒。
- **优雅降级**：任意一路失败时，自动降级为单路检索（只返回可用路的结果），并记录 `used_fallback=True`。
- **RRF 融合**：调用 `RRFFusion.fuse()`，使用公式 `score(d) = Σ 1 / (k + rank(d))`（默认 `k=60`）对多路结果重新排序。
- **后置元数据过滤**：融合后按 `collection`、`doc_type`、`tags`、`source_path` 等条件做应用层兜底过滤。
- **配置驱动**：`HybridSearchConfig` 控制 dense_top_k、sparse_top_k、fusion_top_k、enable_dense、enable_sparse、parallel_retrieval 等开关。

#### CoreReranker（`reranker.py`）
- **工厂注入**：通过 `RerankerFactory` 创建底层重排序器（LLM / CrossEncoder / DashScope / None）。
- **类型转换**：将 `RetrievalResult` 与 reranker 内部的 dict 格式双向转换。
- **降级策略**：reranker 失败或超时时，若 `fallback_on_error=True`（默认），返回原始顺序并标记 `used_fallback=True`。
- **Trace 集成**：记录 rerank 方法、输入/输出数量、各 chunk 的 score。

---

### 4.6 Agent 对话工作流（`src/ragent_backend/workflow.py`）

**`RAGWorkflow`** 基于 LangGraph `StateGraph` 构建，定义了完整的 RAG Agent 状态机：

```
START → session → intent → [条件分支]
                              │
                    need_clarify=True → archive → END
                    need_clarify=False → retrieve → generate → memory_manage → archive → END
```

#### 状态定义（`RAGState` TypedDict）
- `messages`: `Annotated[List[AnyMessage], add_messages]` —— LangGraph 自动管理的消息列表，支持 `RemoveMessage` 滑动窗口删除。
- `summary`: `str` —— 滚动摘要，存储被压缩的旧消息核心信息。
- `query` / `rewritten_query` / `sub_queries`: 原始查询、重写后查询、子查询列表。
- `retrieval_context` / `retrieval_contexts`: 检索到的上下文文本。
- `final_answer` / `used_model`: 生成的答案与使用的模型。
- `memories`: `List[str]` —— 从 LTM 召回的长期记忆事实。
- `trace_events`: 节点级执行事件列表。
- `current_turn_id`: 每轮生成的 UUID，用于三层时间裁剪回滚。
- `_to_archive`: 内部标记，本轮需要归档到 PostgreSQL 的消息（不存入 checkpoint）。

#### 节点详解

1. **`_session_node`（会话初始化）**
   - 确保 `conversation_id`、`task_id`、`current_turn_id` 存在。
   - 为所有消息补全 ID（`ensure_message_ids`，`RemoveMessage` 依赖 `m.id`）。
   - **LTM 召回**：若配置 `user_id`，调用 `LTMStore.retrieve_facts()` 召回 top-3 相关记忆，注入 `memories`。

2. **`_intent_node`（意图识别）**
   - 调用 `analyze_query()`：单次结构化 LLM 调用（`with_structured_output(QueryAnalysisResult, method="json_mode")`），同时完成：
     - **指代消解**：消除"它"、"这个"、"that" 等代词，替换为历史对话中的具体实体。
     - **子查询拆分**：将并列主题（如"北京上海杭州天气"）拆分为可独立执行的子查询列表。
   - 调用 `detect_intent()`：基于规则做二次校验，若重写后查询仍含模糊代词或长度过短，标记 `need_clarify=True`，直接短路到 archive 节点。

3. **`_retrieve_node`（检索）**
   - 构建 collection 名称：`f"conv_{conversation_id}"`。
   - 调用 `QueryKnowledgeHubTool.execute()` 执行 HybridSearch + Rerank。
   - 检索失败时返回友好提示（不中断工作流）。

4. **`_generate_node`（生成）**
   - 构建 Prompt：整合 `memories`（长期记忆）、`summary`（历史摘要）、最近 3 轮对话（6 条消息）、`retrieval_context`（检索上下文）、当前 `query`。
   - 调用 `llm.astream()` 进行**真流式生成**，token 通过 `asyncio.Queue` 实时透传给 `run_stream()` 调用方。
   - 生成结果追加为 `AIMessage`。

5. **`_memory_manage_node`（记忆管理）**
   - 检查消息数量是否超出 `max_messages`（默认 20）。
   - 若超出，保留最近的 `keep_recent` 条（默认 4 条），其余消息通过 `RemoveMessage(id=m.id)` 从 `messages` 中删除（LangGraph 自动处理）。
   - 被删除的消息调用 `RollingMemoryManager.compact()` 合并到 `summary` 中：LLM 重写摘要，保留专有名词、具体结论、用户偏好；LLM 不可用时降级为简单拼接。
   - 被删除的消息标记到 `_to_archive`，供 archive 节点异步写入 PostgreSQL。

6. **`_archive_node`（归档）**
   - 将被压缩的消息 + 本轮新生成的 user/assistant 消息批量写入 PostgreSQL `conversation_archive` 表（`append_to_history()`）。
   - **LTM 提取**：异步调用 `LTMStore.extract_facts()`，从本轮 Q&A 中提炼 0~3 条长期记忆事实，写入 `long_term_memories` 表。
   - 全部通过 `asyncio.create_task()` 异步执行，不阻塞响应返回。

---

### 4.7 后端 API（`src/ragent_backend/app.py`）

FastAPI 应用通过 `create_app()` 工厂函数创建，所有端点均为异步（`async def`）。

#### 核心端点列表

| 方法 | 路径 | 职责 |
|------|------|------|
| GET | `/health` | 健康检查，返回版本与特性列表 |
| POST | `/api/v1/conversations` | 创建新对话 |
| GET | `/api/v1/conversations` | 对话列表（分页、按 updated_at 倒序） |
| GET | `/api/v1/conversations/{id}` | 对话详情 |
| PATCH | `/api/v1/conversations/{id}` | 更新对话（标题、状态） |
| DELETE | `/api/v1/conversations/{id}` | 删除对话（软删除 + 级联删除文件） |
| POST | `/api/v1/conversations/{id}/files` | 文件上传（后台异步 ingest） |
| GET | `/api/v1/conversations/{id}/files` | 文件列表 |
| DELETE | `/api/v1/conversations/{id}/files/{file_id}` | 删除文件 |
| POST | `/api/v1/chat` | 非流式对话 |
| POST | `/api/v1/chat/stream` | **真流式对话**（SSE，token-by-token） |
| WS | `/ws/trace/{conversation_id}` | WebSocket 实时 Trace 推送 |
| POST | `/api/v1/conversations/{id}/rollback` | **三层时间裁剪回滚** |
| GET | `/api/v1/history/{conversation_id}` | 加载完整历史（从 PostgreSQL） |
| GET | `/api/v1/memory/{conversation_id}/stats` | 记忆统计（checkpoint 状态调试） |

#### 关键设计细节

- **CORS**：`allow_origins=["*"]`，允许所有来源（开发环境配置）。
- **PostgreSQL Checkpointer**：`create_checkpointer()` 使用 `AsyncPostgresSaver`，Windows 下通过 `SelectorEventLoop` 兼容 `psycopg` async。
- **后台 Ingest 并发控制**：全局 `asyncio.Semaphore(2)`，限制同时执行的 ingest 任务数，防止 LLM API 配额和内存被打爆。
- **流式中断与 Checkpoint 回滚**：
  - 流式开始前记录 `clean_checkpoint_id`。
  - 客户端断开（`req.is_disconnected()`）或 `asyncio.CancelledError` 时，标记 `interrupted=True`。
  - `finally` 块中调用 `_trim_checkpoints()`，物理删除该 thread 下除 `clean_checkpoint_id` 外的所有 checkpoint、blob、writes 记录。
- **WebSocket Trace**：`broadcast_trace()` 向该对话的所有 WebSocket 客户端广播节点级 trace 事件。

#### 三层时间裁剪回滚（`/rollback`）

回滚端点是系统的**高级时间旅行特性**，同时操作三层存储：

1. **Checkpoint 层（LangGraph 状态）**：
   - 通过 `checkpointer.alist()` 遍历该 thread 的所有 checkpoint。
   - 从 `channel_values.current_turn_id` 匹配目标 turn 的前一个 turn。
   - 保留前一个 turn 最新的 checkpoint，删除之后的所有 checkpoint、blob、writes。

2. **消息归档层（PostgreSQL `conversation_archive`）**：
   - 找到目标 `turn_id` 的最早 `created_at`。
   - 删除该时间点及之后的所有消息记录。

3. **长期记忆层（PostgreSQL `long_term_memories`）**：
   - 删除该 `conversation_id` + `turn_id` 产生的所有 LTM 事实。

---

### 4.8 存储层详解

系统使用 **PostgreSQL 为主存储 + ChromaDB/SQLite 为辅助存储** 的混合架构。

#### PostgreSQL 数据库 Schema

**`conversations` 表**（对话元数据）
```sql
conversation_id VARCHAR(128) PRIMARY KEY,
title VARCHAR(512) NOT NULL,
created_at TIMESTAMP NOT NULL,
updated_at TIMESTAMP NOT NULL,
message_count INTEGER DEFAULT 0,
file_count INTEGER DEFAULT 0,
status VARCHAR(32) DEFAULT 'active',
metadata JSONB
```
索引：`idx_conv_updated(updated_at DESC)`、`idx_conv_status(status)`

**`conversation_archive` 表**（完整消息归档，用户可见历史）
```sql
id SERIAL PRIMARY KEY,
conversation_id VARCHAR(128) NOT NULL,
role VARCHAR(32) NOT NULL,
content TEXT NOT NULL,
message_id VARCHAR(64),
created_at DOUBLE PRECISION NOT NULL,
turn_id VARCHAR(64)
```
索引：`idx_archive_conversation_time(conversation_id, created_at)`、`idx_archive_turn(conversation_id, turn_id)`

**`conversation_files` 表**（文件元数据）
```sql
id SERIAL PRIMARY KEY,
file_id VARCHAR(64) NOT NULL,
conversation_id VARCHAR(128) NOT NULL,
filename VARCHAR(512) NOT NULL,
original_name VARCHAR(512) NOT NULL,
file_path VARCHAR(1024) NOT NULL,
file_size BIGINT NOT NULL DEFAULT 0,
mime_type VARCHAR(128) DEFAULT 'application/octet-stream',
doc_id VARCHAR(128),
status VARCHAR(32) NOT NULL DEFAULT 'pending',
created_at TIMESTAMP NOT NULL,
error_message TEXT,
file_type VARCHAR(32),
page_count INTEGER,
extract_method VARCHAR(32),
word_count INTEGER,
UNIQUE (conversation_id, file_id)
```
索引：`idx_conv_files(conversation_id, created_at)`、`idx_file_id(file_id)`

**`long_term_memories` 表**（长期记忆事实）
```sql
id TEXT PRIMARY KEY,
user_id VARCHAR(64) NOT NULL,
conversation_id VARCHAR(64),
turn_id VARCHAR(64),
fact TEXT NOT NULL,
created_at DOUBLE PRECISION NOT NULL,
access_count INTEGER DEFAULT 0
```
索引：`idx_ltm_user(user_id)`、`idx_ltm_conv_turn(conversation_id, turn_id)`

#### ChromaDB（向量存储）
- 使用 `PersistentClient`，底层为 SQLite，路径 `data/db/chroma/`。
- 每个对话一个 collection：`conv_{conversation_id}`。
- 默认 collection：`default`（用于全局知识或未关联对话的文档）。
- 存储字段：`id`（chunk_id）、`embedding`（dense vector）、`document`（text）、`metadata`（source_path、doc_hash、chunk_index 等）。

#### BM25 索引（文件系统）
- 每 collection 一个 JSON 文件：`data/db/bm25/{collection}/{collection}_bm25.json`。
- 结构：`{"doc_id": {"token": tf, ...}, ...}`。
- `SparseRetriever` 每次查询前重新加载该 JSON，保证跨进程可见性。

#### SQLite 辅助数据库
- `file_store.db`：文件完整性历史（`processed_files` 表）。
- `image_index.db`：图片索引（`image_registry` 表）。
- `ingestion_history.db`：摄取历史（`ingestion_records` 表）。
- `ltm.db`：长期记忆 SQLite 副本（当前实际使用 PostgreSQL，`ltm.db` 为历史残留或降级备份）。

---

### 4.9 前端架构（`frontend/src/`）

前端是一个**完整的 Vue 3 单页应用**，使用 Composition API + `<script setup>` 语法。

#### 技术细节
- **构建工具**：Vite（开发服务器 + Rollup 打包）。
- **UI 框架**：Element Plus（全部组件 + 图标库 `@element-plus/icons-vue`）。
- **HTTP 客户端**：Axios。
- **状态管理**：无 Vuex/Pinia，全部使用 `ref` / `reactive` 本地状态 + `localStorage` 持久化。

#### 界面布局（`App.vue`）
```
┌─────────────────────────────────────────────────────────────┐
│  Sidebar (320px)          │  Main Chat Area      │ Trace Panel (360px) │
│  ───────────────────────  │  ──────────────────  │ ─────────────────── │
│  Logo + New Chat Button   │  Chat Header         │ TracePanel.vue      │
│  ───────────────────────  │  ──────────────────  │                     │
│  历史对话列表（可折叠）    │  Messages Container  │                     │
│  ───────────────────────  │  - Welcome Message   │                     │
│  知识库文件管理           │  - Message Turns     │                     │
│  - Upload Area            │    (turn divider     │                     │
│  - File List (status)     │     with rollback)   │                     │
│  ───────────────────────  │  - Typing Indicator  │                     │
│  System Info              │  ──────────────────  │                     │
│                           │  Input Area          │                     │
│                           │  - Textarea + Send   │                     │
└─────────────────────────────────────────────────────────────┘
```

#### 核心功能实现
- **消息按 Turn 分组**：`messageTurns` computed 属性将扁平的 `messages` 数组按 user → assistant 分组，每组之间显示可点击的 checkpoint 分隔线（用于回溯）。
- **流式输出**：使用原生 `fetch()` + `ReadableStream.getReader()` 读取 SSE，逐行解析 `data: {...}`，实时追加到 `assistantMessage.content`。
- **WebSocket Trace**：`connectTraceWs()` 建立到 `/ws/trace/{conversation_id}` 的 WebSocket 连接，接收并显示 LangGraph 节点级执行 trace。
- **文件上传**：通过隐藏 `<input type="file">` 触发选择，FormData POST 到后端，成功后轮询文件状态（`pollFileStatus`，最多 20 次，每 3 秒）。
- **回溯交互**：点击 turn 分隔线上的圆点，弹出确认框，POST 到 `/rollback`，成功后重新加载历史。

---

### 4.10 MCP Server 架构（`src/mcp_server/`）

MCP（Model Context Protocol）服务器使 RAGent 可以被任何 MCP Client 调用。

#### 启动入口（`server.py`）
- **stdio 传输**：stdout 仅用于 JSON-RPC 协议消息，所有日志重定向到 stderr（`_redirect_all_loggers_to_stderr`）。
- **预加载重量级依赖**：主线程中预先 import `chromadb`、`langgraph` 等重型库，避免后续 `asyncio.to_thread()` 时的 import 锁竞争死锁。

#### ProtocolHandler（`protocol_handler.py`）
- 实现 JSON-RPC 2.0 工具注册与执行调度。
- 支持 `tools/list`（返回工具 schema）和 `tools/call`（执行工具 handler）。
- 统一错误封装：参数错误返回 `INVALID_PARAMS`、内部错误返回通用信息避免泄漏细节。

#### 工具列表

| 工具名 | 功能 | 关键参数 |
|--------|------|---------|
| `query_knowledge_hub` | 知识库混合检索 | `query`, `top_k`, `collection` |
| `list_collections` | 列出所有 collection | `include_stats` |
| `get_document_summary` | 获取文档摘要 | `doc_id`, `collection` |

#### `query_knowledge_hub` 工具内部流程
1. 懒加载初始化：首次调用时构建 `HybridSearch`、`CoreReranker`、`VectorStore` 组件（通过 `asyncio.to_thread` 避免阻塞 stdio 事件循环）。
2. 执行 HybridSearch（`top_k * 2`，预留重排序空间）。
3. 执行 CoreReranker（若启用）。
4. 通过 `ResponseBuilder` 构建 `MCPToolResponse`（Markdown + 引用 + 可选图片）。
5. Trace 记录全程，最终通过 `TraceCollector` 持久化到 `logs/traces.jsonl`。
6. 返回 `types.CallToolResult`（含 `TextContent` 和可选 `ImageContent`）。

---

### 4.11 长期记忆系统（`src/ragent_backend/ltm_store.py`）

LTM（Long-Term Memory）实现**跨会话认知连续**。

#### 记忆提取（`extract_facts`）
- 在 `archive` 节点中，从每轮 Q&A 中异步提取。
- Prompt 要求 LLM 输出 JSON 列表，提取关于用户身份、偏好、禁忌、工作背景的客观事实（0~3 条）。
- 事实必须简洁，一句话说完。

#### 记忆存储（`save_facts`）
- 写入 PostgreSQL `long_term_memories` 表。
- **自动去重**：查询该用户已有事实，仅插入不重复的新事实（大小写不敏感比对）。

#### 记忆召回（`retrieve_facts`）
- 在 `session` 节点中，根据当前 query 召回 top-k（默认 3）条相关记忆。
- **评分机制**：
  - 关键词匹配：query tokens 在 fact 中的命中数 × 10。
  - 时间衰减：`max(0, 24 - age_hours)`，24 小时内满分。
  - 访问频率：`access_count × 2`。
- 召回后更新 `access_count`（访问计数器）。

---

### 4.12 可观测性与评估（`src/observability/`）

#### Trace 系统
- **TraceContext**：每个请求（query/ingestion）创建一个 trace，含唯一 `trace_id`、开始时间、阶段列表、元数据。
- **阶段记录**：`record_stage(stage_name, data, elapsed_ms)`，在 HybridSearch、IngestionPipeline 等关键路径中埋点。
- **TraceCollector**：将完成的 trace 追加写入 `logs/traces.jsonl`（JSON Lines 格式）。
- **WebSocket 实时推送**：`workflow.run_stream()` 中通过 `trace_queue` 将节点级事件实时推送到前端。

#### Streamlit Dashboard
- **多页导航**：Overview、Data Browser、Ingestion Manager、Ingestion Traces、Query Traces、Evaluation Panel。
- **数据服务层**：`data_service.py`、`trace_service.py`、`config_service.py` 为各页面提供数据抽象。

#### 评估框架
- **RagasEvaluator**：封装 Ragas 库，评估 `faithfulness`、`answer_relevancy`、`context_precision` 等指标。
- **RAGEvalRunner**：端到端评估编排器，读取 `tests/fixtures/golden_test_set_v2.json`，对每个问题执行完整 RAGWorkflow，再用 Ragas 评分。
- **benchmark_rag.py**：全链路基准测试脚本，覆盖：
  1. 查询分析（指代消解 + 子查询拆分）
  2. Ingestion Pipeline 完整性与 Trace
  3. RAG 检索质量
  4. 端到端 Ragas 评估

---

## 五、关键数据流

### 5.1 文档摄取数据流

```
用户上传文件
    ↓
FastAPI /api/v1/conversations/{id}/files
    ↓
ConversationFileStore.save_file() → 磁盘保存 + PostgreSQL 元数据记录
    ↓
ingest_file_task()（后台 asyncio.create_task，受 Semaphore(2) 限制）
    ↓
IngestionPipeline.run()
    ├── Stage 1: SQLiteIntegrityChecker.compute_sha256() → should_skip()? → 幂等判断
    ├── Stage 2: UniversalLoader.load() → Document（含图片提取）
    ├── Stage 3: DocumentChunker.split_document() → List[Chunk]
    ├── Stage 4: Transform Pipeline
    │   ├── ChunkRefiner.transform() → LLM/规则精炼
    │   ├── MetadataEnricher.transform() → LLM/规则增强元数据
    │   └── ImageCaptioner.transform() → VLM 生成图片 caption
    ├── Stage 5: BatchProcessor.process() → dense_vectors + sparse_stats
    └── Stage 6: Storage
        ├── VectorUpserter.upsert() → ChromaDB (collection = conv_{id})
        ├── BM25Indexer.add_documents() → data/db/bm25/conv_{id}/
        └── ImageStorage.register_image() → image_index.db
    ↓
ConversationFileStore.update_file_status() → status = "ready"
```

### 5.2 对话查询数据流

```
用户发送消息（流式）
    ↓
POST /api/v1/chat/stream
    ↓
chat_stream() event_stream()
    ├── 确定 thread_id = conversation_id
    ├── 获取 clean_checkpoint_id（用于中断回滚）
    └── workflow.run_stream(initial_state, thread_id)
        ↓
LangGraph 状态机执行：
    session → intent → retrieve → generate → memory_manage → archive
        ↓
session: 确保 IDs，召回 LTM memories
intent: analyze_query() → rewritten_query + sub_queries；detect_intent() → need_clarify?
retrieve: QueryKnowledgeHubTool.execute(collection=f"conv_{thread_id}")
    └── HybridSearch.search()
        ├── QueryProcessor.process() → keywords + filters
        ├── DenseRetriever.retrieve() → ChromaDB 语义检索
        ├── SparseRetriever.retrieve() → BM25 关键词检索
        ├── RRFFusion.fuse() → 融合排序
        └── CoreReranker.rerank() → 精排
generate: llm.astream() → token 实时推送到 token_queue
memory_manage: should_compact()? → RemoveMessage + summary 重写
archive: 异步写入 PostgreSQL conversation_archive + LTM 提取
        ↓
前端通过 SSE 接收 token → 实时渲染
通过 WebSocket 接收 trace → TracePanel 实时展示
```

---

## 六、工厂模式与可插拔设计

系统大量使用**工厂模式**实现 Provider 的可插拔替换：

| 工厂 | 注册表 | 已注册实现 |
|------|--------|-----------|
| `LLMFactory` | `_PROVIDERS` / `_VISION_PROVIDERS` | openai, azure, deepseek, ollama |
| `EmbeddingFactory` | `_PROVIDERS` | openai, azure, ollama |
| `VectorStoreFactory` | `_PROVIDERS` | chroma |
| `RerankerFactory` | `_PROVIDERS` | llm, cross_encoder, dashscope, none |
| `SplitterFactory` | `_PROVIDERS` | recursive |
| `EvaluatorFactory` | `_PROVIDERS` | custom, ragas, source |

所有工厂均支持运行时 `register_provider()` 扩展，无需修改工厂源码即可添加新 Provider。

---

## 七、环境变量清单

| 变量名 | 用途 | 示例值 |
|--------|------|--------|
| `RAGENT_POSTGRES_URL` | PostgreSQL 连接串 | `postgresql://user:pass@localhost:5432/ragent` |
| `RAGENT_PORT` | 后端服务端口 | `8000` |
| `RAGENT_MAX_MESSAGES` | 记忆窗口上限 | `20` |
| `RAGENT_KEEP_RECENT` | 压缩后保留消息数 | `4` |
| `OPENAI_API_KEY` | OpenAI 兼容 API 密钥 | `sk-...` |
| `RAGENT_LLM_MODEL` | 覆盖 LLM 模型 | `gpt-4o` |
| `RAGENT_LLM_BASE_URL` | 覆盖 LLM Base URL | `https://api.openai.com/v1` |
| `RAGENT_EMBEDDING_MODEL` | 覆盖 Embedding 模型 | `text-embedding-3-small` |
| `RAGENT_VISION_LLM_ENABLED` | 是否启用 Vision LLM | `true` |

---

## 八、项目构建与入口

### 8.1 Python 包定义（`pyproject.toml`）
- **构建后端**：`hatchling`。
- **CLI 脚本**：
  - `mcp-server = "main:main"` —— MCP stdio 服务入口。
  - `ragent-backend = "src.ragent_backend.app:run"` —— FastAPI 服务入口。
- **开发依赖**：pytest、pytest-asyncio、pytest-mock、ruff、mypy、openai。
- **代码质量**：Ruff（line-length=100）、MyPy（python_version=3.10）。

### 8.2 启动方式

```bash
# 1. FastAPI 后端（含前端静态文件服务）
python -m src.ragent_backend.app
# 或
ragent-backend

# 2. MCP Server（stdio 模式）
python -m src.mcp_server.server
# 或
mcp-server

# 3. Streamlit Dashboard
streamlit run src/observability/dashboard/app.py

# 4. 前端开发服务器
cd frontend && npm run dev

# 5. 基准测试
python benchmark_rag.py
```

---

## 九、关键设计模式总结

| 模式 | 应用位置 | 目的 |
|------|---------|------|
| **工厂模式** | `libs/*/ *_factory.py` | Provider 可插拔替换 |
| **适配器模式** | `DocumentChunker`、`CoreReranker` | 桥接基础设施与业务对象 |
| **策略模式** | `SplitterFactory`、`RerankerFactory` | 运行时切换算法策略 |
| **状态机模式** | `RAGWorkflow`（LangGraph） | Agent 行为结构化编排 |
| **观察者模式** | `TraceContext` + WebSocket | 实时事件推送 |
| **命令模式** | `ProtocolHandler.execute_tool()` | MCP 工具统一调度 |
| **降级模式** | 全链路各处（HybridSearch、CoreReranker、ChunkRefiner 等） | 优雅处理 LLM/API 故障 |
| **幂等模式** | `SQLiteIntegrityChecker` | 防止重复摄取 |
| **滑动窗口** | `RollingMemoryManager` | 控制上下文长度 |
| **三层回滚** | `_trim_checkpoints` + `delete_messages_from_turn` + `delete_facts_from_turn` | 时间旅行与数据一致性 |

---

## 十、架构演进痕迹

从代码中可以观察到项目的演进轨迹：

1. **从 SQLite 到 PostgreSQL**：早期版本使用 SQLite/MySQL（`init_mysql.sql` 残留），后全面迁移到 PostgreSQL（`asyncpg`），但 SQLite 文件（`ltm.db`、`file_store.db`、`image_index.db`、`ingestion_history.db`）仍保留用于辅助场景。
2. **从全局知识库到会话级隔离**：`collection` 参数从早期的 `"default"` 演变为 `"conv_{conversation_id}"`，实现对话级数据隔离。
3. **从非流式到真流式**：`chat_stream` 端点经历了从模拟流式（`_chunk_text`）到真流式（`llm.astream()` + SSE）的升级，并配套了 checkpoint 回滚机制。
4. **从简单 RAG 到 Agentic RAG**：引入 LangGraph 状态机、意图识别、子查询拆分、记忆压缩、LTM 等 Agent 能力。
5. **MCP 协议接入**：后期增加 `src/mcp_server/` 目录，使系统从独立服务升级为可嵌入任意 MCP Client 的工具。

---

> **文档生成时间**：2026-04-19  
> **基于代码版本**：`rag-pro` 最新源码完整推导  
> **阅读范围**：全部 Python 源码（`src/`、`scripts/`、`benchmark_rag.py`、`main.py`）、前端源码（`frontend/src/`）、配置文件（`config/settings.yaml`、`pyproject.toml`）、Prompt 模板
