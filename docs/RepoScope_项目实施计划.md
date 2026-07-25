# RepoScope — Autonomous Codebase Research Agent
## 项目实施计划文档

> 版本：v1.0　最后更新：2026-07　适用范围：个人简历项目 / 面试可讲清楚的完整系统

---

## 一、项目目标与定位

**一句话定位**：一个能自主理解任意 GitHub 仓库、生成结构化架构洞察，并可作为工具被其他
Agent（Claude Code / Cursor 等）通过 MCP 协议调用的 AI-Native Repository Intelligence
System。

**要解决的核心问题**（这是整个项目的立论基础，简历和面试都围绕它展开）：

| 传统代码 RAG 的问题 | RepoScope 的应对 |
|---|---|
| 缺乏代码结构理解，把代码当纯文本切分 | AST 边界感知分块 + 代码知识图谱 |
| 多步推理能力弱，单次检索答不出架构级问题 | LangGraph 多阶段 Workflow，支持迭代检索 |
| 上下文冗余严重，大仓库直接爆 Token | Context Engineering：优先级裁剪 + Token Budget |
| 分析结果不可解释、可能幻觉 | Reviewer 节点做 citation grounding 校验 |
| 只能自己用，不能被别的工具复用 | MCP Server 封装，暴露给外部 Agent |

**验收标准（做完项目后，你应该能回答这些问题）**：
1. 能否用一句话说清楚系统为什么比"仓库丢进 ChatGPT 上下文里问"更好？
2. Hybrid Retrieval 相比纯向量检索，量化提升了多少？（需要真实数字）
3. Reviewer 节点具体拦截了什么类型的错误？有没有失败案例可以讲？
4. 仓库更新后，增量索引的耗时相比全量索引降低了多少？
5. MCP Server 被 Claude Code 调用时，实际返回结构是什么样的？

---

## 二、系统架构总览

```
                         ┌─────────────────────────┐
                         │   FastAPI (REST + SSE)   │
                         │   MCP Server Endpoint    │
                         └────────────┬─────────────┘
                                      │
                         ┌────────────▼─────────────┐
                         │      LangGraph Runtime     │
                         │                            │
   route ──▶ repo_parse ──▶ retrieve ──▶ analyze ──▶ review ──▶ finalize
    │            │              │            │           │
    │            │              │            │           └─ 未通过 → 回退 retrieve/analyze
    ▼            ▼              ▼            ▼
 意图识别    Ingestion      Hybrid RAG    Planner+子任务   Grounding 校验
 任务路由    Pipeline       + Graph 查询   分解与执行
```

### 2.1 模块划分

| 模块 | 职责 | 关键技术 |
|---|---|---|
| **Repo Ingestion** | 拉取仓库、增量识别变更文件 | GitPython, 文件内容 hash / Merkle 思路 |
| **Code Intelligence Pipeline** | AST 解析、结构化分块、依赖图构建 | tree-sitter, 自建 import/call graph |
| **Repository-aware RAG** | 混合检索、rerank、图谱多跳查询 | Qdrant (vector), BM25, Cross-encoder rerank |
| **Agent Workflow (LangGraph)** | 任务路由、规划、分析、校验 | LangGraph, 状态机 checkpoint |
| **Context Engine** | 动态上下文裁剪与装配 | 优先级评分 + Token Budget 算法 |
| **Observability & Audit** | 事件流、审计记录 | SSE, PostgreSQL, Redis |
| **MCP Server** | 对外暴露工具能力 | MCP Python SDK |
| **Evaluation Harness** | 离线评估检索/生成质量 | 自建测试集 + 人工标注脚本 |

---

## 三、开发阶段与里程碑

### Phase 0：基础设施搭建（预计 3-5 天）
- 初始化项目结构（见附录 A 目录结构）
- 搭建 Qdrant / PostgreSQL / Redis 本地 docker-compose 环境
- FastAPI 骨架 + 健康检查接口
- 确定评估用的 5-8 个测试仓库（建议覆盖：一个 Spring Boot 微服务项目、一个 Python
  Web 项目、一个中等规模的开源库如 requests 或 httpx、一个前端仓库）

**产出物**：可跑通的空框架 + docker-compose.yml + README

---

### Phase 1：Repo Ingestion & Code Intelligence Pipeline（预计 1-1.5 周）
- GitPython 拉取仓库到本地工作区
- 集成 tree-sitter，实现按语言（先支持 Python + JavaScript/TypeScript + Java 三种）
  解析出函数/类边界
- **关键设计点**：分块策略必须是"按 AST 节点边界切"，不是"解析完AST再按字符数切"——
  两者效果差异很大，面试会问到，务必亲手实现并理解区别
- 构建依赖图：import 关系 + 函数调用关系（可先用简单的静态分析，不必上重量级工具）
- 实现增量索引：对每个文件计算 content hash，存入 PostgreSQL，下次拉取仓库时对比
  hash，只重新处理变更文件

**产出物**：
- 输入一个 repo URL，输出：文件列表 + AST chunk 列表 + 依赖图（可先用 JSON 表示，
  不必上图数据库）
- 一份小规模的性能对比：全量索引 vs 增量索引耗时（哪怕只在自己的测试仓库上测）

**自测标准**：随便挑一个测试仓库的函数，能否通过依赖图查出"谁调用了它"

---

### Phase 2：Repository-aware RAG 检索层（预计 1-1.5 周）
- Chunk embedding 写入 Qdrant
- 实现 BM25（可用 rank_bm25 或 Elasticsearch 简化替代）
- 实现 Hybrid Retrieval：向量检索 + BM25 检索结果融合（RRF 或加权融合）
- 接入一个轻量 rerank 模型（如 bge-reranker 或 cohere rerank API）
- 实现"图谱多跳检索"：给定一个检索命中的函数，能否沿依赖图扩展一跳，把调用方/被调用方
  也纳入上下文候选
- 实现 citation alignment：每条检索结果都能追溯到 `文件路径:起始行-结束行`

**产出物**：一个可以单独调用的检索服务，输入自然语言问题，输出带精确文件行号引用的
代码片段列表

**自测标准**：手写 10-15 个"仓库相关问题+人工标注的正确答案文件"，检查检索命中率

---

### Phase 3：LangGraph Agent Workflow（预计 1.5-2 周，核心阶段）
- 搭建 route → repo_parse → retrieve → analyze → review → finalize 状态图
- **route 节点**：判断用户意图（仓库摘要 / 面试分析 / 重构建议），路由到不同子流程
- **analyze 节点**：内部可以再分 Planner（拆解分析步骤）+ 执行子任务
- **review 节点**（这是简历里被面试官问得最多的部分，务必做扎实）：
  - 检查 analyze 输出中引用的每个 citation 是否真实存在于检索结果中（防止编造文件/行号）
  - 检查摘要性结论是否有检索证据支撑（可以用简单的规则：结论句子里提到的函数名/类名，
    是否出现在检索到的 chunk 里）
  - 如果校验不通过，触发一次重试（回退到 retrieve 补充检索）或标注"低置信度"降级返回
- 加入超时控制、Token 预算和失败降级逻辑（比如某个工具调用超时，直接跳过并在结果里
  标注"该部分分析因超时未完成"，而不是让整个流程挂掉）

**产出物**：完整可跑的 workflow，能针对一个仓库跑出 Repository Summary

**自测标准**：故意在测试问题里放一个"仓库里不存在的函数名"，看 Reviewer 能否拦截住
虚构引用

---

### Phase 4：Context Engineering（预计 3-5 天）
- 实现文件优先级评分：结合"是否是入口文件"（如 main.py / Application.java）、
  "被依赖次数"、"与当前问题的检索相关度"综合打分
- 实现 Token Budget 分配：给不同类型内容（代码片段 / 摘要 / 依赖图节点）分配预算上限，
  超出时按优先级裁剪
- 在大仓库（比如 5 万行以上）上做一次实测，记录裁剪前后的 Token 数对比

**产出物**：一份简单的实验记录（裁剪前 Token 数 / 裁剪后 Token 数 / 分析质量是否下降）

---

### Phase 5：Observability + MCP Server（预计 1 周）
- FastAPI SSE 接口，实时推送 parse / retrieve / analyze / review 各阶段事件
- PostgreSQL 记录 repo / chunk / agent_runs 表，用于审计和复现某次分析过程
- Redis 缓存正在进行中的 workflow 中间状态
- **MCP Server**：暴露 2-3 个工具，比如 `get_repo_summary`、`query_dependencies`、
  `suggest_refactor`，让 Claude Code / Cursor 可以直接把 RepoScope 当工具调用

**产出物**：录一段 demo（哪怕是 GIF），展示在 Claude Code 里调用你的 MCP 工具分析仓库

---

### Phase 6：评估与量化（预计 3-5 天，不要跳过这一步）
- 构建 15-25 个仓库的评估问题集（每个仓库 3-5 个问题，人工标注正确答案文件/行号）
- 对比三组检索效果：纯向量检索 / 纯 BM25 / Hybrid Retrieval，记录 Recall@5、
  Precision@5
- 人工评估 20 条摘要类回答的"citation 准确率"（引用的文件/行号是否真实存在且相关）
- 记录关键性能指标：P50/P95 分析耗时、平均 Token 消耗

**产出物**：一份评估报告（表格+简单结论），这是简历里所有量化数字的来源，**没有这一步，
前面所有的量化描述都是编的**

---

## 四、风险与应对

| 风险 | 应对方案 |
|---|---|
| tree-sitter 多语言支持工作量大 | 先只做 Python + JS/TS + Java 三种，够用于demo和面试 |
| 知识图谱构建工作量超预期 | 先用简单的 dict/JSON 表示依赖关系，不必上 Neo4j 等图数据库 |
| LangGraph 状态管理复杂度高 | 先用官方文档的 minimal example 跑通，再逐步加节点 |
| 评估集人工标注耗时 | 控制在 15-20 个问题即可，重点是"有真实数据"而不是规模 |
| MCP Server 联调环境问题 | 可先在本地用 MCP Inspector 工具测试，不必等 Claude Code 联调 |

---

## 五、附录 A：建议目录结构

```
reposcope/
├── app/
│   ├── api/                # FastAPI 路由 + SSE
│   ├── mcp/                 # MCP Server 封装
│   ├── ingestion/            # Git拉取 + 增量索引
│   ├── parsing/               # tree-sitter AST 解析、分块
│   ├── graph/                   # 依赖图构建与查询
│   ├── retrieval/                 # Hybrid Retrieval + Rerank
│   ├── workflow/                    # LangGraph 节点与状态图定义
│   │   ├── nodes/
│   │   │   ├── route.py
│   │   │   ├── repo_parse.py
│   │   │   ├── retrieve.py
│   │   │   ├── analyze.py
│   │   │   ├── review.py
│   │   │   └── finalize.py
│   │   └── graph.py
│   ├── context_engine/                # 上下文裁剪与装配
│   └── models/                          # Pydantic schema
├── eval/
│   ├── test_repos.yaml
│   ├── qa_dataset.jsonl
│   └── run_eval.py
├── docker-compose.yml
├── requirements.txt
└── README.md
```

## 六、附录 B：技术选型速查

| 组件 | 选型 | 备注 |
|---|---|---|
| Agent 编排 | LangGraph | 状态图 + checkpoint |
| AST 解析 | tree-sitter | 多语言支持，增量解析 |
| 向量库 | Qdrant | 本地 docker 部署即可 |
| 稀疏检索 | rank_bm25 / Elasticsearch | 小规模用 rank_bm25 足够 |
| Rerank | bge-reranker-base（本地）或 Cohere Rerank API | 视是否要接外部API而定 |
| Web 框架 | FastAPI | REST + SSE |
| 持久化 | PostgreSQL | repo/chunk/agent_runs 三张核心表 |
| 缓存 | Redis | workflow 中间状态 |
| 对外协议 | MCP (Model Context Protocol) | Python SDK |

---

**给自己的提醒**：这份计划的核心目的不是"把所有技术名词都实现一遍"，而是每一个模块都要
能在面试时讲清楚"为什么这么设计、遇到了什么问题、怎么解决的"。宁可少做一两个模块，也要
保证做出来的部分是真实可讲的。
