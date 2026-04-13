# RAG Agent 配置指南

## 🔑 必需的 API Key

目前项目只需要 **一个 OpenAI API Key**，用于：

| 用途 | 模型 | 说明 |
|------|------|------|
| **LLM (对话)** | gpt-4o | 生成回答、意图识别 |
| **Embedding** | text-embedding-ada-002 | 文档向量化和检索 |

> **注意**: Embedding 和 LLM 都使用同一个 `OPENAI_API_KEY`

---

## 📁 配置方式（推荐）

### 方式1: 环境变量（最简单）

创建 `.env` 文件在项目根目录：

```bash
# 必需的配置
OPENAI_API_KEY=sk-your-openai-api-key-here

# 可选的 LLM 模型配置（默认 gpt-4o）
RAGENT_LLM_MODEL=gpt-4o

# 可选的数据库配置（使用 SQLite 可跳过）
RAGENT_SQLITE_PATH=checkpoints.sqlite

# MySQL 归档存储（可选）
RAGENT_MYSQL_HOST=127.0.0.1
RAGENT_MYSQL_PORT=3306
RAGENT_MYSQL_USER=root
RAGENT_MYSQL_PASSWORD=your_mysql_password
RAGENT_MYSQL_DATABASE=ragent

# 服务器配置
RAGENT_PORT=8000
```

### 方式2: 配置文件

编辑 `config/settings.yaml`：

```yaml
# LLM 配置
llm:
  provider: "openai"
  model: "gpt-4o"
  api_key: "your-openai-api-key-here"  # <-- 填写你的 key
  temperature: 0.0
  max_tokens: 4096

# Embedding 配置
embedding:
  provider: "openai"
  model: "text-embedding-ada-002"
  api_key: "your-openai-api-key-here"  # <-- 同样的 key
  dimensions: 1536
```

---

## 🔧 如何获取 OpenAI API Key

1. 访问 https://platform.openai.com/
2. 注册/登录账号
3. 进入 **API keys** 页面
4. 点击 **Create new secret key**
5. 复制生成的 key（以 `sk-` 开头）

> ⚠️ **重要**: API Key 只会显示一次，请妥善保存！

---

## 💰 费用说明

| 模型 | 用途 | 大致费用 |
|------|------|---------|
| gpt-4o | 对话生成 | $5 / 1M tokens |
| text-embedding-ada-002 | 文档向量化 | $0.10 / 1M tokens |

> 对于个人测试，OpenAI 新账号通常有 $5 免费额度

---

## 🧪 无 API Key 测试方式

如果你没有 OpenAI API Key，可以：

### 1. 使用 Ollama（本地免费）

修改 `config/settings.yaml`:

```yaml
llm:
  provider: "ollama"
  model: "llama3.1"
  base_url: "http://localhost:11434"

embedding:
  provider: "ollama"
  model: "nomic-embed-text"
  base_url: "http://localhost:11434"
```

然后安装 Ollama:
```bash
# 安装 Ollama (Windows/Mac/Linux)
# https://ollama.com/

# 拉取模型
ollama pull llama3.1
ollama pull nomic-embed-text
```

### 2. 使用 Azure OpenAI（企业）

如果你有 Azure OpenAI 服务：

```yaml
llm:
  provider: "azure"
  deployment_name: "gpt-4o"
  azure_endpoint: "https://your-resource.openai.azure.com/"
  api_key: "your-azure-key"
  api_version: "2024-02-15-preview"
```

---

## 📋 快速检查清单

启动前确认：

- [ ] 已创建 `.env` 文件
- [ ] `.env` 中设置了 `OPENAI_API_KEY=sk-...`
- [ ] （可选）安装了 MySQL 并配置了数据库
- [ ] 前端 `.env` 或设置中 API 地址正确

---

## 🚀 启动命令

```bash
# 1. 确保依赖已安装
pip install -e .

# 2. 启动后端
python -m src.ragent_backend.app

# 3. 启动前端（新开终端）
cd frontend
npm run dev -- --host
```

---

## ❓ 常见问题

**Q: 提示 "OpenAI API key not provided"?**
A: 检查 `.env` 文件是否存在且包含 `OPENAI_API_KEY`

**Q: 提示 "Invalid API key"?**
A: 检查 key 是否复制完整（以 `sk-` 开头）

**Q: 如何切换模型？**
A: 修改 `RAGENT_LLM_MODEL` 环境变量，如 `gpt-4o-mini` 更便宜

**Q: 可以不联网使用吗？**
A: 可以，使用 Ollama 本地模型（见上文）
