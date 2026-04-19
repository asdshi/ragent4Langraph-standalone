# 个人简历

---

## 基本信息

| 项目 | 内容 |
|------|------|
| **姓名** | [你的名字] |
| **意向岗位** | AI Agent开发工程师 / 大模型应用工程师 / RAG开发工程师 |
| **工作年限** | [X] 年 |
| **学历** | [本科/硕士] · [计算机/软件工程/人工智能等相关专业] |
| **所在城市** | [城市] |
| **GitHub** | https://github.com/[你的用户名] |
| **联系方式** | [手机号] · [邮箱] |

---

## 技术栈

| 维度 | 关键词 |
|------|--------|
| **Agent框架** | LangGraph · LangChain · LlamaIndex · ReAct · CoT · Task Planning · Tool Use · HITL · Evaluator-Optimizer · Multi-Agent System |
| **RAG技术** | Hybrid Search · Dense Retrieval · Sparse Retrieval(BM25) · RRF Fusion · Cross-Encoder Rerank · 向量检索 · 知识库构建 · 查询优化(Query Decomposition) |
| **大模型生态** | GPT-4 · Claude · Qwen · DeepSeek · OpenAI API · Embedding模型 · Prompt Engineering · Function Calling |
| **后端** | Python · FastAPI · RESTful API · SSE流式输出 · MCP(Model Context Protocol) |
| **前端** | Vue 3 · React · Element Plus · Streamlit |
| **数据库** | Chroma · Qdrant · PostgreSQL · MySQL · SQLite · Redis |
| **工程化** | Docker · pytest · TDD · 工厂模式 · 配置驱动 · 模块化架构 · 幂等设计 |

---

## 项目经历

---

### 项目一：智能知识检索与问答系统（RAG-Pro）| 个人项目

**背景**：针对企业级知识库场景中文档分散、检索精度不足、AI Agent难以接入私有知识的共性痛点，设计并实现了面向生产环境的模块化RAG检索框架。

**目标**：构建支持Hybrid Search + MCP协议的智能知识检索系统，实现精准语义检索与AI Agent直接调用私有知识库的能力，将检索命中率提升至90%以上。

**过程**：
• 基于 **LangGraph 状态机**编排端到端RAG Pipeline，实现 Retrieve → Rerank → Generate 的完整链路，支持SSE流式输出与Vue 3前端实时渲染
• 设计并实现 **Hybrid Search 混合检索引擎**：Dense语义检索(Cosine Similarity) + Sparse关键词检索(BM25) 双路并行召回，通过RRF融合算法平衡查准率与查全率；精排层支持Cross-Encoder / LLM Rerank / None三种模式可插拔切换，Rerank失败时自动Fallback至融合排名保障可用性
• 基于 **MCP(Model Context Protocol)** 标准实现知识检索Server，暴露 query_knowledge_hub / list_collections / get_document_summary 三个Tool，支持Claude Desktop等AI Agent通过Tool Calling直接调用私有知识库，返回结构化Citation引用
• 设计 **全链路可插拔架构**：为LLM / Embedding / Splitter / VectorStore / Reranker / Evaluator六大组件定义统一抽象接口，基于工厂模式 + YAML配置驱动实现"改配置不改代码"的组件切换；支持Azure OpenAI / OpenAI / DeepSeek / Ollama四种LLM Provider与Chroma / Qdrant向量数据库动态切换
• 构建 **会话级知识库隔离机制**：每个对话拥有独立Chroma Collection，RAG检索严格限定在当前会话文档范围；设计滑动窗口记忆压缩 + LTM长期记忆存储，基于LangGraph Checkpoint + MySQL双轨制自动压缩历史消息，支持跨会话知识沉淀与召回
• 实现 **五阶段智能数据摄取流水线**：Load → Split → Transform → Embed → Upsert；Transform阶段包含ChunkRefiner（LLM智能重组去噪）、MetadataEnricher（自动生成Title/Summary/Tags语义元数据）、ImageCaptioner（Vision LLM生成图片描述实现"搜文出图"）；基于SHA256文件哈希 + 内容哈希实现增量摄取与幂等Upsert
• 实现 **查询优化模块**：结构化LLM一次完成指代消解(Coreference Resolution) + 子查询拆分(Query Decomposition)，解决复杂多跳问题；Query Analysis Benchmark 5个测试cases（代词消解、并列拆分、无历史上下文等）全部通过
• 构建 **全链路可观测性体系**：设计Ingestion Trace（10阶段）+ Query Trace（5阶段）双链路追踪，基于Streamlit搭建六页面可视化管理平台（系统总览、数据浏览器、摄取管理、追踪分析、评估面板），支持精准定位坏Case
• 建立 **自动化评估与测试体系**：集成Ragas评估框架（Faithfulness / Answer Relevancy / Context Precision）+ 自定义指标（Hit Rate / MRR）；基于MS MARCO黄金测试集进行消融实验，对比Dense-only / Sparse-only / Hybrid / Hybrid+Rerank四种策略；累计编写60+测试用例覆盖单元/集成/E2E三层

**结果**：检索模块在MS MARCO数据集上Hit Rate@10达97%、MRR达95.75%，较纯BM25（Hit Rate 83%）提升14个百分点；Query Analysis 5/5 cases通过；Ingestion Pipeline Trace 10个stage完整性100%；系统支持4种LLM Provider与2种向量数据库无缝切换。

**技术栈**：Python, FastAPI, LangGraph, LangChain, Chroma, Qdrant, PostgreSQL, MySQL, SQLite, MCP, Vue 3, Streamlit, Ragas

---

### 项目二：智能深度研究Agent系统（LangResearch）| 个人项目

**背景**：针对通用大模型在深度研究任务中存在的信息检索片面、报告质量不稳定、用户意图理解偏差等问题，设计基于LangGraph的自主研究Agent。

**目标**：构建支持Multi-Agent协作的自主研究系统，实现需求澄清、任务分解、并行调研、报告生成与质量校验的完整闭环，提升研究报告的完整性与准确性。

**过程**：
• 设计 **Multi-Agent协作架构**：主图负责任务调度（Clarify → Plan → Router → Synthesize），嵌套子图(ResearchLoop)负责单任务ReAct推理循环（search → think → finalize）；通过LangGraph的Send机制实现并行Fan-out，最多3个Research Sub-Agent并发执行，最多2轮委托迭代
• 实现 **HITL人机协同机制**：设计置信度阈值策略，当调度Agent对用户query意图置信度 < 0.65时自动触发interrupt()中断，引导用户提供澄清信息（时间范围、关注维度、输出格式），最多2轮交互后恢复执行进入正式研究阶段
• 实现 **Evaluator-Optimizer报告质量优化模式**：报告生成采用Draft → Evaluate → Rewrite闭环，最多2轮自动迭代；Evaluate从完整性、准确性、引用规范性、逻辑连贯性四个维度评估，根据反馈自动重写；Verify节点规则化校验token覆盖率、引用来源、inline citation完整性
• 设计 **预算控制与系统容错机制**：每线程硬限制最多2次Tavily搜索调用，防止资源滥用；当LLM输出格式异常或超时时自动降级至规则-based处理，确保系统不崩溃；单个子任务失败不影响整体流程，具备错误隔离与状态恢复能力
• 实现 **并发安全的状态管理**：使用Annotated[list[Finding], operator.add]自定义reducer实现并行研究分支的安全合并；Finding结构体标准化(subtask / summary / sources / reflections)确保多源信息可综合；基于LangGraph持久化机制支持断点续传与状态回溯

**结果**：系统覆盖8阶段完整研究闭环（Clarify→Plan→Save→Router→Research→Synthesize→Report→Verify）；支持最多3个Sub-Agent并发执行与2轮委托迭代；HITL机制最多2轮交互澄清用户意图；Evaluator-Optimizer模式最多2轮自动优化报告质量，显著提升输出稳定性与准确性。

**技术栈**：Python, LangGraph, LangChain, Tavily Search, OpenAI API

---

## 两个项目的关系

| 维度 | RAG-Pro（项目一） | LangResearch（项目二） |
|------|------------------|----------------------|
| **核心能力** | 强检索 + 工程完整 | 强架构 + Agent设计 |
| **Agent形态** | LangGraph状态机编排RAG Pipeline | **真正的Multi-Agent系统**（调度+研究+报告+校验） |
| **检索深度** | Hybrid Search + Rerank，Hit Rate 97% | 仅Tavily单源，检索能力弱 |
| **前端** | Vue 3完整聊天界面 + Streamlit Dashboard | CLI/API调用 |
| **互补价值** | 可为LangResearch提供**多源检索+知识库**能力 | 可为RAG-Pro提供**主动研究+报告生成**能力 |

---

## 教育背景

| 时间 | 学校 | 专业 | 学历 |
|------|------|------|------|
| 20XX.09 - 20XX.06 | [学校名称] | [计算机科学与技术 / 软件工程 / 人工智能] | [本科/硕士] |

---

## 面试追问预测

1. **Hybrid Search的RRF融合**：BM25和Dense检索在RRF中的k参数选多少？为什么不用线性加权？Rerank失败后Fallback到融合排名的策略是什么？
2. **MCP协议设计**：为什么选择stdio-based而不是HTTP-based？Tool的Schema怎么定义？Claude Desktop如何发现你的Tool？
3. **可插拔架构的边界**：新增一个LLM Provider需要实现哪几个方法？工厂模式在运行时切换和启动时切换的区别？
4. **记忆管理机制**：滑动窗口压缩的触发条件是什么？LTM长期记忆怎么存储和召回？Checkpoint用PostgreSQL还是SQLite？
5. **Ingestion Pipeline幂等性**：chunk_id = hash(source_path + section_path + content_hash)的具体设计动机？增量摄取怎么判断文件是否已处理？
6. **Multi-Agent状态传递**：主图和嵌套子图之间Send的时候，ResearchState和ResearchLoopState怎么映射？并行分支合并时operator.add怎么避免重复？
7. **Evaluator-Optimizer的评估维度**：4个维度各自的权重？Rewrite后质量没有提升怎么办？怎么防止无限循环？
8. **HITL中断恢复**：LangGraph的interrupt/resume在代码层怎么实现？如果用户长时间不响应，thread状态会过期吗？
