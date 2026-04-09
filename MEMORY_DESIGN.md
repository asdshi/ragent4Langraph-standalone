# 滑动窗口记忆模块设计

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│  LangGraph State                                            │
│  ├── messages: Annotated[list, add_messages]  ← 最近4轮     │
│  └── summary: str                             ← 滚动摘要    │
└─────────────────────────────────────────────────────────────┘
                              │
           ┌──────────────────┴──────────────────┐
           ▼                                      ▼
┌─────────────────────┐              ┌─────────────────────┐
│  Checkpoint         │              │  MySQL Archive      │
│  (SQLite/Postgres)  │              │  (用户可见历史)      │
│                     │              │                     │
│  存给模型用的精简状态  │              │  存完整对话历史      │
│  - messages (4轮)   │              │  - 所有消息          │
│  - summary          │              │  - 时间戳           │
└─────────────────────┘              └─────────────────────┘
```

## 核心机制

### 1. 滑动窗口压缩

当 `messages` 超过 `max_messages`（默认20）时：

```python
# 保留最近的 keep_recent 条（默认4条）
to_keep = messages[-keep_recent:]     # 保留给模型
to_archive = messages[:-keep_recent]  # 归档到 MySQL

# 旧消息合并到 summary
new_summary = rewrite_summary(old_summary + to_archive)

# 使用 RemoveMessage 删除旧消息
return {"messages": [RemoveMessage(id=m.id) for m in to_archive]}
```

### 2. 滚动摘要重写

Prompt 设计确保保留：
- **专有名词**：项目名、人名、技术栈
- **具体结论**：数字、决策、事实
- **用户偏好**：喜好、约束、反对意见

### 3. 数据流

```
用户提问
    ↓
LangGraph 自动从 Checkpoint 加载 state
    ↓
session_node: 确保 ID，初始化默认值
    ↓
intent → retrieve → generate
    ↓
memory_manage_node:
    ├── 检查消息数 > max_messages?
    ├── 否 → 跳过
    └── 是 → RemoveMessage 删除旧消息 + 重写 summary
    ↓
archive_node:
    └── 异步保存到 MySQL（被移除的消息 + 本轮新消息）
    ↓
LangGraph 自动保存新 state 到 Checkpoint
    ↓
返回给用户
```

## 配置

```bash
# 记忆管理
RAGENT_MAX_MESSAGES=20    # 触发压缩的阈值
RAGENT_KEEP_RECENT=4      # 始终保留的最近消息数

# Checkpointer（二选一）
RAGENT_POSTGRES_URL=postgresql://...     # 生产环境
RAGENT_SQLITE_PATH=checkpoints.sqlite    # 本地开发

# MySQL 归档
RAGENT_MYSQL_HOST=127.0.0.1
RAGENT_MYSQL_PORT=3306
RAGENT_MYSQL_USER=root
RAGENT_MYSQL_PASSWORD=xxx
RAGENT_MYSQL_DATABASE=ragent
```

## API 变更

### 新增接口

```bash
# 获取完整历史（从 MySQL 加载）
GET /api/v1/history/{conversation_id}

# 获取记忆统计（从 Checkpoint 加载）
GET /api/v1/memory/{conversation_id}/stats
```

### 流式响应增强

```json
{
  "type": "done",
  "conversation_id": "xxx",
  "memory_stats": {
    "message_count": 4,
    "max_messages": 20,
    "need_compact": false,
    "summary_length": 350
  }
}
```

## 与旧方案对比

| 维度 | 旧方案 | 新方案（滑动窗口） |
|------|--------|-------------------|
| 模型上下文 | 固定4轮 + 线性增长summary | 固定4轮 + 可控summary |
| 支持对话长度 | 有限 | 无限（滚动压缩） |
| Token 控制 | 一般 | 严格可控 |
| 首响应延迟 | 低 | 需要异步优化 |
| 实现复杂度 | 低 | 中等 |

## 关键代码路径

- **状态定义**: `src/ragent_backend/schemas.py`
- **记忆管理**: `src/ragent_backend/memory_manager.py`
- **工作流**: `src/ragent_backend/workflow.py`
- **归档存储**: `src/ragent_backend/store.py`
- **API 入口**: `src/ragent_backend/app.py`

## 测试

```bash
# 运行测试脚本
python test_memory.py
```

测试内容：
1. 多轮对话（超过压缩阈值）
2. 验证 checkpoint 中只保留精简状态
3. 验证 MySQL 中有完整历史
4. 验证摘要正确更新
