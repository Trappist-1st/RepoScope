# RepoScope 项目 —— AI 编码工具生成 Prompt

> 使用方式：建议分阶段喂给 Claude Code / Cursor 等编码 Agent，而不是一次性丢一个巨大
> prompt 让它生成整个项目——那样出来的代码你会看不懂，之后没法在面试里讲清楚。
> 下面拆成「总体说明」+「六个阶段的分段 prompt」，按顺序逐个喂给 AI，每完成一个阶段
> 自己先读一遍代码、跑一遍测试，再进入下一阶段。

---

## 总体说明 Prompt（第一次对话时先发这个，建立上下文）

```
我要开发一个名为 RepoScope 的个人项目：一个能自主理解 GitHub 仓库、生成结构化架构
洞察的 AI Agent 系统，同时可以通过 MCP 协议被 Claude Code / Cursor 等外部 Agent 当
工具调用。

核心目标：解决传统代码 RAG 的四个问题——缺乏结构理解、多步推理弱、上下文冗余、结果
不可解释。

技术栈：
- Agent 编排：LangGraph
- AST 解析：tree-sitter（先支持 Python / JavaScript+TypeScript / Java 三种语言）
- 向量库：Qdrant（本地 docker）
- 稀疏检索：rank_bm25
- Rerank：bge-reranker-base 或类似轻量模型
- Web 框架：FastAPI（REST + SSE）
- 持久化：PostgreSQL（repo / chunk / agent_runs 三张表）
- 缓存：Redis
- 对外协议：MCP (Model Context Protocol) Python SDK

架构是一个 LangGraph 状态图：route → repo_parse → retrieve → analyze → review →
finalize，其中 review 节点负责校验 analyze 输出的 citation 是否真实存在于检索结果
中，防止幻觉；未通过校验则回退重试或降级返回。

我会分阶段跟你交流每个模块的实现，现在先不用写代码，请先帮我确认这个架构理解是否
合理，并给出你建议的项目目录结构。理解后等我发送第一个阶段的具体需求。
```

---

## 阶段 1 Prompt：Repo Ingestion & Code Intelligence Pipeline

```
现在实现第一个模块：Repo Ingestion & Code Intelligence Pipeline。

需求：
1. 用 GitPython 实现一个函数，输入 repo URL（或本地路径），拉取/更新到本地工作区
2. 对每个源码文件计算 content hash（用于后续增量索引判断文件是否变更），存入
   PostgreSQL 的 files 表（字段：repo_id, file_path, content_hash, last_indexed_at）
3. 集成 tree-sitter，为 Python / JavaScript+TypeScript / Java 三种语言分别配置
   parser，实现一个统一接口：输入文件内容，输出该文件的函数/类定义列表，每个定义
   包含：名称、起始行、结束行、所属类型（function/class/method）
4. 基于第3步的结果，实现「按AST节点边界分块」的 chunking 逻辑——每个 chunk 对应一个
   完整的函数或类定义，不要用固定字符数切分。对于无法解析的文件（比如配置文件），
   fallback 到简单的按行数切分
5. 实现一个简单的依赖图构建器：解析每个文件的 import 语句，以及函数调用关系（可以
   先只做同文件内 + 简单跨文件 import 匹配，不需要完整的静态类型分析），输出一个
   JSON 结构，表示 "文件A 依赖 文件B"、"函数X 调用 函数Y"
6. 实现增量索引逻辑：给定一个已经索引过的仓库，重新拉取后对比每个文件的 content
   hash，只有 hash 变化的文件才重新走 tree-sitter 解析 + chunking + 依赖图更新

请先给出这个模块的目录结构和核心类/函数签名设计，我确认后你再写具体实现。每写完
一部分，请附带一个可以直接跑的最小测试用例（用一个小型示例仓库或几个手写的示例
文件）。
```

---

## 阶段 2 Prompt：Repository-aware RAG 检索层

```
现在实现第二个模块：Repository-aware RAG。基于阶段1产出的 chunk 列表和依赖图。

需求：
1. 用 sentence-transformers 或类似模型生成 chunk 的 embedding，写入 Qdrant，
   metadata 中保留 file_path, start_line, end_line, chunk_type
2. 用 rank_bm25 对同一批 chunk 建立稀疏索引
3. 实现 Hybrid Retrieval：给定自然语言 query，分别做向量检索 top-k 和 BM25 检索
   top-k，用 RRF (Reciprocal Rank Fusion) 或加权分数融合两组结果
4. 接入一个 rerank 步骤，对融合后的候选结果重新排序，取 top-n
5. 实现「图谱扩展」：给定检索命中的一个 chunk（比如某个函数），根据依赖图查询它的
   调用者/被调用者，作为补充候选加入上下文（一跳扩展即可，不需要多跳）
6. 每条最终返回的检索结果，必须带上精确的 citation：`file_path:start_line-end_line`

请先给出检索服务的接口设计（输入输出的数据结构），我确认后再实现。完成后写一个
简单的评估脚本框架（先留空评估集，后面阶段6我会补充），用于后续计算 Recall@5 /
Precision@5。
```

---

## 阶段 3 Prompt：LangGraph Agent Workflow（核心阶段，重点关注 review 节点）

```
现在实现核心模块：LangGraph Agent Workflow。状态图节点：route → repo_parse →
retrieve → analyze → review → finalize。

需求：
1. 定义 workflow 的共享 State（包含：用户问题、当前仓库、检索结果、中间分析结果、
   review 结果、重试次数等字段）
2. route 节点：判断用户意图属于「仓库摘要」「面试分析」「重构建议」哪一类，路由到
   对应的 analyze 子逻辑
3. repo_parse 节点：调用阶段1模块，确保目标仓库已完成解析和索引（走增量索引逻辑）
4. retrieve 节点：调用阶段2的检索服务，获取与当前问题相关的代码上下文
5. analyze 节点：内部先用一个 Planner 步骤拆解本次分析需要几步完成，再逐步执行，
   生成结构化的分析结果（比如：架构总结 + 关键设计点 + 每条结论对应的 citation）
6. review 节点，这是最重要的部分，请重点实现：
   - 提取 analyze 输出中所有的 citation（file_path:line范围）
   - 校验每条 citation 是否确实存在于本次 retrieve 阶段返回的候选结果中（防止
     analyze 阶段幻觉出不存在的引用）
   - 校验分析结论中提到的函数名/类名，是否在 citation 对应的代码片段中真实出现
   - 如果校验失败：如果重试次数 < 2，回退到 retrieve 节点补充检索后重新 analyze；
     如果重试次数已达上限，在 finalize 阶段标注「该部分结论置信度较低」而不是
     直接报错
7. finalize 节点：整理最终输出格式（Markdown 结构化报告），包含每条结论的 citation
   链接
8. 加入超时控制：每个节点设置超时时间，超时则跳过该节点并在结果中标注「因超时未
   完成」
9. 加入 Token 预算控制：analyze 阶段传入上下文前，先做粗略的 token 计数，超过预算
   时优先保留高相关度片段（这部分先写一个简单版本，阶段4我会专门优化）

请先给出状态图的节点连接逻辑图（用文字描述条件边），我确认后再实现代码。review
节点请单独写清楚校验逻辑的伪代码，我要重点检查这部分设计是否合理。
```

---

## 阶段 4 Prompt：Context Engineering

```
现在优化 Context Engineering 模块，替换阶段3里 analyze 节点的简单 token 预算逻辑。

需求：
1. 实现文件/chunk 优先级评分函数，综合以下因素：
   - 是否是仓库入口文件（如 main.py, Application.java, index.ts）
   - 该 chunk 在依赖图中被引用的次数（被更多地方依赖的代码优先级更高）
   - 该 chunk 与当前问题的检索相关度分数（来自阶段2的 rerank 分数）
2. 实现 Token Budget 分配逻辑：给「核心代码片段」「依赖图摘要」「历史分析结论」等
   不同类型内容分配预算上限，当总量超出模型上下文窗口时，按优先级从低到高裁剪
3. 写一个实验脚本，在一个真实的大仓库（5万行以上）上跑一次，记录：
   - 裁剪前候选内容的总 token 数
   - 裁剪后实际传入模型的 token 数
   - 裁剪比例
   输出一份简单的实验记录（用于后续写入项目文档/简历量化数据）

请先给出优先级评分函数的具体公式和各因素权重设计，我确认合理后再实现。
```

---

## 阶段 5 Prompt：Observability + MCP Server

```
现在实现可观测性和 MCP Server 模块。

需求：
1. FastAPI 增加一个 SSE 接口 `/analyze/stream`，实时推送 workflow 各节点的执行
   事件（node_name, status, timestamp, 可选的中间结果摘要）
2. PostgreSQL 增加 agent_runs 表，记录每次完整分析的：run_id, repo_id, 用户问题,
   各节点耗时, 最终结果, review校验是否通过, 创建时间——用于事后审计和复现
3. Redis 缓存正在执行中的 workflow 中间状态（用于支持长时间运行任务的状态查询）
4. 用 MCP Python SDK 实现一个 MCP Server，暴露以下工具：
   - `get_repo_summary(repo_url)`：返回仓库架构摘要
   - `query_dependencies(repo_url, symbol_name)`：查询某个函数/类的依赖关系
   - `suggest_refactor(repo_url, file_path)`：对指定文件给出重构建议
   每个工具的返回结构要清晰，包含 citation 信息
5. 写一份简单的说明，介绍如何在 Claude Code 或 MCP Inspector 中配置并调用这个
   MCP Server

请先给出 MCP 工具的接口 schema 设计（每个工具的输入参数和返回结构），我确认后
再实现。
```

---

## 阶段 6 Prompt：评估与量化

```
现在构建评估体系，这一步不需要写很多代码，但是整个项目量化数据的来源，请认真做。

需求：
1. 帮我设计一个评估问题集的模板（YAML 或 JSONL 格式），每条记录包含：
   仓库地址、问题（自然语言）、期望命中的文件路径+行号范围（人工标注）、问题类型
   （摘要类/依赖查询类/重构建议类）
2. 写一个评估脚本，针对「纯向量检索」「纯BM25检索」「Hybrid Retrieval」三种配置，
   分别跑一遍评估问题集，计算 Recall@5 和 Precision@5，输出对比表格
3. 写一个简单的人工评估辅助脚本：随机抽取 N 条 analyze 阶段生成的分析结果，展示
   给我（人工标注者）看，我逐条判断 citation 是否准确、结论是否有依据，脚本记录
   打分结果并汇总成准确率
4. 输出一份最终的评估报告模板（Markdown），包含以上所有指标，我会拿真实跑出来的
   数字填进去

我会自己去准备 15-20 个测试仓库和问题，你只需要帮我搭好评估框架和脚本。
```

---

## 使用建议

1. **不要跳着用**：每个阶段的代码你都要读懂、能跑起来、能改动，否则简历上写了但
   面试答不上来，风险比不写还大。
2. **每阶段结束后，自己动手做点修改**：比如换一个 rerank 模型、调整一下优先级评分
   权重，这样你才有"我做过取舍"的真实经历可以讲，而不是纯粘贴 AI 生成的代码。
3. **阶段6千万别省略**：没有真实跑出来的评估数字，简历里所有"提升了X%"都是空话，
   面试官一问数据来源就露馅。
