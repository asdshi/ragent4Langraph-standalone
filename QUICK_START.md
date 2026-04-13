# RAG Agent 快速启动指南

## ✅ 当前状态

| 组件 | 状态 |
|------|------|
| 后端 API | ✅ 已修复 CORS，可正常对话 |
| 前端页面 | ✅ 已修复图标显示 |
| 阿里云百炼模型 | ✅ 已配置 qwen3.5-omni-flash |
| 环境变量加载 | ✅ 自动加载 .env |

## 🚀 启动方式

### 方式 1: 双击启动（推荐）
直接双击 **`start.bat`** 文件

### 方式 2: 手动启动
```powershell
# 终端 1 - 后端
python -m src.ragent_backend.app

# 终端 2 - 前端
cd frontend
npm run dev -- --host
```

## 🔧 访问地址

- **前端界面**: http://localhost:5173
- **后端 API**: http://localhost:8000
- **健康检查**: http://localhost:8000/health

## 📋 配置说明

### API Key（已配置）
```bash
# .env 文件
OPENAI_API_KEY=sk-d0b1eb180cb246ad8ea32580f0791a9f
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

### 使用的模型
| 功能 | 模型 |
|------|------|
| 对话 LLM | qwen3.5-omni-flash |
| Embedding | text-embedding-v3 |
| 图片描述 | qwen-vl-max |

## ⚠️ 已知问题

1. **MySQL 归档存储** - 密码错误，不影响核心对话功能
2. **文件上传** - 有 bug 待修复（500 错误）

## 🧪 测试 API

```powershell
python test_api.py
```

预期输出：
```
[OK] Health: 200
[OK] Chat Status: 200
[SUCCESS] API is working!
```

## 📁 主要文件

| 文件 | 说明 |
|------|------|
| `start.bat` | 一键启动脚本 |
| `test_api.py` | API 测试脚本 |
| `CONFIG_GUIDE.md` | 详细配置指南 |
| `docs/API.md` | API 接口文档 |
