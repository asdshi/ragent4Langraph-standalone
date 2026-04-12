# RAG Agent 前端

基于 Vue 3 + Element Plus 的智能对话前端应用。

## 功能特性

- 💬 **智能对话** - 支持流式输出，实时显示 AI 回复
- 📎 **文件管理** - 拖拽上传，支持多种文件格式（PDF, Word, TXT, CSV 等）
- 🔍 **检索来源** - 显示 RAG 检索来源，可追溯答案出处
- ⚙️ **灵活配置** - 支持自定义 API 地址、模型参数等
- 📱 **响应式设计** - 适配桌面和移动端

## 技术栈

- Vue 3 (Composition API)
- Element Plus (UI 组件库)
- Axios (HTTP 客户端)
- Vite (构建工具)

## 快速开始

### 1. 安装依赖

```bash
cd frontend
npm install
```

### 2. 配置后端地址

默认后端地址为 `http://localhost:8000`，可在页面设置中修改。

### 3. 启动开发服务器

```bash
npm run dev
```

访问 http://localhost:5173

### 4. 构建生产版本

```bash
npm run build
```

构建产物位于 `dist/` 目录。

## 项目结构

```
frontend/
├── src/
│   ├── App.vue          # 主应用组件
│   ├── main.js          # 入口文件
│   ├── components/      # 组件目录
│   └── views/           # 页面视图
├── index.html           # HTML 模板
├── package.json         # 项目配置
├── vite.config.js       # Vite 配置
└── README.md           # 本文档
```

## API 接口

前端连接后端 REST API，主要接口包括：

| 接口 | 说明 |
|------|------|
| `POST /api/v1/chat` | 发送消息 |
| `POST /api/v1/chat/stream` | 流式对话 |
| `GET /api/v1/history/{id}` | 获取历史 |
| `POST /api/v1/conversations/{id}/files` | 上传文件 |
| `GET /api/v1/conversations/{id}/files` | 列出文件 |
| `DELETE /api/v1/conversations/{id}/files/{file_id}` | 删除文件 |

详见 `docs/API.md`

## 配置说明

设置项保存在浏览器 LocalStorage 中：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| API 基础地址 | `http://localhost:8000/api/v1` | 后端服务地址 |
| 检索数量 (Top K) | 5 | RAG 检索文档数 |
| 流式输出 | 开启 | 是否使用 SSE 流式响应 |
| 模型 | GPT-4o | 使用的 LLM 模型 |

## 使用说明

1. **新建对话** - 点击"新建对话"按钮开始新的对话
2. **上传文件** - 拖拽或点击上传文件，支持批量上传
3. **提问** - 输入问题，AI 会基于上传的文档回答
4. **查看来源** - 点击 AI 回复下方的"检索来源"查看引用文档

## 开发计划

- [ ] Markdown 渲染增强
- [ ] 对话历史列表
- [ ] 代码高亮
- [ ] 深色模式
- [ ] 语音输入
- [ ] 图片预览
