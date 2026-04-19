# TODO List — RAG-Pro / LangResearch 改进计划

> 最后更新: 2026-04-19  
> 核心目标: 将RAG-Pro升级为分层智能体架构 + LangResearch多源检索增强 + 简历定稿

---

## Phase 1: RAG-Pro 架构升级（分层智能体 + 三分支意图路由）

### P0 — 意图识别三分支路由
- [ ] 修改 `IntentResult`，新增 `intent_type` 字段（clarify / rag / tool）
- [ ] 重写 `detect_intent()`，支持 LLM-based 意图分类（不是简单规则）
- [ ] LangGraph 条件边从 2 叉改为 3 叉：`intent → [clarify / retrieve / tool_subgraph]`
- [ ] `clarify_node`：返回澄清提示，不进入后续 Pipeline

### P0 — 工具调用子智能体（核心架构升级）
- [ ] 新建 `src/tool_agent/` 模块，物理隔离子智能体
- [ ] 定义 `ToolSubgraphState`：独立 State Schema，含 `internal_messages` / `tool_summary` / `available_tools` / `failed_tools`
- [ ] 子图拓扑：`think_node → router → [tool_node → think_node]` 循环
- [ ] `think_node`：LLM 分析上下文，输出 `tool_calls` 或结束决策
- [ ] `router_node`：读取 think 输出，路由到 tool / finish
- [ ] `tool_node`：动态 dispatch 工具，捕获异常，记录 `failed_tools`
- [ ] `summarize_node`：子图结束前，多轮 ToolMessage 整理成结构化 `tool_summary`
- [ ] **关键设计**：子图 `internal_messages` 不合并回主图，只返回 `tool_summary`
- [ ] 主图 `generate_node`：从 State 读取 `retrieval_context` + `tool_summary`，统一注入 Prompt

### P1 — MCP Client（RAG-Pro 调用外部工具）
- [ ] 新建 `src/ragent_backend/mcp_client.py`
- [ ] 封装 `MCPClient` 类：支持 stdio / sse 两种 transport
- [ ] 实现 `list_tools()` / `call_tool()` / `disconnect()`
- [ ] 配置化：YAML 中声明外部 MCP Server（Tavily / Filesystem / PostgreSQL 等）
- [ ] 工具注册表：`ToolRegistry` 统一管理内置工具 + 外部 MCP 工具

### P1 — 子图持久化与可观测性
- [ ] 子图独立 Trace：内部 10 轮对话写独立 JSONL，不污染主图 Trace
- [ ] 子图可选 Checkpointer：长时任务配独立 SQLite，短时任务不持久化
- [ ] 子图执行摘要存入 `tool_summary`，含 `latency_ms` / `retry_count` / `status`

---

## Phase 2: LangResearch 多源检索增强

### P0 — LangResearch 通过 MCP 调用 RAG-Pro
- [ ] `langresearch/tools.py` 新增 `rag_pro_search()` 函数
- [ ] MCP Client 连接 RAG-Pro Server，调用 `query_knowledge_hub`
- [ ] Research Sub-Agent 搜索时并行调用：Tavily + RAG-Pro 知识库
- [ ] 结果融合：Tavily 结果 + 知识库结果合并到 `Finding.sources`

### P1 — LangResearch 其他增强
- [ ] 多源检索：Arxiv / DDG / 本地文档（接入 RAG-Pro 的 ingestion pipeline）
- [ ] 长期记忆：向量记忆存储跨会话的研究偏好
- [ ] Streamlit 前端 MVP：研究任务提交 + 进度监控 + 报告展示
- [ ] 测试覆盖：单元测试 + 集成测试补全

---

## Phase 3: 简历定稿与项目展示

### P0 — 简历最终版
- [ ] 基于分层智能体架构重写项目描述（更新 RAG-Pro 部分）
- [ ] 突出 "分层智能体架构 / Agent Delegation / State字段传递" 等差异化设计
- [ ] 量化指标确认：MS MARCO Hit Rate 97% / MRR 95.75% / Query Analysis 5/5
- [ ] 两个项目互补关系表保留
- [ ] 技术栈关键词密度检查（Multi-Agent / ReAct / HITL / Evaluator-Optimizer / MCP）
- [ ] 中英文双版本（可选）

### P1 — GitHub 仓库整理
- [ ] RAG-Pro README 更新：新增架构图（分层智能体）
- [ ] LangResearch README 更新：多源检索说明
- [ ] 项目截图 / Demo GIF（Streamlit Dashboard / Vue 前端）
- [ ] 安装脚本一键运行（`start.bat` / `docker-compose.yml`）

---

## Phase 4: 工程化与部署

### P1 — Docker 化
- [ ] RAG-Pro Dockerfile
- [ ] LangResearch Dockerfile
- [ ] `docker-compose.yml` 一键启动（后端 + 前端 + PostgreSQL + Chroma）

### P2 — CI/CD
- [ ] GitHub Actions：pytest 自动化测试
- [ ] 代码格式化：ruff / black
- [ ] 类型检查：mypy

### P2 — 性能优化
- [ ] 消融实验完整报告（Dense / Sparse / Hybrid / Hybrid+Rerank）
- [ ] Ragas 端到端评估（补全之前 failed 的项）
- [ ] 延迟优化：异步并发检索、连接池

---

## Phase 5: 扩展方向（可选）

- [ ] **多Agent协作**：LangResearch 多个 Research Sub-Agent 分别连接不同数据源
- [ ] **记忆机制升级**：RAG-Pro LTM 长期记忆接入 LangResearch
- [ ] **Eval 框架**：统一评估 RAG-Pro 和 LangResearch 的产出质量
- [ ] **论文/博客**：把分层智能体架构写成技术博客

---

## 当前阻塞项

| 问题 | 状态 | 解决方案 |
|------|------|---------|
| RAG-Pro 当前无 MCP Client | 🔴 阻塞 Phase 1 P1 | 需新建 mcp_client.py |
| LangResearch 仅 Tavily 单源 | 🔴 阻塞 Phase 2 P0 | 需接入 RAG-Pro MCP |
| 简历中 RAG-Pro 架构描述过时 | 🟡 阻塞 Phase 3 P0 | 等 Phase 1 完成再更新 |
| 无 Docker / CI | 🟢 不阻塞 | Phase 4 处理 |

---

## 本周优先执行（推荐顺序）

1. **Day 1-2**: Phase 1 P0 — 意图三分类 + 子图 State Schema 设计
2. **Day 3-4**: Phase 1 P0 — 子图拓扑（think→router→tool→think 循环）
3. **Day 5**: Phase 1 P1 — MCP Client 封装
4. **Day 6**: Phase 2 P0 — LangResearch 连接 RAG-Pro
5. **Day 7**: Phase 3 P0 — 简历更新 + GitHub README 整理

---

> **核心原则**: 分层智能体架构是最大差异化卖点，优先做完这个，其他都可以后补。
