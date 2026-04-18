# RAG Agent - 对话级知识库系统

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688.svg?logo=fastapi)](https://fastapi.tiangolo.com)
[![Vue 3](https://img.shields.io/badge/Vue.js-35495E?logo=vuedotjs)](https://vuejs.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

一个面向生产环境的模块化 RAG (Retrieval-Augmented Generation) 系统，支持**对话级知识库**、**滑动窗口记忆管理**、**文件实时上传与处理**。

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| 💬 **对话级知识库** | 每个对话拥有独立的文档集合，RAG 检索严格限定在当前对话范围内 |
| 📁 **文件实时上传** | 支持 PDF、TXT、MD、CSV 等格式，后台异步 Ingest |
| 🧠 **滑动窗口记忆** | LangGraph Checkpoint + MySQL 双轨制，自动压缩历史消息 |
| 🔍 **混合检索** | Dense (向量) + Sparse (BM25) + RRF Fusion |
| 🎨 **Vue 3 前端** | 现代化的聊天界面，支持对话管理、文件上传、历史查看 |
| 📊 **可观测性** | Pipeline Trace 记录、Dashboard 可视化 |
| 🔌 **MCP 协议** | 支持 Model Context Protocol，可被 Claude Desktop 等客户端调用 |

## 🚀 快速开始

### 方式一：双击启动（推荐 Windows 用户）

```powershell
# 双击运行
start.bat
```

然后访问 http://localhost:5173

### 方式二：手动启动

```powershell
# 终端 1 - 后端 API
python -m src.ragent_backend.app

# 终端 2 - 前端
cd frontend && npm run dev -- --host
```

## 📋 前置要求

- **Python** 3.10+
- **Node.js** 18+ (前端)
- **PostgreSQL** 14+ (Agent 层统一存储)
- **阿里云百炼 API Key** (LLM + Embedding)

## 🔧 安装配置

### 1. 克隆仓库

```bash
git clone <your-repo-url>
cd rag-pro
```

### 2. 安装 Python 依赖

```bash
python -m venv .venv
.\.venv\Scripts\activate  # Windows
pip install -e .
```

### 3. 安装前端依赖

```bash
cd frontend
npm install
```

### 4. 配置环境变量

创建 `.env` 文件：

```bash
# LLM 配置 (阿里云百炼)
OPENAI_API_KEY=sk-your-api-key
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
RAGENT_LLM_MODEL=qwen3.5-omni-flash

# Agent 层存储 (PostgreSQL，必需)
RAGENT_POSTGRES_URL=postgresql://user:password@localhost:5432/ragent

# IG 层存储 (SQLite，文件指纹)
RAGENT_SQLITE_PATH=checkpoints.sqlite

# 记忆管理
RAGENT_MAX_MESSAGES=20
RAGENT_KEEP_RECENT=4
```

### 5. 初始化 PostgreSQL 数据库

```bash
python scripts/init_postgres.py
```

## 📖 使用指南

### 创建对话并上传文件

1. 打开前端界面 http://localhost:5173
2. 点击"新建对话"创建新会话
3. 在左侧拖拽或点击上传文件
4. 等待文件状态变为"就绪"
5. 开始提问关于文件内容的问题

### API 接口

#### 对话管理
```http
# 创建对话
POST /api/v1/conversations
{
  "title": "可选标题"
}

# 获取对话列表
GET /api/v1/conversations

# 删除对话
DELETE /api/v1/conversations/{conversation_id}
```

#### 文件管理
```http
# 上传文件
POST /api/v1/conversations/{conversation_id}/files
Content-Type: multipart/form-data
file: <binary>

# 列出文件
GET /api/v1/conversations/{conversation_id}/files

# 删除文件
DELETE /api/v1/conversations/{conversation_id}/files/{file_id}
```

#### 对话聊天
```http
# 发送消息
POST /api/v1/chat
{
  "query": "总结这份文档",
  "conversation_id": "conv_xxx",
  "top_k": 5
}

# 获取历史消息
GET /api/v1/history/{conversation_id}
```

## 🏗️ 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                      Frontend (Vue 3)                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   Chat UI    │  │ File Manager │  │ Conversation │      │
│  │              │  │              │  │    List      │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Backend (FastAPI)                         │
│  ┌─────────────────────────────────────────────────────┐   │
│  │           RAGWorkflow (LangGraph)                    │   │
│  │  session → intent → retrieve → generate → archive   │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ File Store   │  │ Conversation │  │   Archive    │      │
│  │   (MySQL)    │  │    Store     │  │    Store     │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
       ┌──────────────┐ ┌──────────┐ ┌──────────────┐
       │   ChromaDB   │ │  BM25    │ │    MySQL     │
       │  (Vectors)   │ │ (Sparse) │ │ (Metadata)   │
       └──────────────┘ └──────────┘ └──────────────┘
```

## 📁 项目结构

```
rag-pro/
├── frontend/                  # Vue 3 前端
│   ├── src/
│   │   ├── App.vue           # 主界面
│   │   └── ...
│   └── package.json
├── src/
│   ├── ragent_backend/       # FastAPI 后端
│   │   ├── app.py            # API 入口
│   │   ├── workflow.py       # LangGraph 工作流
│   │   ├── file_store.py     # 文件存储管理
│   │   ├── conversation_store.py  # 对话管理
│   │   ├── memory_manager.py # 滑动窗口记忆
│   │   └── store.py          # 消息归档
│   ├── core/
│   │   └── query_engine/     # 混合检索 (Dense + Sparse)
│   ├── ingestion/            # 文档摄取流水线
│   │   └── pipeline.py       # 6阶段 Ingestion
│   ├── mcp_server/           # MCP 协议实现
│   └── observability/        # Trace 和 Dashboard
├── scripts/                  # 工具脚本
│   ├── init_postgres.py      # PostgreSQL 数据库初始化
│   └── start_dashboard.py    # 启动观测面板
├── config/                   # 配置文件
├── logs/                     # Trace 日志
└── data/                     # 数据存储
    ├── uploads/              # 上传文件
    ├── db/                   # SQLite/Chroma
    └── images/               # 提取的图片
```

## 🔍 核心流程

### 文件上传与 Ingest

```
上传文件
    ↓
保存到磁盘 (data/uploads/{conv_id}/)
    ↓
记录元数据到 MySQL (status: pending)
    ↓
后台异步 Ingest
    ↓
├─ 1. Integrity Check (SHA256)
├─ 2. Load (PDF/Text 解析)
├─ 3. Split (文本切块)
├─ 4. Transform (精炼 + 元数据增强)
├─ 5. Embed (Dense + Sparse 编码)
└─ 6. Upsert (存入 Chroma + BM25)
    ↓
更新状态为 ready
```

### 对话与检索

```
用户提问
    ↓
意图识别 + 查询重写
    ↓
构建 collection = f"conv_{conversation_id}"
    ↓
混合检索
├─ Dense Retrieval (向量相似度)
├─ Sparse Retrieval (BM25)
└─ RRF Fusion (融合排序)
    ↓
LLM 生成回答
    ↓
滑动窗口记忆管理
├─ 保留最近 N 条消息
├─ 旧消息合并到 summary
└─ 异步归档到 MySQL
```

## ⚙️ 配置说明

### 核心配置项

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `OPENAI_API_KEY` | 阿里云百炼 API Key | 必填 |
| `RAGENT_POSTGRES_URL` | PostgreSQL 连接字符串 | 必填 |
| `RAGENT_MAX_MESSAGES` | 记忆窗口大小 | 20 |
| `RAGENT_KEEP_RECENT` | 压缩后保留消息数 | 4 |
| `RAGENT_PORT` | API 端口 | 8000 |

### 模型配置

| 功能 | 默认模型 |
|------|---------|
| 对话 LLM | qwen3.5-omni-flash |
| Embedding | text-embedding-v3 |
| 图片描述 | qwen-vl-max |

## 🧪 测试

```bash
# API 测试
python test_api.py

# 记忆管理测试
python test_memory.py

# 完整测试套件
pytest
```

## 📊 可观测性

启动 Dashboard 查看 Trace：

```bash
python -m streamlit run src/observability/dashboard/app.py
```

访问 http://localhost:8501 查看：
- Ingestion Trace (文件处理全流程)
- Query Trace (检索全流程)
- 数据统计与诊断

## 📝 已知限制

1. **扫描 PDF 需 OCR** - 图片型 PDF 需要额外安装 Tesseract
2. **流式输出为模拟** - 实际是完整生成后分块发送，非真正流式
3. **图片未在前端显示** - 已提取但未做预览功能

## 🔮 路线图

- [ ] 真正的 LLM 流式输出 (SSE)
- [ ] 图片预览与多模态对话
- [ ] 扫描 PDF OCR 支持
- [ ] 用户认证与权限管理
- [ ] 对话导出 (Markdown/PDF)
- [ ] 移动端适配

## 🤝 贡献

欢迎提交 Issue 和 PR！

## 📄 License

MIT License

---

**Made with ❤️ by RAG Agent Team**
