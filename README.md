# MODULAR-RAG-MCP-SERVER（二次开发版）

一个面向生产实践的模块化 RAG 系统，支持：

- MCP Server（stdio 协议）
- 文档摄取与混合检索（Dense + Sparse + RRF）
- 可选重排与评测
- FastAPI 后端
- 基于 LangGraph 的滚动窗口记忆（你的二次开发重点）

本项目基于上游仓库进行二次开发，当前版本重点增强了会话记忆管理与后端接口能力。

## 1. 项目亮点

### 1.1 模块化架构

- `src/ingestion`: 文档摄取流水线（切块、增强、入库）
- `src/core/query_engine`: 查询处理、混合检索、重排
- `src/mcp_server`: MCP 协议入口与工具注册
- `src/ragent_backend`: 对话后端与工作流编排
- `src/observability`: 日志、追踪、评测

### 1.2 你的二次开发能力

你当前代码中已经加入以下关键能力：

- 滚动窗口记忆管理（RollingMemoryManager）
- LangGraph checkpoint 持久化（Postgres/SQLite）
- MySQL 对话归档（完整历史）
- Chat API 与流式输出接口
- 记忆统计与历史查询接口

## 2. 目录结构

```text
rag-pro/
├─ main.py                     # 启动前配置检查入口
├─ config/
│  ├─ settings.yaml            # RAG 核心配置
│  └─ prompts/                 # 提示词模板
├─ docs/
│  └─ api.md                   # MCP 协议接口文档
├─ scripts/
│  ├─ ingest.py                # 摄取脚本
│  ├─ query.py                 # 查询脚本
│  ├─ evaluate.py              # 评测脚本
│  └─ start_dashboard.py       # 可视化入口
├─ src/
│  ├─ core/
│  ├─ ingestion/
│  ├─ libs/
│  ├─ mcp_server/
│  ├─ observability/
│  └─ ragent_backend/          # 二次开发重点
└─ tests/
   ├─ unit/
   ├─ integration/
   └─ e2e/
```

## 3. 环境要求

- Python 3.10+
- 推荐使用虚拟环境（.venv）
- 可选外部依赖：
  - OpenAI/Azure OpenAI（LLM 与 Embedding）
  - MySQL（归档存储）
  - PostgreSQL 或 SQLite（LangGraph checkpoint）

## 4. 安装

### 4.1 创建虚拟环境

```bash
python -m venv .venv
```

Windows PowerShell 激活：

```powershell
.\.venv\Scripts\Activate.ps1
```

### 4.2 安装依赖

```bash
pip install -e .
```

开发依赖：

```bash
pip install -e .[dev]
```

## 5. 配置

### 5.1 RAG 主配置

编辑 `config/settings.yaml`，重点检查：

- `llm`
- `embedding`
- `vector_store`
- `retrieval`
- `rerank`
- `ingestion`

### 5.2 后端环境变量

复制并修改 `.env.example`：

- `OPENAI_API_KEY`
- `RAGENT_SQLITE_PATH` 或 `RAGENT_POSTGRES_URL`
- `RAGENT_MYSQL_*`
- `RAGENT_MAX_MESSAGES`
- `RAGENT_KEEP_RECENT`

## 6. 快速开始

### 6.1 启动前检查

```bash
python main.py
```

### 6.2 摄取文档

```bash
python scripts/ingest.py --path tests/fixtures/sample_documents --collection demo
```

### 6.3 查询知识库

```bash
python scripts/query.py --query "什么是 RAG" --collection demo --top-k 5
```

### 6.4 启动后端 API

```bash
python -m src.ragent_backend.app
```

默认监听端口：8000（可通过 `RAGENT_PORT` 修改）

## 7. 后端 API（你新增/强化部分）

- `GET /health`
- `GET /api/v1/collections`
- `POST /api/v1/chat`
- `POST /api/v1/chat/stream`
- `GET /api/v1/history/{conversation_id}`
- `GET /api/v1/memory/{conversation_id}/stats`

更完整的 MCP 工具协议请见 `docs/api.md`。

## 8. 记忆机制说明

当前实现采用“短期状态 + 长期归档”双层设计：

- 短期状态：LangGraph checkpoint（给模型推理）
- 长期归档：MySQL 全量历史（给用户查询与审计）

当消息数超过阈值时：

1. 保留最近 N 条消息
2. 旧消息合并进入 summary
3. 被压缩与本轮消息异步归档到 MySQL

关键参数：

- `RAGENT_MAX_MESSAGES`
- `RAGENT_KEEP_RECENT`

## 9. 测试

运行全部测试：

```bash
pytest
```

仅运行集成测试：

```bash
pytest -m integration
```

跳过 LLM 相关测试：

```bash
pytest -m "not llm"
```

记忆专项脚本：

```bash
python test_memory.py
```

## 10. 常见问题

### 10.1 MCP 客户端连不上

检查是否通过 stdio 启动 MCP 服务，并确认 stdout 未被日志污染。

### 10.2 没有检索结果

先确认是否完成 ingest，并检查 `collection` 名称一致。

### 10.3 记忆没有生效

确认：

- conversation_id 在多轮请求中保持一致
- checkpoint 存储可写
- `RAGENT_MAX_MESSAGES` 与 `RAGENT_KEEP_RECENT` 配置合理

## 11. 版本与说明

- 当前 README 对应你的二次开发代码形态
- 若后续变更接口或工作流，请同步更新本文件与 `docs/api.md`

## 12. License

MIT
