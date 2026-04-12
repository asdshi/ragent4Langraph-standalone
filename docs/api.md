# RAG Agent API 文档

> **版本**: v1.0  
> **基础 URL**: `http://localhost:8000/api/v1`  
> **最后更新**: 2026-04-12

---

## 目录

1. [概述](#概述)
2. [认证](#认证)
3. [接口清单](#接口清单)
4. [详细接口说明](#详细接口说明)
5. [错误码](#错误码)
6. [示例代码](#示例代码)

---

## 概述

RAG Agent 提供 RESTful API 接口，支持：

- **对话管理** - 创建对话、发送消息、获取历史
- **文件管理** - 上传文件、列出文件、删除文件
- **流式对话** - SSE 格式的实时响应流
- **记忆管理** - 查看对话记忆统计

所有接口返回 JSON 格式数据（除非指定为 SSE 流）。

### 基础 URL

```
开发环境: http://localhost:8000/api/v1
```

### 请求格式

- `Content-Type: application/json` - 用于 POST/PUT 请求体
- `Content-Type: multipart/form-data` - 用于文件上传

---

## 认证

当前版本暂未实现认证机制，所有接口公开访问。

> **TODO**: 后续版本将添加 API Key 或 JWT 认证

---

## 接口清单

| 方法 | 路径 | 说明 |
|------|------|------|
| **对话接口** |||
| POST | `/chat` | 发送消息（非流式） |
| POST | `/chat/stream` | 发送消息（流式 SSE） |
| GET | `/history/{conversation_id}` | 获取对话历史 |
| **文件接口** |||
| POST | `/conversations/{conversation_id}/files` | 上传文件 |
| GET | `/conversations/{conversation_id}/files` | 列出文件 |
| DELETE | `/conversations/{conversation_id}/files/{file_id}` | 删除文件 |
| **记忆接口** |||
| GET | `/memory/{conversation_id}/stats` | 获取记忆统计 |

---

## 详细接口说明

### 1. 对话接口

#### 1.1 发送消息（非流式）

```http
POST /api/v1/chat
Content-Type: application/json
```

**请求体**:

```json
{
  "query": "总结一下这份文档的主要内容",
  "conversation_id": "conv_abc123",
  "top_k": 5,
  "model": "gpt-4o"
}
```

**参数说明**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | ✅ | 用户提问内容 |
| `conversation_id` | string | ❌ | 对话 ID，不传则创建新对话 |
| `top_k` | integer | ❌ | 检索文档数，默认 5 |
| `model` | string | ❌ | 模型 ID，默认使用系统配置 |

**响应示例** (200 OK):

```json
{
  "conversation_id": "conv_abc123",
  "task_id": "task_xyz789",
  "answer": "根据文档内容，主要涉及以下几个方面：1. ... 2. ...",
  "model_id": "gpt-4o",
  "retrieval_sources": [
    {
      "doc_id": "doc_xxx",
      "chunk_id": "chunk_1",
      "score": 0.95,
      "content": "相关文档片段..."
    }
  ],
  "trace_events": [
    {
      "event": "retrieve",
      "timestamp": "2026-04-12T10:30:00Z",
      "details": "Retrieved 5 documents from collection conv_abc123"
    }
  ]
}
```

**字段说明**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `conversation_id` | string | 对话唯一标识 |
| `task_id` | string | 本次请求任务 ID |
| `answer` | string | AI 生成的回答 |
| `model_id` | string | 实际使用的模型 |
| `retrieval_sources` | array | 检索到的文档来源（调试用）|
| `trace_events` | array | 执行过程追踪事件 |

---

#### 1.2 发送消息（流式）

```http
POST /api/v1/chat/stream
Content-Type: application/json
Accept: text/event-stream
```

**请求体**:

与非流式接口相同。

**响应格式** (SSE):

```
event: start
data: {"conversation_id": "conv_abc123", "task_id": "task_xyz789"}

event: token
data: {"token": "根据", "index": 0}

event: token
data: {"token": "文档", "index": 1}

event: token
data: {"token": "内容", "index": 2}

...

event: done
data: {"finish_reason": "stop"}
```

**事件类型**:

| 事件 | 说明 | 数据结构 |
|------|------|---------|
| `start` | 开始生成 | `{conversation_id, task_id}` |
| `token` | 生成的 token | `{token, index}` |
| `error` | 发生错误 | `{error, message}` |
| `done` | 完成 | `{finish_reason}` |

---

#### 1.3 获取对话历史

```http
GET /api/v1/history/{conversation_id}
```

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| `conversation_id` | string | 对话 ID |

**查询参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `limit` | integer | ❌ | 返回消息数上限，默认 100 |
| `offset` | integer | ❌ | 偏移量，默认 0 |

**响应示例** (200 OK):

```json
{
  "conversation_id": "conv_abc123",
  "total_messages": 15,
  "messages": [
    {
      "role": "user",
      "content": "请帮我分析这份报告",
      "timestamp": "2026-04-12T10:00:00Z"
    },
    {
      "role": "assistant",
      "content": "好的，我来为您分析报告内容...",
      "timestamp": "2026-04-12T10:00:05Z",
      "retrieval_sources": [...]
    }
  ]
}
```

---

### 2. 文件接口

#### 2.1 上传文件

```http
POST /api/v1/conversations/{conversation_id}/files
Content-Type: multipart/form-data
```

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| `conversation_id` | string | 对话 ID |

**请求体** (multipart/form-data):

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | File | ✅ | 要上传的文件 |

**支持的文件类型**:

- `.pdf` - PDF 文档
- `.docx` - Word 文档
- `.txt` - 文本文件
- `.md` - Markdown 文件
- `.csv` - CSV 文件
- `.xlsx` - Excel 文件

**响应示例** (200 OK):

```json
{
  "file_id": "file_xyz789",
  "filename": "report.pdf",
  "size": 2048000,
  "mime_type": "application/pdf",
  "status": "pending",
  "conversation_id": "conv_abc123",
  "message": "File uploaded successfully, processing in background",
  "created_at": "2026-04-12T10:30:00Z"
}
```

**状态说明**:

| 状态 | 说明 |
|------|------|
| `pending` | 已上传，等待处理 |
| `ingesting` | 正在解析和向量化 |
| `ready` | 处理完成，可检索 |
| `error` | 处理失败 |

---

#### 2.2 列出文件

```http
GET /api/v1/conversations/{conversation_id}/files
```

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| `conversation_id` | string | 对话 ID |

**查询参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `status` | string | ❌ | 按状态筛选：pending/ingesting/ready/error |

**响应示例** (200 OK):

```json
{
  "conversation_id": "conv_abc123",
  "file_count": 3,
  "files": [
    {
      "file_id": "file_001",
      "filename": "report.pdf",
      "size": 2048000,
      "mime_type": "application/pdf",
      "status": "ready",
      "doc_id": "doc_hash_xxx",
      "chunk_count": 15,
      "created_at": "2026-04-12T10:00:00Z",
      "updated_at": "2026-04-12T10:01:30Z"
    },
    {
      "file_id": "file_002",
      "filename": "data.csv",
      "size": 512000,
      "mime_type": "text/csv",
      "status": "ingesting",
      "doc_id": null,
      "chunk_count": 0,
      "created_at": "2026-04-12T10:30:00Z",
      "updated_at": "2026-04-12T10:30:05Z"
    }
  ]
}
```

---

#### 2.3 删除文件

```http
DELETE /api/v1/conversations/{conversation_id}/files/{file_id}
```

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| `conversation_id` | string | 对话 ID |
| `file_id` | string | 文件 ID |

**响应示例** (200 OK):

```json
{
  "success": true,
  "message": "File deleted successfully",
  "file_id": "file_001",
  "deleted_at": "2026-04-12T11:00:00Z"
}
```

---

### 3. 记忆接口

#### 3.1 获取记忆统计

```http
GET /api/v1/memory/{conversation_id}/stats
```

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| `conversation_id` | string | 对话 ID |

**响应示例** (200 OK):

```json
{
  "conversation_id": "conv_abc123",
  "memory_stats": {
    "total_messages": 45,
    "checkpoint_messages": 12,
    "archived_messages": 33,
    "summary_length": 256,
    "compression_ratio": 0.73
  },
  "window_config": {
    "max_messages": 20,
    "keep_recent": 4
  },
  "last_updated": "2026-04-12T10:30:00Z"
}
```

**字段说明**:

| 字段 | 说明 |
|------|------|
| `total_messages` | 对话总消息数 |
| `checkpoint_messages` | 当前 checkpoint 中的消息数（热数据）|
| `archived_messages` | 已归档到 MySQL 的消息数（冷数据）|
| `summary_length` | 滚动摘要的字符数 |
| `compression_ratio` | 压缩比例 |

---

## 错误码

### HTTP 状态码

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 400 | 请求参数错误 |
| 404 | 资源不存在 |
| 422 | 请求格式校验失败 |
| 500 | 服务器内部错误 |
| 503 | 服务暂时不可用 |

### 错误响应格式

```json
{
  "error": {
    "code": "FILE_NOT_FOUND",
    "message": "File with id 'file_xxx' not found in conversation 'conv_xxx'",
    "details": {
      "file_id": "file_xxx",
      "conversation_id": "conv_xxx"
    }
  }
}
```

### 常见错误码

| 错误码 | 说明 | HTTP 状态码 |
|--------|------|------------|
| `CONVERSATION_NOT_FOUND` | 对话不存在 | 404 |
| `FILE_NOT_FOUND` | 文件不存在 | 404 |
| `FILE_TOO_LARGE` | 文件超过大小限制 | 400 |
| `UNSUPPORTED_FILE_TYPE` | 不支持的文件类型 | 400 |
| `INGESTION_FAILED` | 文件处理失败 | 500 |
| `LLM_UNAVAILABLE` | LLM 服务不可用 | 503 |
| `VECTOR_STORE_ERROR` | 向量数据库错误 | 500 |

---

## 示例代码

### JavaScript/TypeScript

```typescript
const API_BASE = 'http://localhost:8000/api/v1';

// 发送消息
async function sendMessage(query: string, conversationId?: string) {
  const response = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query,
      conversation_id: conversationId,
      top_k: 5
    })
  });
  return await response.json();
}

// 流式对话
async function* streamMessage(query: string, conversationId?: string) {
  const response = await fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, conversation_id: conversationId })
  });
  
  const reader = response.body?.getReader();
  const decoder = new TextDecoder();
  
  while (true) {
    const { done, value } = await reader!.read();
    if (done) break;
    
    const chunk = decoder.decode(value);
    const lines = chunk.split('\n');
    
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        yield JSON.parse(line.slice(6));
      }
    }
  }
}

// 上传文件
async function uploadFile(conversationId: string, file: File) {
  const formData = new FormData();
  formData.append('file', file);
  
  const response = await fetch(
    `${API_BASE}/conversations/${conversationId}/files`,
    {
      method: 'POST',
      body: formData
    }
  );
  return await response.json();
}

// 列出文件
async function listFiles(conversationId: string) {
  const response = await fetch(
    `${API_BASE}/conversations/${conversationId}/files`
  );
  return await response.json();
}

// 删除文件
async function deleteFile(conversationId: string, fileId: string) {
  const response = await fetch(
    `${API_BASE}/conversations/${conversationId}/files/${fileId}`,
    { method: 'DELETE' }
  );
  return await response.json();
}
```

### Python

```python
import requests

API_BASE = "http://localhost:8000/api/v1"

# 发送消息
def send_message(query: str, conversation_id: str = None):
    response = requests.post(
        f"{API_BASE}/chat",
        json={
            "query": query,
            "conversation_id": conversation_id,
            "top_k": 5
        }
    )
    return response.json()

# 流式对话
def stream_message(query: str, conversation_id: str = None):
    import json
    
    response = requests.post(
        f"{API_BASE}/chat/stream",
        json={"query": query, "conversation_id": conversation_id},
        stream=True
    )
    
    for line in response.iter_lines():
        if line.startswith(b"data: "):
            data = json.loads(line[6:])
            yield data

# 上传文件
def upload_file(conversation_id: str, file_path: str):
    with open(file_path, "rb") as f:
        response = requests.post(
            f"{API_BASE}/conversations/{conversation_id}/files",
            files={"file": f}
        )
    return response.json()

# 列出文件
def list_files(conversation_id: str):
    response = requests.get(
        f"{API_BASE}/conversations/{conversation_id}/files"
    )
    return response.json()

# 删除文件
def delete_file(conversation_id: str, file_id: str):
    response = requests.delete(
        f"{API_BASE}/conversations/{conversation_id}/files/{file_id}"
    )
    return response.json()

# 使用示例
if __name__ == "__main__":
    # 发送消息
    result = send_message("你好，请介绍一下自己")
    print(f"回答: {result['answer']}")
    print(f"对话ID: {result['conversation_id']}")
    
    # 流式输出
    for data in stream_message("写一首诗"):
        if "token" in data:
            print(data["token"], end="", flush=True)
```

### cURL

```bash
# 发送消息
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "query": "总结一下这份文档",
    "conversation_id": "conv_abc123",
    "top_k": 5
  }'

# 流式对话
curl -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"query": "你好"}'

# 上传文件
curl -X POST http://localhost:8000/api/v1/conversations/conv_abc123/files \
  -F "file=@/path/to/document.pdf"

# 列出文件
curl http://localhost:8000/api/v1/conversations/conv_abc123/files

# 删除文件
curl -X DELETE http://localhost:8000/api/v1/conversations/conv_abc123/files/file_xyz789

# 获取对话历史
curl http://localhost:8000/api/v1/history/conv_abc123

# 获取记忆统计
curl http://localhost:8000/api/v1/memory/conv_abc123/stats
```

---

## WebSocket 事件（未来版本）

> **注意**: 以下接口在 v1.0 中暂未实现，计划在 v1.1 版本添加

### 连接 WebSocket

```javascript
const ws = new WebSocket('ws://localhost:8000/ws/conversations/{conversation_id}');

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('收到消息:', data);
};
```

### 事件类型

| 事件 | 方向 | 说明 |
|------|------|------|
| `file.ingest.progress` | Server → Client | 文件处理进度更新 |
| `file.ingest.complete` | Server → Client | 文件处理完成 |
| `file.ingest.error` | Server → Client | 文件处理失败 |

---

**文档版本**: v1.0  
**最后更新**: 2026-04-12
