<div align="center">

<img src="ragent.png" width="280" alt="ragent logo">

###  **当 RAG 只会机械匹配，你需要一个懂上下文的 Agent**

[![ragent](https://img.shields.io/badge/ragent-0.4.0-purple.svg)](https://github.com/yourname/ragent)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2.x-blue.svg)](https://langchain-ai.github.io/langgraph/)
[![LangChain](https://img.shields.io/badge/LangChain-0.3.x-blue.svg)](https://python.langchain.com/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-Passing-brightgreen.svg)](tests/)

**下一代认知检索架构** · Next-Gen Cognitive Retrieval Architecture

[🚀 快速开始](#快速开始) · [🏗️ 核心架构](#系统架构) · [⚡ 功能特性](#功能特性详解) · [📡 API 文档](#api-文档)

</div>

---

> 🤖 **你的知识库只会关键词搬运？ragent 让企业知识真正被理解**
>
> 💡 **核心差异**：传统 RAG 匹配即结束，ragent 从意图理解到混合检索、从记忆延续到工具增强，完成完整认知闭环。

---

## 目录

- [系统架构](#系统架构)
- [功能特性详解](#功能特性详解)
- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [快速开始](#快速开始)
- [配置详解](#配置详解)
- [API 文档](#api-文档)
- [前端界面](#前端界面)
- [可观测性面板](#可观测性面板)
- [测试体系](#测试体系)
- [开发指南](#开发指南)
- [错误处理与降级策略](#错误处理与降级策略)
- [部署说明](#部署说明)

---
## 前端页面
![alt text](image-1.png)

## 系统架构

系统采用分层架构，核心由三大主线组成：**文档摄取（Ingestion）**、**查询检索（Retrieval）**、**对话工作流（Conversation）**。

### 核心数据流

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          对话层 (FastAPI + LangGraph)                      │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────┐  │
│  │   session    │──▶│   intent     │──▶│  retrieve    │──▶│ generate │  │
│  │   (初始化)    │   │  (意图路由)   │   │  (混合检索)   │   │(LLM生成) │  │
│  └──────────────┘   └──────┬───────┘   └──────────────┘   └────┬─────┘  │
│                            │                                   │        │
│                       ┌────┴────┐                       ┌────┴────┐   │
│                       ▼         ▼                       ▼         │   │
│                  ┌────────┐ ┌────────┐          ┌──────────────┐  │   │
│                  │clarify │ │Tool_sub│          │memory_manage │  │   │
│                  │(澄清)  │ │(工具子图)│          │(滑动窗口压缩) │  │   │
│                  └────────┘ └────┬───┘          └──────┬───────┘  │   │
│                                  │                     │          │   │
│                                  ▼                     ▼          │   │
│                           ┌────────────┐         ┌────────────┐  │   │
│                           │ summarize  │         │  archive   │  │   │
│                           │ (结果汇总)  │         │(MySQL归档)  │  │   │
│                           └────────────┘         └────────────┘  │   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          检索层 (Hybrid Search)                          │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐     │
│  │  QueryProcessor │───▶│ DenseRetriever  │───▶│   RRFFusion     │     │
│  │   (查询预处理)   │    │  (向量语义检索)  │    │  (融合重排序)    │     │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘     │
│                         ┌─────────────────┐            │               │
│                         │ SparseRetriever │────────────┘               │
│                         │  (BM25关键词)   │                            │
│                         └─────────────────┘                            │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          摄取层 (Ingestion Pipeline)                       │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌────────┐ │
│  │ Integrity│──▶│  Loader  │──▶│ Chunker  │──▶│ Transform│──▶│ Encode │ │
│  │  (去重)   │   │(文档加载) │   │(分块拆分) │   │(元数据增强)│   │(向量化) │ │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘   └────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

### 工作流状态图

```mermaid
graph TD
    A[START] --> B[session_node]
    B --> C[intent_node]
    C -->|clarify| D[clarify_node]
    C -->|rag| E[retrieve_node]
    C -->|tool| F[tool_subgraph]
    D --> C
    E --> G[generate_node]
    F --> G
    G --> H[memory_manage_node]
    H --> I[archive_node]
    I --> J[END]
```

---

## 功能特性详解

### 1. 认知意图路由（Intent Router）

传统 RAG 一上来就检索，ragent 先理解用户到底要什么：

- **指代消解**："那篇文章的价格是多少" → 自动关联上文提到的商品
- **子查询拆分**："对比 A 和 B 的优缺点" → 拆分为独立子查询并行检索
- **三分支路由**：`clarify`（信息不足，反问澄清）/ `rag`（走知识库检索）/ `tool`（调用外部工具）

### 2. 混合检索引擎（Hybrid Search）

Dense + Sparse 双路并行，RRF 融合：

| 维度 | Dense（向量语义） | Sparse（BM25 关键词） |
|------|-------------------|----------------------|
| 优势 | 理解同义词、语义相关性 | 精确匹配专有名词、ID、数字 |
| 召回 | 语义相近但字面不同的内容 | 包含精确术语的文档 |
| 融合 | RRF（Reciprocal Rank Fusion）自动加权 | 双路失败时优雅降级为单路 |

- **并行检索**：`ThreadPoolExecutor(max_workers=2)` 同时发起向量检索和关键词检索
- **优雅降级**：任一路失败时自动退化为另一路，双路失败时抛明确异常
- **Metadata 过滤**：支持按文件类型、时间、标签等维度预过滤

### 3. 滚动记忆管理（Rolling Memory）

解决长对话上下文爆炸问题：

- **窗口策略**：保留最近 `keep_recent=4` 条原始消息，更早消息压缩为 summary
- **压缩质量**：LLM 重写 summary，保留专有名词、数字、用户偏好等关键信息
- **降级保护**：LLM 不可用时自动 fallback 为简单拼接，绝不阻塞对话

### 4. 工具增强子图（Tool Subgraph）

ReAct 循环子图，让 Agent 能调用外部能力：

```
START → think_node → router → [tool_node | summarize_node | END(max_iter)]
```

- **MCP 集成**：自动发现 MCP Server 工具，命名空间隔离 `{server_name}.{tool_name}`
- **结构化决策**：LLM 输出 `ToolDecision`（call_tool / finish），避免无限循环
- **结果汇总**：多轮工具调用结果自动整理为结构化摘要，注入主图 `tool_summary`

### 5. 会话级知识库隔离

每个对话独立 collection：`conv_{conversation_id}`：

- 文件上传即创建专属知识库
- 多用户/多会话数据物理隔离
- 删除会话时自动清理向量数据

### 6. 三层回滚机制

对话状态可回溯到任意历史节点：

| 层级 | 机制 | 延迟 | 用途 |
|------|------|------|------|
| L1 | LangGraph Checkpoint | < 100ms | 快速撤销/重做 |
| L2 | Archive 快照 | ~500ms | 恢复到历史会话状态 |
| L3 | LTM 长期记忆 | 异步 | 跨会话知识沉淀 |

### 7. 流式响应与实时追踪

- **真流式 SSE**：`llm.astream()` 透传 token，非假流式
- **心跳保活**：每 15 秒发送 `:heartbeat` 注释，防止 Nginx/CDN 切断
- **WebSocket Trace**：`/ws/trace/{conversation_id}` 实时推送 LangGraph 节点级执行轨迹

---

## 技术栈

| 层级 | 技术选型 | 版本 |
|------|---------|------|
| 工作流编排 | LangGraph | 0.2.x |
| LLM 框架 | LangChain | 0.3.x |
| Web 框架 | FastAPI | 0.115+ |
| 前端框架 | Vue 3 + Element Plus | - |
| 向量数据库 | ChromaDB | - |
| 关系数据库 | PostgreSQL / MySQL | - |
| 文档加载 | unstructured / python-docx / PyPDF2 | - |
| 编码 | sentence-transformers + BM25 | - |

---

## 项目结构

```
rag-pro/
├── config/
│   └── settings.yaml              # 主配置文件
├── src/
│   ├── core/
│   │   ├── query_engine/
│   │   │   ├── hybrid_search.py   # 混合检索引擎
│   │   │   ├── dense_retriever.py # 向量检索
│   │   │   └── sparse_retriever.py# BM25 检索
│   │   ├── models/
│   │   │   └── schema.py          # Pydantic 数据模型
│   │   └── config.py              # 配置加载器
│   ├── ingestion/
│   │   └── pipeline.py            # 六阶段摄取主编排器
│   ├── ragent_backend/
│   │   ├── app.py                 # FastAPI 主应用
│   │   ├── workflow.py            # LangGraph RAG 工作流
│   │   ├── intent.py              # 意图识别模块
│   │   ├── memory_manager.py      # 滚动记忆管理
│   │   └── archive.py             # 归档与 LTM
│   ├── tool_agent/
│   │   ├── tool_registry.py       # 工具注册表（含 MCP）
│   │   ├── subgraph.py            # 工具子图（ReAct）
│   │   └── mcp_client.py          # MCP 客户端
│   └── frontend/                  # Vue 3 前端
│       ├── src/
│       │   ├── App.vue            # 主聊天界面
│       │   └── components/        # 可复用组件
│       └── package.json
├── tests/                         # 测试套件
├── start.bat                      # Windows 一键启动
└── readme.md                      # 本文档
```

---

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+（前端）
- PostgreSQL 14+（对话归档 + LangGraph checkpoint）
- （可选）ChromaDB 本地运行或远程服务

### 1. 安装依赖

```bash
# 后端
pip install -e .

# 前端
cd frontend
npm install
```

### 2. 配置环境变量

复制 `config/settings.yaml` 并按需修改，或通过环境变量覆盖：

```bash
# LLM 配置
set RAGENT_LLM_MODEL=gpt-4o
set RAGENT_LLM_API_KEY=sk-xxx

# 向量数据库
set RAGENT_VECTOR_STORE_PATH=./chroma_db

# PostgreSQL（对话归档）
set RAGENT_POSTGRES_URL=postgresql://user:pass@localhost:5432/ragent
```

### 3. 启动服务

**Windows 一键启动（推荐）**：

```bash
start.bat
```

**手动启动**：

```bash
# 终端 1：后端（端口 8000）
python -m src.ragent_backend.app

# 终端 2：前端（端口 5173）
cd frontend
npm run dev
```

打开浏览器访问 `http://localhost:5173`。

---

## 配置详解

配置文件路径：`config/settings.yaml`

### LLM 配置

```yaml
llm:
  model: "gpt-4o"           # 主模型
  api_key: ""               # 留空则从环境变量 RAGENT_LLM_API_KEY 读取
  base_url: ""              # 自定义 API 基地址
  temperature: 0.7
  max_tokens: 4096
```

### Embedding 配置

```yaml
embedding:
  model: "BAAI/bge-large-zh-v1.5"
  device: "auto"            # auto / cpu / cuda
  normalize_embeddings: true
```

### 检索配置

```yaml
retrieval:
  dense_top_k: 10           # 向量检索 Top K
  sparse_top_k: 10          # BM25 检索 Top K
  fusion_top_k: 5           # 融合后最终 Top K
  parallel_retrieval: true  # 是否并行检索
  rerank_enabled: false     # 是否启用重排序
```

### 记忆配置

```yaml
memory:
  max_messages: 20          # 最大保留消息数
  keep_recent: 4            # 保留最近 N 条不压缩
  summary_model: ""         # 压缩专用模型（留空使用主模型）
```

### MCP 服务器配置

```yaml
mcp_servers:
  - name: "filesystem"
    transport: "stdio"
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
  - name: "sqlite"
    transport: "stdio"
    command: "uvx"
    args: ["mcp-server-sqlite", "--db-path", "./data.db"]
```

---

## API 文档

### 对话管理

| 方法 | 端点 | 说明 |
|------|------|------|
| `POST` | `/conversations` | 创建新对话 |
| `GET` | `/conversations` | 列出所有对话 |
| `GET` | `/conversations/{id}` | 获取对话详情 |
| `PATCH` | `/conversations/{id}` | 更新对话设置（标题、知识库等） |
| `DELETE` | `/conversations/{id}` | 删除对话及关联数据 |

### 文件管理

| 方法 | 端点 | 说明 |
|------|------|------|
| `POST` | `/conversations/{id}/files` | 上传文件到对话知识库（支持拖拽） |
| `GET` | `/conversations/{id}/files` | 列出对话已上传文件 |
| `DELETE` | `/conversations/{id}/files/{file_id}` | 删除文件 |

文件上传后自动触发摄取流水线：去重 → 加载 → 分块 → 元数据增强 → 向量化 → 入库。

### 聊天接口

#### 同步对话

```bash
POST /chat
Content-Type: application/json

{
  "conversation_id": "conv_xxx",
  "message": "请介绍一下产品定价策略",
  "stream": false
}
```

#### 流式对话（SSE）

```bash
POST /chat/stream
Content-Type: application/json

{
  "conversation_id": "conv_xxx",
  "message": "请介绍一下产品定价策略",
  "stream": true
}
```

响应格式：

```
data: {"type": "token", "content": "根据"}
data: {"type": "token", "content": "知识库"}
data: {"type": "sources", "content": [{"doc_id": "...", "score": 0.95}]}
data: {"type": "done", "checkpoint_id": "cp_xxx"}
```

- `token`：实时生成的文本片段
- `sources`：检索来源文档列表
- `done`：生成完成，附带 checkpoint ID 用于回滚

### 回滚接口

```bash
POST /conversations/{id}/rollback
Content-Type: application/json

{
  "checkpoint_id": "cp_xxx",
  "strategy": "checkpoint"  // checkpoint | archive | ltm
}
```

### WebSocket 追踪

```javascript
const ws = new WebSocket('ws://localhost:8000/ws/trace/{conversation_id}');

ws.onmessage = (event) => {
  const trace = JSON.parse(event.data);
  // trace.node: 当前执行节点名称
  // trace.state: 节点输入/输出状态摘要
  // trace.timestamp: 执行时间戳
};
```

实时推送 LangGraph 节点级执行轨迹，用于前端 Trace 面板可视化。

---

## 前端界面

基于 Vue 3 + Element Plus 构建的三栏式聊天界面：

### 左侧边栏

- **历史对话列表**：支持搜索、删除、重命名
- **知识库文件管理**：拖拽上传、文件列表、删除操作
- **设置面板**：API 地址、Top K、流式开关、模型选择

### 主聊天区

- **消息轮次（Turn）分组**：用户提问 + AI 回复为一组
- **Checkpoint 时间线**：可点击的分隔线，点击即可回滚到该状态
- **流式输出**：逐字显示效果，非整块刷新
- **检索来源折叠**：每条回复下方可展开查看引用的文档片段

### 右侧 Trace 面板

- **实时节点追踪**：通过 WebSocket 接收 LangGraph 执行轨迹
- **节点耗时统计**：每个节点的输入/输出 token 数、执行耗时
- **状态可视化**：当前活跃节点高亮显示

---

## 可观测性面板

系统集成多层可观测能力：

| 指标类型 | 采集方式 | 存储 |
|---------|---------|------|
| 请求延迟 | FastAPI 中间件 | Prometheus / 日志 |
| Token 消耗 | LangChain 回调 | 每请求日志 |
| 检索质量 | HybridSearch 自统计 | 日志 + 可选上报 |
| 节点耗时 | LangGraph 钩子 | WebSocket 实时推送 |
| 错误率 | 异常拦截器 | 结构化日志 |

### 日志格式

```json
{
  "timestamp": "2024-01-15T10:30:00Z",
  "level": "INFO",
  "module": "hybrid_search",
  "conversation_id": "conv_xxx",
  "event": "fusion_complete",
  "dense_results": 10,
  "sparse_results": 8,
  "fused_results": 5,
  "latency_ms": 145
}
```

---

## 测试体系

```bash
# 运行全部测试
pytest tests/ -v

# 覆盖率报告
pytest tests/ --cov=src --cov-report=html
```

### 测试分类

| 类型 | 路径 | 说明 |
|------|------|------|
| 单元测试 | `tests/unit/` | 核心模块独立测试 |
| 集成测试 | `tests/integration/` | 多模块协作测试 |
| 工作流测试 | `tests/workflow/` | LangGraph 端到端测试 |
| API 测试 | `tests/api/` | FastAPI 接口测试 |
| 性能测试 | `tests/perf/` | 检索延迟、并发压测 |

### 关键测试场景

- **意图路由**：歧义查询 → clarify；知识查询 → rag；工具调用 → tool
- **混合检索**：Dense 失败 → Sparse 降级；双路失败 → 明确异常
- **记忆压缩**：超长对话 → summary 生成 → 上下文不丢失关键信息
- **流式中断**：客户端断开 → 脏 checkpoint 自动回滚
- **并发安全**：多会话同时上传文件 → collection 隔离正确

---

## 开发指南

### 添加新的摄取阶段

在 `src/ingestion/pipeline.py` 中实现 `BaseStage` 子类：

```python
class MyCustomStage(BaseStage):
    async def process(self, chunks: List[DocumentChunk], context: PipelineContext) -> List[DocumentChunk]:
        # 实现你的处理逻辑
        return chunks
```

然后在 `IngestionPipeline` 的 `stages` 列表中注册。

### 添加新的 MCP 工具

无需修改代码，在 `config/settings.yaml` 的 `mcp_servers` 列表中添加即可：

```yaml
mcp_servers:
  - name: "my_custom_tool"
    transport: "stdio"
    command: "python"
    args: ["-m", "my_mcp_server"]
```

重启后端后自动发现。

### 自定义意图分类

修改 `src/ragent_backend/intent.py` 中的 `_TOOL_KEYWORDS` 规则列表，或调整 `detect_intent()` 中的 LLM prompt 模板。

---

## 错误处理与降级策略

系统遵循**优雅降级**原则，任何单点故障不应阻塞主流程：

| 故障场景 | 降级行为 | 用户感知 |
|---------|---------|---------|
| Dense 检索超时 | 退化为 Sparse 检索 | 无感知 |
| Sparse 检索失败 | 退化为 Dense 检索 | 无感知 |
| LLM 压缩记忆失败 | Fallback 简单拼接 | 无感知 |
| LLM 意图识别失败 | 规则关键词匹配兜底 | 可能精度下降 |
| 归档数据库不可达 | 跳过归档，仅保留 checkpoint | 无感知（除跨会话记忆） |
| 工具调用失败 | 返回错误描述，继续对话 | 提示工具执行失败 |

### 脏 Checkpoint 自动回滚

流式生成过程中，若客户端异常断开：

1. 检测到连接关闭
2. 标记当前 checkpoint 为 `dirty`
3. 自动回滚到上一个干净 checkpoint
4. 确保下次对话从一致状态开始

---

## 部署说明

### Docker 部署（推荐）

```bash
# 构建镜像
docker build -t ragent:latest .

# 运行
docker run -d \
  -p 8000:8000 \
  -p 5173:5173 \
  -e RAGENT_LLM_API_KEY=sk-xxx \
  -e RAGENT_POSTGRES_URL=postgresql://... \
  -v ./chroma_db:/app/chroma_db \
  ragent:latest
```

### 生产环境检查清单

- [ ] PostgreSQL 已配置并连接正常
- [ ] ChromaDB 持久化卷已挂载
- [ ] LLM API Key 已通过环境变量注入
- [ ] `start.bat` 中的端口未被占用
- [ ] Nginx/CDN 已配置 SSE 心跳保持（或关闭超时）
- [ ] 日志收集系统已接入（ELK / Loki / 等）
- [ ] 健康检查端点 `/health` 已配置监控

### 性能调优建议

| 瓶颈 | 优化方案 |
|------|---------|
| 检索延迟高 | 启用 `parallel_retrieval: true`，考虑预加载向量索引 |
| Token 消耗大 | 调低 `max_messages`，使用更便宜的 `summary_model` |
| 并发不足 | 使用 Gunicorn + Uvicorn Worker，水平扩展 |
| 前端加载慢 | 启用 Vite 生产构建 `npm run build`，使用 Nginx 托管静态资源 |

---

<div align="center">

**[⬆ 回到顶部](#ragent)**

Made with ❤️ by the ragent team

</div>
