# 接口文档

本文档描述 `MODULAR-RAG-MCP-SERVER` 当前对外暴露的接口。这个项目不是传统的 HTTP REST 服务，而是一个通过 MCP（Model Context Protocol）协议暴露工具的 stdio 服务。

## 1. 服务入口

- 主启动校验入口：`main.py`
- MCP stdio 服务入口：`src/mcp_server/server.py`

`main.py` 主要用于读取和校验配置文件；真正对外提供 MCP 能力的是 `src/mcp_server/server.py` 启动的 stdio 服务器。

## 2. 协议层接口

### 2.1 initialize

MCP Client 连接后首先调用 `initialize`，用于完成协议握手并获取服务能力声明。

返回内容包含：

- `serverInfo`
- `capabilities`

当前服务声明的能力主要是 `tools`。

### 2.2 tools/list

列出当前可调用的 MCP 工具。

当前注册的工具包括：

- `query_knowledge_hub`
- `list_collections`
- `get_document_summary`

### 2.3 tools/call

调用指定工具并传入 JSON arguments。

返回值为 MCP `CallToolResult`，常见结构如下：

- `content`: 内容块数组，通常包含 `text`，部分查询场景也可能包含 `image`
- `isError`: 是否为错误响应

## 3. 工具接口总览

| 工具名 | 用途 | 主要入参 |
|---|---|---|
| `query_knowledge_hub` | 在知识库中进行混合检索 | `query`、`top_k`、`collection` |
| `list_collections` | 列出当前可用集合 | `include_stats` |
| `get_document_summary` | 获取某个文档的摘要和元数据 | `doc_id`、`collection` |

## 4. 详细接口说明

### 4.1 query_knowledge_hub

在知识库中执行检索，内部采用混合检索流程，组合语义召回和关键词召回，并在可用时执行重排。

#### 入参

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `query` | string | 是 | 无 | 查询问题或关键词 |
| `top_k` | integer | 否 | 5 | 返回结果数量，范围 1 到 20 |
| `collection` | string | 否 | 配置默认值 | 限定检索的集合名称 |

#### 返回

成功时返回格式化后的检索结果，通常包含：

- 结果摘要
- 引用信息
- 相关文档片段

当结果中包含图片时，响应内容可能会包含 `image` 类型内容块。

#### 失败行为

- 空查询会返回参数错误
- 检索或重排失败时会返回可读错误响应，而不是让协议层直接崩溃

#### 示例

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "query_knowledge_hub",
    "arguments": {
      "query": "What is Azure OpenAI?",
      "top_k": 3,
      "collection": "knowledge_hub"
    }
  }
}
```

### 4.2 list_collections

列出 ChromaDB 中可用的集合，并可选返回统计信息。

#### 入参

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `include_stats` | boolean | 否 | true | 是否返回每个集合的文档数量 |

#### 返回

返回一个集合列表，每项通常包含：

- `name`
- `count`（当 `include_stats=true` 时）
- `metadata`

#### 示例

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "list_collections",
    "arguments": {
      "include_stats": true
    }
  }
}
```

### 4.3 get_document_summary

根据文档 ID 获取文档摘要、标签、来源路径和 chunk 数量。

#### 入参

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `doc_id` | string | 是 | 无 | 文档 ID，可传完整 ID 或 hash 部分 |
| `collection` | string | 否 | 配置默认值 | 指定查询集合 |

#### 返回

成功时返回一个结构化文档摘要，通常包含：

- `doc_id`
- `title`
- `summary`
- `tags`
- `source_path`
- `chunk_count`
- `metadata`

#### 失败行为

- 文档不存在时，返回 `isError=true` 的错误结果
- 集合不存在或底层存储不可用时，也会返回可读错误信息

#### 示例

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "get_document_summary",
    "arguments": {
      "doc_id": "doc_abc123"
    }
  }
}
```

## 5. 响应约定

### 5.1 文本内容

大多数工具返回 `text` 内容块。客户端通常可以直接展示这些文本。

### 5.2 多模态内容

`query_knowledge_hub` 可能返回图片内容块，用于展示检索命中的图像资源。

### 5.3 错误处理

协议层对以下情况做了统一处理：

- 未注册工具：返回“Tool not found”类错误
- 参数不合法：返回“Invalid parameters”类错误
- 内部异常：返回通用内部错误，不暴露详细堆栈给客户端

## 6. 推荐调用顺序

1. 调用 `initialize`
2. 调用 `tools/list` 查看可用工具
3. 先用 `list_collections` 找到目标集合
4. 使用 `query_knowledge_hub` 查询知识
5. 需要查看某篇文档时，再调用 `get_document_summary`

## 7. 备注

- 该服务使用 stdio 作为传输层，stdout 只能输出 JSON-RPC 协议消息。
- 日志已重定向到 stderr，避免污染协议流。
- 工具实现是可插拔的，后续新增工具时应同步更新本文档。
