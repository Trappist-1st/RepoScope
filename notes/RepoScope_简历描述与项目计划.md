# RepoScope · 简历描述（修改版）与项目计划文档

---

## 一、修改后的简历描述

> **使用说明**：文中标记为 `[数据待补充：xxx]` 的地方需要你实际跑测试后填入真实数字，不要编造——面试时追问细节会直接露馅。建议按"三、评估方法论"里的方法跑一个小规模测试集，拿到真实数据后再定稿。

```markdown
RepoScope · Autonomous Codebase Research Agent · Python · 个人项目

面向开发者「快速理解复杂 GitHub 仓库并生成架构洞察」的场景，构建 AI Native
Repository Intelligence System。项目针对传统代码 RAG 存在的「缺乏代码结构
理解、多步推理能力弱、上下文冗余严重、分析结果不可解释」等问题，设计
Autonomous Workflow + Repository-aware RAG + Code Knowledge Graph，实现从
仓库解析、代码检索到架构分析与面试洞察生成的完整 Agentic Research Pipeline。

- 基于 LangGraph 构建多阶段 Agent Workflow（route → repo_parse → retrieve →
  analyze → review → finalize）：Router 负责任务分流，Planner 拆解分析步骤，
  Reviewer 节点对生成结果做双重校验——① 引用一致性校验（analyze 阶段引用的
  代码片段是否真实存在于检索结果中，防止路径级幻觉）② 结论完整性校验（是否
  覆盖了 Planner 拆解出的所有子问题）——未通过校验触发有限次重试或降级为
  "低置信度"标记，而非静默输出错误结论。

- 实现 Repo Ingestion & Code Intelligence Pipeline：基于 GitPython 自动拉取
  仓库，结合 tree-sitter 做 AST 结构化解析（按函数/类边界切分代码块，避免
  固定长度切分导致的语义割裂），在 import 依赖图基础上构建函数调用图与类
  继承关系，形成轻量级代码知识图谱（NetworkX 存储），支持"谁调用了这个函数"
  "这个接口影响哪些模块"类的结构化查询，而不仅依赖向量相似度。

- 构建 Repository-aware RAG：代码 Chunk Embedding 入库 Qdrant，采用 Hybrid
  Retrieval（BM25 + Vector）+ Rerank 策略，结合 file-aware retrieval 与
  路径级 citation alignment，实现代码级精准检索与引用追踪；封装 Git / File
  Search / Dependency Analyzer / Graph Query 等 Tool，支持统一超时控制、
  预算限制与错误降级。

- 实现基于 Merkle Tree 的增量索引机制：仓库更新后通过文件哈希树对比定位
  变更文件，仅对变更部分重新解析与 re-embedding，避免大仓库场景下的全量
  重建开销，[数据待补充：增量索引相比全量重建的耗时降低比例]。

- 实现面向 Agent Runtime 的 Context Engineering：基于文件优先级、模块关联度
  与 Token Budget 进行动态上下文裁剪与上下文装配，缓解大仓库分析中的
  Context Explosion 问题，[数据待补充：在 X 万行代码仓库上，平均单次分析
  Token 消耗从 Y 降低到 Z]。

- 将核心检索与分析能力封装为 MCP Server，可作为 Skill 被 Claude Code /
  Cursor 等 Agentic IDE 直接调用，实现"仓库理解能力"的跨工具复用，而不仅是
  一个孤立的 Web 应用。

- 基于 FastAPI 提供 RESTful + SSE 流式接口，实时推送 parse / tool / analyze
  / review 等 Agent Event；PostgreSQL 持久化 repo / chunk / agent_runs 审计
  数据，Redis 缓存分析状态与 Workflow 中间结果。

- 构建了包含 [数据待补充：N] 个仓库（覆盖 Python / Java Spring Boot /
  TypeScript 微服务等）的评估集，人工标注 citation 准确率与关键结论覆盖率，
  验证 Hybrid Retrieval 相比纯向量检索的召回提升 [数据待补充：X%]，端到端
  分析平均耗时 [数据待补充：X 秒]，P95 [数据待补充：X 秒]。

- Interview Workflow 基于结构化分析结果自动生成系统设计追问、架构风险点、
  可扩展性分析与重构建议，支持对 Spring Boot、微服务、Agent 系统等复杂
  仓库进行结构化分析。
```

### 相比原版的核心改动一览

| 改动点 | 原版 | 修改版 |
|---|---|---|
| Reviewer 节点 | 只提了名字 | 明确写出两项具体校验逻辑（引用一致性 + 完整性），并说明失败后的处理策略 |
| 代码结构理解 | import dependency graph | 升级为函数调用图 + 类继承关系的轻量知识图谱，对齐 2026 年 Repository Intelligence 的主流方向 |
| Chunking 策略 | 只说"AST 解析" | 明确"按函数/类边界切分"，这是 cAST 论文验证过的、比固定长度切分更优的做法 |
| 索引更新 | 未提及 | 新增 Merkle Tree 增量索引，体现生产级思维 |
| 工具互操作性 | 只有 FastAPI 接口 | 新增 MCP Server 封装，对齐 2026 年"能力可被其他 Agent 复用"的评判标准 |
| 量化数据 | 完全没有 | 每个关键模块后都留出数据位，倒逼你真的跑一遍评估 |
| 评估方法论 | 无 | 新增一条独立 bullet，说明有测试集、有人工标注、有对比实验 |

---

## 二、项目最终成品计划文档

### 1. 项目愿景

RepoScope 是一个"仓库级代码理解 Agent"：输入一个 GitHub 仓库地址，输出结构化的架构洞察、设计追问和重构建议，同时把自己的检索与分析能力开放为 MCP 工具，供其他 Agentic IDE（Claude Code / Cursor）复用。

核心主张：**代码理解不能只靠向量相似度，必须结合结构（AST / 调用图）+ 语义（Embedding）+ 验证（Reviewer 校验），才能产出可信、可追溯的分析结论。**

### 2. 系统架构总览

```
                        ┌─────────────────────────┐
                        │        FastAPI Layer     │
                        │  REST + SSE Event Stream │
                        └────────────┬─────────────┘
                                     │
                        ┌────────────▼─────────────┐
                        │      LangGraph Workflow    │
                        │ route → repo_parse →       │
                        │ retrieve → analyze →       │
                        │ review → finalize          │
                        └──┬───────┬────────┬───────┘
                           │       │        │
           ┌───────────────▼┐ ┌────▼─────┐ ┌▼──────────────┐
           │ Repo Ingestion  │ │ Retrieval│ │ Tool Layer      │
           │ - GitPython 拉取 │ │ - Hybrid │ │ - Git Tool      │
           │ - tree-sitter   │ │  (BM25+  │ │ - File Search   │
           │   AST 解析       │ │  Vector) │ │ - Dependency    │
           │ - 调用图/继承图   │ │ - Rerank │ │   Analyzer      │
           │   构建 (NetworkX)│ │ - Citation│ │ - Graph Query   │
           │ - Merkle Tree   │ │  Alignment│ │ - 超时/预算控制  │
           │   增量索引       │ │          │ │ - 错误降级       │
           └───────┬─────────┘ └────┬─────┘ └────────┬────────┘
                   │                │                 │
           ┌───────▼────────────────▼─────────────────▼──────┐
           │          Storage Layer                            │
           │  Qdrant(向量) / NetworkX or Neo4j(代码图) /        │
           │  PostgreSQL(审计: repo/chunk/agent_runs) /         │
           │  Redis(状态缓存/中间结果)                           │
           └────────────────────────────────────────────────┘
                                     │
                        ┌────────────▼─────────────┐
                        │       MCP Server Layer     │
                        │  暴露 repo_search /         │
                        │  code_graph_query /         │
                        │  architecture_summary       │
                        │  供 Claude Code/Cursor 调用   │
                        └───────────────────────────┘
```

### 3. 核心模块详细设计

#### 3.1 Repo Ingestion & Code Intelligence Pipeline

- **拉取**：GitPython clone/pull，记录 commit hash 作为版本基线
- **语言识别**：基于文件扩展名 + linguist 规则识别主语言分布
- **AST 解析**：tree-sitter 按语言解析，提取函数/类/接口定义边界
- **结构化切分**：Chunk 边界对齐到函数/类定义（而非固定字符数），跨文件的
  长类可按方法级切分，超长函数保留完整体 + 摘要双份索引
- **代码知识图谱**：
  - 节点：文件 / 类 / 函数 / 接口
  - 边：import 依赖、函数调用、类继承/实现、模块间引用
  - 存储：初期用 NetworkX 内存图 + 落盘 pickle/JSON，仓库规模增长后可平滑
    迁移到 Neo4j
- **增量索引（Merkle Tree）**：
  - 为每个文件计算 hash，按目录结构组织成 Merkle 树
  - 仓库更新时对比根 hash，逐层下钻定位变更的叶子文件
  - 仅对变更文件重新解析 AST、更新图谱、重新 embedding
  - 未变更部分的 chunk 与图节点直接复用

#### 3.2 Repository-aware RAG

- **向量库**：Qdrant，按 repo_id + commit_hash 分 collection，支持版本隔离
- **检索策略**：
  - BM25（关键词/标识符精确匹配，代码场景下变量名、类名的字面匹配很重要）
  - Vector（语义相似度，处理"这段代码的作用是什么"类模糊查询）
  - 两路召回后用轻量 Cross-Encoder Rerank 融合排序
- **Citation Alignment**：检索结果保留 `文件路径 + 起止行号`，分析阶段生成
  的每条结论必须能映射回具体的 citation，供 Reviewer 校验和最终报告展示

#### 3.3 LangGraph Agent Workflow

| 节点 | 职责 | 关键设计 |
|---|---|---|
| route | 判断任务类型（架构总结/面试洞察/重构建议） | 基于用户输入意图分类，决定后续走哪条子图 |
| repo_parse | 触发 Ingestion Pipeline，等待解析完成 | 大仓库场景下异步执行，通过 SSE 推送进度 |
| retrieve | 执行 Hybrid Retrieval + 知识图谱查询 | 根据 route 结果动态调整检索权重（如"重构建议"任务更依赖调用图） |
| analyze | LLM 基于检索结果生成结构化分析 | 输出必须带 citation，禁止无引用的断言 |
| review | 校验 analyze 输出 | ① 逐条 citation 回查是否存在于检索结果中<br>② 检查是否覆盖 Planner 拆解的子问题<br>③ 失败触发重试（上限 N 次）或标记低置信度 |
| finalize | 组装最终报告 | Markdown/JSON 双格式输出，写入 PostgreSQL 审计表 |

#### 3.4 Context Engineering

- **优先级打分**：文件的重要性 = 被调用次数（图中入度）× 与查询的语义相关度
- **Token Budget 分配**：按模块动态分配，核心模块保留完整代码，边缘模块只
  保留签名 + docstring
- **动态裁剪**：单次分析请求前先估算 token 消耗，超预算时按优先级从低到高
  裁剪，而非简单截断

#### 3.5 MCP Server 层

- 暴露三个核心 Tool：
  - `repo_search(query, repo_id)` → Hybrid Retrieval 结果
  - `code_graph_query(entity, relation)` → 调用图/继承图查询
  - `architecture_summary(repo_id)` → 缓存的结构化摘要
- 目标：让 Claude Code / Cursor 在处理某个仓库任务时，能直接调用 RepoScope
  已经建好的索引和图谱，而不用自己重新解析一遍

#### 3.6 可观测性与审计

- SSE 推送 Agent Event：`parse_started` / `tool_called` / `analyze_done` /
  `review_failed` / `finalize`
- PostgreSQL 表设计：
  - `repos`：repo 元信息、commit hash、索引状态
  - `chunks`：chunk 内容、embedding 引用、来源文件路径行号
  - `agent_runs`：每次 workflow 执行的完整 trace（节点耗时、token 消耗、
    review 结果、最终输出）
- Redis：缓存正在执行的 workflow 中间状态，支持断点恢复

### 4. 评估方法论

1. **构建测试集**：选 15-20 个仓库，覆盖 Python / Java Spring Boot /
   TypeScript 微服务，规模从小型（<5k 行）到中大型（>5 万行）
2. **人工标注**：针对每个仓库准备 3-5 个"已知答案"的架构问题（如"哪个模块
   负责鉴权""XX 接口的调用链是什么"）
3. **对比实验**：
   - Baseline A：纯向量检索
   - Baseline B：BM25 + Vector（无 Rerank）
   - RepoScope：Hybrid + Rerank + 知识图谱
4. **指标**：
   - Citation 准确率（引用的代码片段是否真实存在且相关）
   - 关键结论覆盖率（是否命中人工标注的关键点）
   - 端到端延迟（P50/P95）
   - 平均 Token 消耗
   - 增量索引 vs 全量重建的耗时对比

### 5. 开发里程碑（建议 6-8 周，可按实际进度调整）

| 阶段 | 周期 | 交付物 |
|---|---|---|
| M1：基础设施 | 第 1 周 | GitPython 拉取 + tree-sitter 解析 + 基础 chunk 切分跑通 |
| M2：检索层 | 第 2 周 | Qdrant 入库 + Hybrid Retrieval + Rerank 跑通，能对单仓库做基础问答 |
| M3：知识图谱 | 第 3 周 | NetworkX 调用图/继承图构建，支持结构化查询 |
| M4：Agent Workflow | 第 4-5 周 | LangGraph 六节点全部跑通，Reviewer 校验逻辑生效 |
| M5：工程化 | 第 5-6 周 | FastAPI + SSE + PostgreSQL 审计 + Redis 缓存 + Merkle 增量索引 |
| M6：MCP 封装 | 第 6-7 周 | MCP Server 暴露三个核心工具，可被 Claude Code 调用验证 |
| M7：评估与打磨 | 第 7-8 周 | 测试集构建、跑评估、补全简历中的量化数据 |

### 6. 技术栈清单

- **编排**：LangGraph
- **解析**：GitPython、tree-sitter
- **图谱**：NetworkX（可选升级 Neo4j）
- **检索**：Qdrant、BM25（rank_bm25 或 Elasticsearch）、Cross-Encoder Rerank
- **服务层**：FastAPI、SSE
- **存储**：PostgreSQL、Redis
- **互操作**：MCP（Model Context Protocol）Server

### 7. 主要风险与应对

| 风险 | 应对 |
|---|---|
| 大仓库解析耗时过长 | 异步任务队列 + 分批解析 + SSE 进度反馈 |
| 知识图谱构建复杂度失控 | 先做单语言（Python/Java）跑通，再扩展多语言 |
| LLM 幻觉导致 Review 误判 | Review 阶段用规则校验（citation 是否存在）而非纯 LLM 判断，降低成本和不确定性 |
| MCP 封装范围蔓延 | 严格限定 v1 只暴露 3 个工具，避免过度设计 |
