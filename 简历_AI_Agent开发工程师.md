# 个人简历

---

## 基本信息

| 项目 | 内容 |
|------|------|
| **姓名** | [你的名字] |
| **意向岗位** | AI Agent开发工程师 / 大模型应用工程师 / RAG开发工程师 / LLM Agent工程师 |
| **工作年限** | [X] 年 |
| **学历** | [本科/硕士] · [计算机/软件工程/人工智能等相关专业] |
| **所在城市** | [城市] |
| **GitHub** | https://github.com/[你的用户名] |
| **联系方式** | [手机号] · [邮箱] |

---

## 技术栈

| 方向 | 技术 |
|------|------|
| **Agent框架** | LangGraph · LangChain · LlamaIndex · AutoGen · CrewAI |
| **Agent设计模式** | **Multi-Agent System** · **ReAct** · **CoT思维链** · **Tool Use / Function Calling** · **Task Planning** · **HITL人机协同** · **Evaluator-Optimizer** · **记忆机制** · **Prompt Engineering** |
| **RAG技术** | **检索增强生成(RAG)** · **混合检索(Hybrid Search)** · **BM25稀疏检索** · **Dense向量检索** · **Rerank重排序** · **RRF融合排序** · **知识库构建** · **查询优化(Query Optimization)** |
| **编程语言** | Python(主力) · TypeScript/JavaScript · SQL |
| **后端框架** | FastAPI · Flask · RESTful API设计 · SSE流式输出 · WebSocket |
| **前端框架** | Vue 3 · React · Element Plus · Vite · Streamlit |
| **大模型生态** | **GPT-4 / Claude / Qwen / DeepSeek** · **OpenAI API** · **Embedding模型** · **Chat模型** · **模型微调(Fine-tuning)** |
| **向量数据库** | Chroma · Qdrant |
| **数据库/缓存** | PostgreSQL · MySQL · SQLite · Redis |
| **协议与集成** | **MCP(Model Context Protocol)** · **Function Calling** · **API编排** |
| **工程化** | Docker · pytest · CI/CD · Git · 模块化架构 · 微服务设计 |
| **评估与观测** | **Ragas评估** · Pipeline Trace · 瀑布流可视化 · 黄金测试集 · A/B Test · 消融实验(Ablation Study) |

---

## 项目经历

---

### 项目一：RAG-Pro — 生产级模块化RAG知识库系统（个人项目）

> **项目定位**：面向生产环境的企业级对话级知识库问答系统，支持REST API与**MCP协议**接入  
> **技术栈**：Python · FastAPI · **LangGraph** · **LangChain** · Vue 3 · Chroma · PostgreSQL · MySQL · **MCP协议**  
> **GitHub**：[你的仓库链接]

#### 核心指标

| 指标 | 数值 | 说明 |
|------|------|------|
| **检索模块 Hit Rate** | **97%** | MS MARCO数据集，Hybrid + Rerank |
| **检索模块 MRR** | **95.75%** | Hybrid + Cross-Encoder Rerank |
| **查询分析准确率** | **100%** | 指代消解+子查询拆分，5/5 cases通过 |
| **Ingestion Pipeline完整性** | **100%** | 10个stage trace零缺失 |
| **测试覆盖** | **60+** | 50+单元测试 + 10+集成测试 + E2E测试 |

#### 系统架构
```
┌─────────────┐     SSE流式      ┌──────────────┐     LangGraph      ┌─────────────────┐
│  Vue3前端   │ ◄──────────────► │  FastAPI     │ ────────────────► │  RAG Pipeline   │
│  聊天界面   │                  │  REST API    │    状态机编排      │  Retrieve→Rerank│
└─────────────┘                  └──────┬───────┘                   │  →Generate      │
                                        │                          └─────────────────┘
                                MCP协议 │
                                stdio   │    ┌──────────────────────────────────────┐
                                        └──► │  MCP Server (3 Tools)                │
                                             │  · query_knowledge_hub               │
                                             │  · list_collections                  │
                                             │  · get_document_summary              │
                                             └──────────────────────────────────────┘
```

#### 核心职责与成果

**1. 设计并实现端到端RAG Pipeline（检索增强生成）**
- 基于 **LangGraph 状态机**编排 RAG 完整链路：**Dense向量检索 → Rerank重排序 → LLM生成**
- 实现 **Hybrid Search混合检索**：语义检索(Dense Embedding) + 关键词检索(BM25稀疏索引) + RRF融合排序
- **消融实验验证**：在MS MARCO数据集上对比4种检索策略，Hybrid + Cross-Encoder Rerank 达到 **Hit Rate 97% / MRR 95.75%**，较纯BM25（Hit Rate 83%）提升14个百分点
- 支持 Chroma/Qdrant 多向量数据库动态切换，检索层可插拔架构

**2. 会话级知识库与记忆管理**
- 每个对话拥有独立Chroma Collection，RAG检索严格限定在当前会话文档范围，实现**会话级数据隔离**
- 设计**滑动窗口记忆压缩** + **LTM长期记忆存储**：LangGraph Checkpoint + MySQL双轨制，自动压缩历史消息，支持跨会话知识沉淀与召回

**3. 实现MCP(Model Context Protocol)协议接入**
- 独立开发 **stdio-based MCP Server**，暴露3个核心Tool：query / list / get_document_summary
- RAG能力可被 **Claude Desktop、Cursor** 等MCP客户端**原生调用**，实现LLM与知识库的Function Calling闭环

**4. 构建文件摄取与向量化Pipeline**
- 6阶段同步Pipeline：PDF/TXT/MD/CSV解析 → 智能分块(Chunking) → LLM Refine/Enrich/Caption → 向量化入库
- 后台异步任务处理，前端轮询实时观测状态流转
- **Ingestion Trace 100%完整**：10个处理阶段（load→split→chunk_refiner→llm_enrich→metadata_enricher→transform→batch→embed→upsert）全部可观测

**5. 查询优化**
- 结构化LLM一次完成 **指代消解(Coreference Resolution)** + **子查询拆分(Query Decomposition)**
- **Query Analysis Benchmark**：5个测试cases（代词消解、并列拆分、无历史上下文等）全部通过
- 支持 **OpenAI / Azure / DeepSeek / Ollama** 多LLM Provider动态切换

**6. 可观测性与评估体系**
- Pipeline Trace 瀑布流记录每个RAG步骤的耗时与中间状态
- Streamlit Dashboard 实时可视化检索质量与性能指标
- 集成 **Ragas评估框架**，支持基于黄金测试集自动化评估
- 设计**消融实验(Ablation Study)**对比Dense-only / Sparse-only / Hybrid / Hybrid+Rerank 4种策略

**7. 工程化与测试体系**
- 构建 **3层测试体系**：**50+ 单元测试** + **10+ 集成测试** + **E2E端到端测试**
- golden_test_set 黄金测试集（MS MARCO）持续回归，确保检索模块迭代不退化
- 模块化分层架构：Ingestion / Retrieval / Generation / Observability 各层解耦

---

### 项目二：LangResearch — 基于LangGraph的Multi-Agent智能深度研究系统（个人项目）

> **项目定位**：对标 GPT Researcher 的深度研究Agent，采用 **Multi-Agent协作架构** 实现自主调研与报告生成  
> **技术栈**：Python · **LangGraph** · **LangChain** · Tavily Search · OpenAI API  
> **GitHub**：[你的仓库链接]

#### Multi-Agent系统架构
```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Multi-Agent 深度研究系统                              │
│                                                                             │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────────┐ │
│  │ Clarify    │──►│   Plan      │──►│  Router     │──►│ Synthesize      │ │
│  │ Agent      │   │  Agent      │   │  Agent      │   │  Agent          │ │
│  │ (HITL)     │   │(Task Planning│   │(调度分发)   │   │ (综合汇总)      │ │
│  └─────────────┘   └─────────────┘   └──────┬──────┘   └─────────────────┘ │
│                                             │                               │
│                              Send并行Fan-out │                               │
│                    ┌────────────────────────┼────────────────────────┐      │
│                    ▼                        ▼                        ▼      │
│           ┌───────────────┐       ┌───────────────┐       ┌───────────────┐│
│           │ Research      │       │ Research      │       │ Research      ││
│           │ Sub-Agent #1  │       │ Sub-Agent #2  │       │ Sub-Agent #3  ││
│           │ search→think  │       │ search→think  │       │ search→think  ││
│           │ →finalize     │       │ →finalize     │       │ →finalize     ││
│           └───────┬───────┘       └───────┬───────┘       └───────┬───────┘│
│                   └────────────────────────┼────────────────────────┘      │
│                                            │ operator.add合并              │
│                                            ▼                               │
│                                   ┌─────────────────┐                      │
│                                   │  Report Agent   │                      │
│                                   │(Evaluator-      │                      │
│                                   │ Optimizer模式)  │                      │
│                                   │ Draft→Eval→    │                      │
│                                   │ Rewrite×2       │                      │
│                                   └────────┬────────┘                      │
│                                            │                               │
│                                            ▼                               │
│                                   ┌─────────────────┐                      │
│                                   │  Verify Agent   │                      │
│                                   │ (质量校验)       │                      │
│                                   └─────────────────┘                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### 核心职责与成果

**1. 设计Multi-Agent协作系统（主图+嵌套子图架构）**
- **调度Agent（主图）**：负责任务规划(Task Planning)、子任务路由分发、结果综合汇总(Synthesize)
- **研究Agent（嵌套子图）**：每个子任务由独立的Research Sub-Agent执行，内部运行 `search → think → finalize` 的**ReAct推理循环**
- 通过 **Send机制实现并行Fan-out**，最多3个Research Agent并发执行，最多2轮委托迭代
- 各Agent通过标准化 `Finding` 结构体(subtask/summary/sources/reflections)通信，实现**多Agent信息融合**

**2. 实现HITL人机协同节点（Human-in-the-Loop）**
- 设计**置信度阈值机制**：当调度Agent对用户query意图置信度 `< 0.65` 时，自动触发 `interrupt()` 中断
- 引导用户提供澄清信息（时间范围、关注维度、输出格式），最多2轮HITL交互后进入正式研究阶段
- 解决模糊Query导致的研究方向偏差问题，提升最终报告的**相关性与准确性**

**3. 实现Evaluator-Optimizer质量优化模式**
- 报告生成采用 **Draft → Evaluate → Rewrite** 闭环优化，最多2轮自动迭代
- Evaluate维度：完整性、准确性、引用规范性、逻辑连贯性
- 根据评估反馈自动重写，显著提升报告质量，避免LLM"幻觉"输出
- **Verify Agent**：规则化校验清单，确保token覆盖率、引用来源、inline citation完整性

**4. 设计ReAct推理循环（Research Sub-Agent内部）**
- 每个Research Agent内部实现 **ReAct(Reasoning + Acting)** 模式：
  - **Reason**：LLM分析当前已收集信息，判断是否需要继续搜索
  - **Act**：调用Tavily Search工具执行搜索，获取新信息
  - **Observation**：整合新信息到已有知识，更新研究状态
- 支持**工具调用(Tool Use)**，可扩展接入Arxiv、本地文档、搜索引擎等多源检索

**5. 预算控制与系统鲁棒性**
- **搜索预算硬控制**：每线程最多2次Tavily搜索调用，防止资源滥用与成本失控
- **启发式Fallback降级**：当LLM输出格式异常或超时时，自动降级到规则-based处理，确保系统不崩溃
- 所有Agent节点具备错误隔离与状态恢复能力，单个子任务失败不影响整体研究流程

**6. 状态管理与并发安全**
- 使用 `Annotated[list[Finding], operator.add]` 自定义reducer，实现并行研究分支的**安全合并**
- 基于LangGraph的持久化机制，支持研究流程的**断点续传**与**状态回溯**

---

## 两个项目的关系

| 维度 | RAG-Pro | LangResearch |
|------|---------|-------------|
| **定位** | 被动检索系统（用户问，系统答） | 主动研究系统（自主调研，生成报告） |
| **Agent形态** | LangGraph状态机编排RAG Pipeline | **真正的Multi-Agent系统**（调度Agent+研究Agent+报告Agent+校验Agent） |
| **检索能力** | 强（Hybrid Search + Rerank） | 弱（仅Tavily单源） |
| **协作能力** | 无 | **多Agent并行协作** |
| **记忆机制** | 会话级隔离 + LTM长期记忆 | 基础session记忆 |
| **前端** | Vue 3完整聊天界面 | 无（CLI/API调用） |
| **互补性** | RAG-Pro可为LangResearch提供**多源检索能力** | LangResearch可为RAG-Pro提供**主动研究+报告生成能力** |

---

## 开源贡献与社区

- **GitHub**：[你的GitHub主页]，维护2个AI Agent相关开源项目
- **技术方向**：LangGraph Multi-Agent架构 · RAG系统 · MCP协议
- 活跃于LangChain/LangGraph社区，关注Multi-Agent System最新进展

---

## 教育背景

| 时间 | 学校 | 专业 | 学历 |
|------|------|------|------|
| 20XX.09 - 20XX.06 | [学校名称] | [计算机科学与技术 / 软件工程 / 人工智能] | [本科/硕士] |

---

## 自我评价

> **一句话定位**：具备 **RAG全链路工程化** + **Multi-Agent系统架构设计** 双重能力的AI Agent开发工程师

1. **RAG检索增强生成全链路能力**：从文档摄取(Ingestion)、混合检索(Hybrid Search)、重排序(Rerank)到生成的完整Pipeline均有生产级落地经验。基于LangGraph状态机编排RAG工作流，MS MARCO数据集验证检索模块 **Hybrid + Rerank达到97% Hit Rate / 95.75% MRR**，较纯BM25提升14个百分点。

2. **Multi-Agent系统架构能力**：基于LangGraph设计并落地 **Multi-Agent协作系统**（调度Agent + 研究Agent + 报告Agent + 校验Agent多角色分工），实践 **ReAct · CoT · Task Planning · Tool Use · HITL · Evaluator-Optimizer** 等Agent核心设计模式。

3. **两个项目的互补性**：RAG-Pro（强检索 + 工程完整）+ LangResearch（强架构 + Multi-Agent），两者结合可构建**既能深度检索又能自主研究**的完整Agent系统。

4. **大模型工程化与协议集成**：熟悉 **MCP协议**、Function Calling、Prompt Engineering，能将LLM能力通过标准化协议接入外部系统。

5. **数据驱动的工程素养**：注重**可观测性**与**量化评估**，构建消融实验对比不同检索策略，基于黄金测试集与60+测试用例持续优化系统性能。

---

> **面试准备重点**：
> 1. **RAG-Pro vs LangResearch的区别**：前者是RAG Pipeline状态机编排，后者是Multi-Agent协作系统，两者如何互补？
> 2. **消融实验**：Dense-only(98%/3.6s) vs Sparse-only(83%/32ms) vs Hybrid+Rerank(97%/9.4s)，为什么选这个 trade-off？
> 3. **Multi-Agent协作**：Send并行Fan-out的实现？子图与主图的状态传递？
> 4. **Evaluator-Optimizer**：评估维度？Rewrite触发条件？与单轮生成的优劣对比？
> 5. **HITL**：LangGraph interrupt/resume机制？thread_id在状态恢复中的作用？
> 6. **MCP协议**：stdio-based的设计动机？Tool Schema定义？客户端发现机制？
