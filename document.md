# document

## 1. 架构

### 1.1 后端

- `FastAPI` 提供 HTTP API、SSE、WebSocket。
- `SQLModel` 管理数据模型与 CRUD。
- `services/` 负责业务逻辑，`routers/` 只做路由转发。
- `SQLite` 为默认开发数据库，文件位于 `./data/essay_read.db`。

### 1.2 前端

- `React + TypeScript + Vite`
- `api/` 负责所有请求封装。
- `features/` 负责领域组件。
- `pages/` 负责路由级页面。

## 2. 数据流

### 2.1 论文主链路

1. 上传 PDF 到 `/api/papers/upload`
2. 后端落盘并创建 `Paper`
3. `auto_index` 提取全文与元数据
4. 前端在 `/reader/:id` 触发论文解析
5. `analysis_runner` 做流式解析、相关论文搜索、智能标签
6. 结果写入 `AnalysisResult`，解析状态写入 `Paper`

### 2.2 深度研究链路

1. 工作区聚合论文
2. `/api/research/workspaces/{ws_id}/runs` 创建研究任务
3. pipeline 依次执行 `collect -> expand -> chunk -> embed -> digest -> graph -> persist`；`expand` 内部并行执行在线多源召回与 PDF 引用解析，`digest` 可按配置跳过
4. `/api/research/workspaces/{ws_id}/graph` 返回图谱快照
5. `/api/research/workspaces/{ws_id}/query` 提供 RAG 问答；`mode=graph` 时叠加 PathRAG 风格图谱路径证据
6. `/api/research/runs/{run_id}/stream` 订阅进程内事件总线；15 秒无事件时发送 SSE 注释心跳；已完成任务回放 `graph_ready/done`；数据库残留但当前进程无事件总线的任务返回 `error` 事件

### 2.3 工作区洞察链路

1. `/api/workspaces/{workspace_id}/insights` 读取工作区论文与最新图谱
2. 统计阅读层级、来源、聚类、关键论文、桥梁论文
3. 前端工作区详情页展示阅读建议与图谱摘要

### 2.4 工作区智能规划链路

1. `/api/workspaces/{workspace_id}/reading-plan` 读取工作区论文画像与最新图谱
2. 将引用边转换为阅读前置关系，结合年份、解读层级、图谱重要性分阶段排序
3. `/api/workspaces/{workspace_id}/survey` 读取论文画像生成综述草稿、主题聚合与对比矩阵
4. 前端工作区详情页展示阅读路径、覆盖统计、综述片段和矩阵

### 2.5 Agent 工具链路

1. MCP 客户端通过 `python -m backend.mcp_server` 启动 stdio 服务
2. MCP `tools/list` 返回论文检索、证据检索、图谱读取、开发 brief 工具
3. 工具调用进入 `services/agent_tools.py`
4. `agent_tools` 只读取 `Paper`、`AnalysisResult`、`ResearchGraph`、向量索引和工作区关联
5. HTTP 客户端可通过 `/api/agent-tools/*` 使用同一组工具
6. `/api/agent-tools/workflow/langgraph-spec` 返回论文驱动开发工作流规范

### 2.6 Research Agent Harness 链路

1. 客户端通过 `/api/agent-tools/runs` 或 `python -m backend.research_agent run` 提交 `workflow/goal/workspace_id`
2. `parse_goal` 默认使用确定性计划；设置 `RESEARCH_AGENT_PLANNER_ENABLED=true` 后通过统一 LLM 配置执行一次有 token/超时上限的结构化 goal 规划，失败自动回退原始 goal
3. `services/research_harness.py` 用 LangGraph 编排固定 workflow，并通过 `services/agent_tools.py` 调用论文检索、证据检索、阅读计划和综述工具
4. 每个节点记录结构化 journal；goal planner 与可选证据检索失败按 `soft_fail` 处理，其余领域错误终止 workflow
5. 成功运行返回 Markdown、goal plan、论文 ID、证据、阅读计划和综述等 artifacts；HTTP 失败按 `{detail, code}` 返回 4xx/5xx，CLI/core 保留完整 failed run
6. CLI 可将 Markdown 与 journal 写入文件

### 2.7 领域探索详情与追问

- `/explore` 创建探索并列出历史；创建成功或点击历史进入 `/explorer/:id`。
- 详情页从 `ExplorationRun` 恢复原始问题、检索式、全部题录/摘要、图谱、计量结果和综合报告，通过 SSE 接收尚未结束的初始探索。
- `ExplorationMessage` 独立保存每轮问题、回答、状态、选择的论文、补充检索资料、证据键和调用上下文。不会覆盖初始探索；删除探索前检查活动追问并清理关联消息。
- `services/exploration_chat.py` 为追问核心，路由只校验和启动后台任务。指定论文优先，其余由标准 BM25+、配置的预训练向量模型、等权 RRF 和重排服务选择；检索问句包含最新问题、上一轮问题和原始问题。前轮引用保留在上下文，不强制占满本轮资料预算。每轮最多 32 篇摘要、最近 12 轮已完成对话及原始报告片段。历史引用编号带上对应论文标题。所有历史完整落库，历史回答不作为文献事实证据；`context.retrieval` 保存实际模型、通道、回退警告。
- 勾选补充检索时结合原始问题、近期问题和最新追问拆解最多 3 条检索式，召回最多 60 篇；新增资料在模型回答前落库，生成失败也保留。单轮总超时 300 秒（包含检索与证据核验），同探索仅一个活动追问；重启将中断任务标记为失败。
- 客户端轮询活动追问状态；图表支持独立放大弹窗与 75%–250% 缩放。论文标题使用 DOI、arXiv、已知 HTTP(S) 地址或 Scopus 编号定位；正文中完整标题及唯一简称自动链接，不猜测不存在的下载地址。
- 导出上下文为 JSON，包含原始产物、对话和累计资料。

| 方法 | 路径 | 参数 | 返回 | 状态码 |
|---|---|---|---|---|
| GET | `/api/exploration/runs/{run_id}/messages` | `run_id` | 按 ID 排序的 `ExplorationMessageResponse[]` | 200/404/422 |
| POST | `/api/exploration/runs/{run_id}/messages` | `question` 非空、最多 4000 字符；`search_more=false`；`selected_keys=[]` 最多 12 个 | 已持久化的 pending 消息；后台继续生成 | 200/400/404/409/422 |

消息字段：`id/run_id/question/answer/status/search_more/selected_keys/papers/evidence_keys/context/error_message/created_at/updated_at`。状态为 `pending/running/done/failed`。`context` 记录原始问题、实际材料键、历史消息 ID、检索式、检索源、模型名、警告、本轮引用编号映射和 `evidence_audit`；不保存 API Key。常见错误码为 `LLM_NOT_CONFIGURED`、`CHAT_RUNNING`、`EXPLORATION_RUNNING`、`NOT_FOUND`。

### 2.8 论文库元数据链路

- `Paper.bibliography` JSON 保存 `subjects/publication_type/publisher/providers/field_sources/updated_at`；DOI、arXiv、OpenAlex 标识使用已有独立列，Scopus 标识保存在 `source_ids.scopus_id`。启动迁移自动添加 JSON 列。
- `services/paper_metadata.py` 负责解析、合并、展示与补全。研究方向优先手工修正，其次已识别的 arXiv 作者分类；其他来源采用 OpenAlex 主主题的子学科，常用方向转为中文，无主主题时用首个主题，最后回退原分类。方向不再与会议/期刊或检索源混用。
- Scopus/OpenAlex 召回候选的刊物、出版类型、出版社与方向经过融合、`PaperWork` 和注册流程落库；融合时保留正式发表载体及其年份。
- 已有论文通过标识符补全；本地 PDF 只从首页提取 DOI/arXiv ID，避免误用参考文献 DOI。查询 arXiv、配置好的 Scopus、OpenAlex、Crossref；标题回退需规范化后完全相同，不做模糊匹配写回。
- 手工修正优先于自动结果；正式会议/期刊的类型、年份和载体优先于预印本。空的自动结果不抹掉已有值。未匹配字段保留缺失状态，补全不调用 LLM、不下载 PDF。
- 补全任务每进程一个、并发 3、单篇超时 90 秒，重复请求返回当前任务状态。进度在内存，结果逐篇落库；服务重启后可重新发起，已保存的结果保留。
- `/library` 统一分页浏览上传论文和可选扩展论文；浏览、智能搜索共用方向/发表类型/载体/元数据来源/年份/日期筛选。先筛选再计数分页，facets 取当前来源范围的全库值。搜索仍按命中数排序，普通浏览可选择添加时间、发表年份或标题排序。

### 2.9 成熟算法执行链（2026-09-07）

| 模块 | 当前实现 | 参数与输出 |
|---|---|---|
| 论文关系 | `graphkit/relatedness.py`：八通道逐端排名、平均双端倒数排名的 RRF 适配；不再做分位截断或引文弱标签 AUC 调权 | `weighted_rrf_v1`，k=60；默认八通道各 0.125，全局归一；解释包含原始分、双端排名、贡献；融合分不是概率 |
| 图骨架 | mutual-kNN 主分析图；原始 Disparity Filter 作为独立标记 | kNN k=10（边界同分保留），alpha=0.05 仅控制标记；原目标度数/减基线停用；`stats.backbone.policy=mutual_knn_with_disparity_annotation_v1`、`edge.selection_method` 标明来源；补桥仅展示，不参与聚类/中心性 |
| 社区/重要性 | `igraph` + `leidenalg` 的 Leiden（RBConfiguration），加权 PageRank | resolution=1、seed=42；不强行合并小簇或按桥接边分配归属；importance 使用关系 PageRank，引文 PageRank 单独保留 |
| 探索聚类 | 配置的预训练 embedding 表示标题+摘要，mutual-kNN + Leiden | k=10，边界同分保留；向量失败时用 sklearn 标准 TF-IDF，并在 `metrics.clustering` 明示降级 |
| 检索/选材 | 多检索式等权 RRF；**双通道召回**：每条检索式 relevance 主路 + 前 3 词引号短语经典路（`order="cited"` 被引降序，跨式去重）；BM25+ 与 dense 排名 RRF，再调用配置的 reranker | relevance 排序天然偏新文献（时代流行词会把奠基论文挡在 AND 匹配外，实测见 `DEEP_RESEARCH_EVAL.md` 盲测节）；经典路是显式高被引补充层，不是逐论文流行度偏好；文献被引量不作为问题相关性；有限缓存按 provider/base_url/model/文本隔离；BGE v1.5 使用真实 tokenizer 的 512 token 窗口 |
| 引用率 | `citation_metrics.py`：两个相邻完整两年窗口的条件 Poisson 精确检验（SciPy binomtest），集合内 Benjamini-Hochberg 校正 | 排除当前/未来及发表当年；BH p<=0.05 且增长才标 rising；逐篇 `citation_metrics` 保存假设、窗口、p/q、比值与无法检验原因 |
| 被引百分位 | SciPy average rank 中点百分位，按本次样本发表年份分组 | 非全领域/ESI 百分位；徽章只表示样本前 5%，至少 20 篇且引用量正；不是显著性声明 |
| 证据核验 | `evidence_audit.py`：借鉴 ALCE 的论断/引用支持核验，使用现有 LLM 作裁判、程序核对原文摘录与编号 | 40 秒、最多 24 论断；状态 completed/partial/no_claims/skipped/error；不完整时 support_rate=null；不把 LLM 判定当人工真值 |

探索综合报告核验位于 `synthesis.stats.evidence_audit`，追问核验位于 `message.context.evidence_audit`。前端提供可展开的论断、支持/矛盾/不足及原文摘录；保留原回答，不因核验服务失败丢失内容。模型自报 confidence 仅兼容存储，不再显示为正确概率。支持率是被审查论断中模型判为材料支持的比例，不代表整份文献调查的准确率。

引用年度记录缺项默认为未知，不擅自补零。仅来源适配器明确提供 `counts_by_year_metadata={source,missing_years:'zero',start_year,end_year}` 时，在指定完整范围补零。Poisson 独立性、窗口内恒定率和 BH 依赖条件是方法假设，未用当前少量数据证明；引用具有过度离散或相关性时，名义错误率不保证成立。该实现称“引用率变化检验”，不是 Kleinberg burst。

配置读取时，仅把无方法版本且精确匹配旧出厂权重的配置迁为等权，把无骨架版本且为旧默认 0.25 的 alpha 迁为 0.05；显式自定义/已标版本的参数值保留。`relations.method`、召回/混合检索 method 为 `weighted_rrf_v1`，`research.backbone_method` 为 `mutual_knn_with_disparity_annotation_v1`；旧 calibrate 字段兼容返回 false。迁移在内存进行，不直接改写秘密配置文件。历史图谱和探索快照不自动覆盖，需要重新运行才产生新结果。

mutual-kNN 是标准相似图构造（von Luxburg, 2007, §2.2），k=10 是默认邻域规模，未声称经本项目数据优化。RRF 压缩权重差异，直接用 Disparity 筛选会出现少数边通过而多数节点孤立，因此主分析图始终使用互近邻，alpha 只控制独立标记。Disparity 保留包含度为 1 的约定，不能全部称为显著；这也不是多重检验后的语义正确性保证。

前端 `domainAggregates.ts` 的社区密度/中心性同样排除展示桥。子方向卡片只展示样本末三年论文占比，取消占比超过 45% 就判为“上升中”的规则；这与论文级引用率检验是不同统计量。

### 2.10 评测入口

- `backend.eval.explore_eval`：verdict 按名义类别 exact-match 计分，删除相邻档半对；返回分类报告和 macro 指标。DOI 可解析率单独保留，不解释为论断支持率。
- `backend.eval.survey_metrics`：读取 JSON，输出 Recall@K、nDCG、引文存在性、引文/联合证据支持；未判定相关性明确用 skip/zero/error 策略。可选本地预训练 NLI 预测单独输出，不能用预测冒充参考标签。参照 SurGE/ALCE 的指标思想，不宣称复现整套官方评测。
- 查看输入格式：`.venv/bin/python -m backend.eval.survey_metrics --example`；执行：`.venv/bin/python -m backend.eval.survey_metrics input.json --output report.json`。
- 真实材料链路检查：`.venv/bin/python -m backend.eval.algorithm_smoke --run-id 18 --limit 60 --audit --output data/eval/algorithm-smoke-2026-09-07.json`。仅读取已有探索，结果另存；使用配置中的模型，会调用 embedding/reranker/LLM 服务。
- 2026-09-07 验证：后端734项、前端全量117项通过；最后总览调整相关20项通过（含新增展示桥排除回归）；Ruff、类型编译、构建、CORS通过。60篇材料检索/聚类使用真实配置无降级；179篇工作区只读重建得到537条分析关系、7条展示桥、9个社区。上述验证不含独立标注集的检索准确率、聚类主题准确率或整篇综述可靠性结论。
- 真实数据连通性检查：`.venv/bin/python -m backend.eval.algorithm_smoke --run-id 18 --limit 60 --audit --output data/eval/algorithm-smoke-2026-09-07.json`。只读已有探索，不写业务数据；此脚本检验运行与降级，不输出准确率提升。

方法来源：RRF（Cormack 等，SIGIR 2009，doi:10.1145/1571941.1572114）；Disparity Filter（Serrano 等，PNAS 2009，doi:10.1073/pnas.0808904106）；Leiden（Traag 等，2019，doi:10.1038/s41598-019-41695-z）；条件 Poisson 检验（Przyborowski/Wilenski，1940，doi:10.1093/biomet/31.3-4.313）；BH（1995，doi:10.1111/j.2517-6161.1995.tb02031.x）。SurGE/ALCE 和候选比较见 `docs/ALGORITHM_REPLACEMENT_REVIEW_2026-09-07.md`。

### 2.11 统一文献研究与调查报告（2026-09-07）

- `/explore` 为统一入口：自然语言问题、深度、可选工作区母论文、联网补充开关；`/explorer/:id` 默认展示调查报告，分析结果/资料/追问为同一任务的其他视图。
- `/workspace/:id/research` 兼容跳转到 `/explore?workspace_id=:id`；原图谱与全文索引功能保留在 `/workspace/:id/graph`，原 API 不变。工作区集合仍用于资料管理。
- `ExplorationRun.options` JSON 保存 `workspace_id/workspace_name/seed_paper_ids/search_online`，创建时检查工作区成员并快照具体 ID；默认不选母论文。空选择、非成员、仅本地且无母论文拒绝；已删除母论文不会被其他论文自动替代。
- `services/research_sources.py` 是种子适配层，复用原 deep research 的 `collect_online_recall/fuse_candidates` 获取后向/前向/推荐候选，再与问题检索统一排序。关闭联网补充时不检索或富化外部文献，模型调用仍使用现有配置。
- 统一主流程：**数据源预检** → 问题拆解 → 可选问句/母论文召回 → 富化 → 计量/聚类 → 综合 → 调查表提取/聚合 → 持久化。外层仍是单向状态机；需要语义判断的拆解、综合、逐篇证据提取和最终报告在节点内部运行有界 ReAct 循环。模型只能输出结构化 action/finish 并调用节点白名单工具，程序 validator 通过后才允许进入下一阶段；每档有最大轮数、连续无进展与单轮超时，预算耗尽时标记 partial。确定性节点采用程序执行+验收的一轮循环，不交给模型自由推进。每个主要产物及时落库，报告异常不丢已有资料。旧结果可直接补生成报告，不重复检索。
- 数据源预检（2026-09-08）：调查开始前用**带权限的真实最小请求**探测每个源（Scopus Search count=1 / OpenAlex per-page=1，`deep_research/preflight.py`），不做 ping 可达性判断。状态分 `ok/auth_error(401·403)/rate_limited/network_error/unconfigured`；`GET /api/exploration/preflight` 随时查询（20s 短缓存），`/explore` 表单区实时显示体检徽章。运行内为 SSE `preflight` 阶段（stage 0）：主源判死（`rate_limited` 不判死——权限仍在）则 FailoverProvider 主源预熔断，省去逐通道 401 试错；全部在线源判死则快速失败并给出修复指引，不再空跑拆解与检索；体检快照随 `result.sources.preflight` 落盘，检索透明度区对失效源展示回退说明。
- 盲测排除（2026-09-08）：创建运行可传 `exclude_dois`/`exclude_arxiv_ids`（各 ≤50，规范化后随 `options.exclusions` 快照落盘）；`explorer.apply_exclusions` 在召回与引文扩展合并后按归一化 DOI/arXiv ID（版本号不参与）过滤，语料、计量、综合、报告全文下载均不接触被排除文献；实际排除计数与标题记入 `result.sources.excluded` 保持检索透明度。报告证据提取对瞬时连接/超时错误错峰退避重试一次；`full_text()` 对无 `pdf_url`/`arxiv_id` 的选定论文走 `resolve_candidate_pdf_urls` OA 级联（Unpaywall/EuropePMC/OpenAlex/机构直链，前 3 候选、18s 预算、%PDF 魔数校验兜住着陆页）。
- 同日盲测修订：新增 `exclude_titles`（最多50条），排除规则由 `services/research_exclusions.py` 统一处理 DOI、arXiv 版本、abs/pdf/html URL 与精确规范标题，不采用标题前缀相似匹配。冲突母论文拒绝进入流程；召回裁剪前、合并后、富化后、追问新旧材料、报告重建与提取前再次过滤。运行上下文使下载器和 `resilient_get` 在每次 HTTP/HTML 重定向前检查已知排除身份。未知镜像链接不能仅凭 URL 识别身份；排除不是对模型预训练记忆的屏蔽。
- `core_query` 为拆解器返回的2至4词核心检索概念。在线研究的召回预算为最多5条相关性检索、1条经典检索、3条 arXiv 补充（核心概念、evaluation、benchmark），Scopus 单页25条，深挖相关式最多2页、经典1页；重试与回退另计。`result.sources.recall` 保存各通道查询、排序、上限、命中数、排除数及警告；这是查询通道预算，不是整次研究 HTTP 请求总量。
- arXiv 搜索每式最多40条，一起返回摘要、DOI与PDF链接；成功响应缓存10分钟、最多128条，相同查询并发合并。API与PDF下载在同进程共享单并发、至少3秒间隔，429遵守Retry-After共享冷却，API最多2次尝试。Scopus各实例在同事件循环共享2并发、至少0.5秒间隔，429更新排队请求的冷却截止时间；等待取消归还信号量。多进程或其他工具不共享此预算。预检并发合并，20秒缓存从探测完成计时。
- OpenAlex富化的同一次请求保留arXiv/PDF地址，无需为已有地址重复解析。报告采用相关性前半与框架/指标/任务数据集/方法四类选材补充；全文预算仍4/8/12篇，优先现成PDF链接，最多3个候选、下载阶段每篇总45秒，单候选最多25秒，解析结果为空不计成功。`survey_report.method.downloads` 记录逐篇尝试数与 available/unavailable/no_url；只接受PDF魔数。深挖提取上限120秒、4000输出token，瞬时错误最多重试1次；独立模型逐行复核保留。最终摘要再基于已核验表格与摘录综合，引用范围程序校验；`method.narrative_source=verified_table_evidence` 标明成功路径。这不是人工关系正确率。
- `deep_research/stage_agent.py` 提供通用节点循环：白名单工具、结构化 action/finish、程序验收、4/6/8 轮综合预算、无进展终止、脱敏短轨迹与 `stage_iteration` 事件；不保存自由思维链。拆解的快览/标准/深挖最多 4/7/10 轮；零命中时分别要求至少 1/2/3 轮不同检索式的真实预览后才允许正式召回。
- `deep_research/survey_report.py` 在快览/标准/深挖中最多选 12/24/40 篇进行证据提取；优先已选母论文，其余 BM25/向量/RRF/重排。单篇并发3；每篇提取最多 4/5/7 轮，工具包括材料检查、提交提取与关系复核，程序验收通过后完成，深挖单轮上限120秒、其余80秒。全文通过BM25针对问题、框架、指标、任务数据集、分类各选择片段，补足5个窗口，每窗最多2400字符，重叠合并；摘要最多10000字符。联网时对前4/8/12篇尝试已有PDF/arXiv链接下载（单篇最多25秒），失败回退摘要，复用PDF解析器，提取后清理临时PDF，文本快照保留供追问。
- `result.survey_report` 包含 `version/status/title/summary/findings/tables/matrices/coverage/extractions/bibliometrics/references/warnings/search_summary/method/markdown`。`status=no_results` 表示正式召回为0：仍生成并允许导出报告，列出预览/正式检索式、来源、排序、上限、命中数、停止原因与建议补搜方向；只陈述本次搜索事实，不使用模型记忆补领域结论，也不把零命中表述为“idea 不存在”。`tables` 有 `frameworks/metrics/tasks/taxonomy`，每行含稳定表内ID与 `evidence[{paper_key,quote,source_kind}]`。程序校验引用摘录在当前材料内（NFKC、PDF跨行断词及空白归一后匹配），框架/指标/数据集专名也必须出现在摘录。关系复核所选材料是否支持全部字段，可结合原材料中对应的表头和脚注，quote作为原文定位（50秒超时）；无依据的行丢弃并计数，复核失败的论文不纳入统计。模型复核不是独立或人工验证，`method.human_validated=false`，不能据此声称准确率。
- 结构化模型调用识别完整Markdown JSON围栏。官方DeepSeek v4端点关闭thinking，避免推理占满输出预算产生空JSON；其他模型调用参数不变。综合循环每轮限制150秒，流派任务限制90秒。
- OpenAI 兼容响应若缺少 `choices` 或最终正文为空，统一返回脱敏的 `LLM_INVALID_RESPONSE_ENVELOPE` / `LLM_EMPTY_CONTENT`（仅含 endpoint host、模型、请求 ID 与正文长度）并按原重试策略处理，不再伪装成普通 JSON 解析错误。配置了解读模型但拆解后仍没有可用英文检索式时，运行于 `decompose` 阶段失败，明确不把纯中文原句发送给学术数据库；未配置 Key 的历史原句回退行为保持兼容。
- 框架行中的可选指标名、任务行中的可选方法名只有出现在该行摘录时才保留，其余留空；必需的框架/指标/数据集实体缺失则整行拒绝。第二轮关系复核同时检查评估工具类别，避免将训练框架/超参数优化库当成评估框架。
- `matrices.aspect_metric` 与 `task_dataset` 的 `rows/columns/cells[{row,column,count,paper_keys}]` 由已通过校验的表格关系汇总。同一单元格按论文去重，不把未提及变成不适用、不把图色当性能分数。名称只做大小写归一，不猜测同义缩写。
- `coverage` 明确材料总数/有材料数/入选数/完成数/失败数/全文片段数/摘要数/被拒绝行数；selected范围完成不等于全领域覆盖。新综合引用编号随参考文献重新映射；没有映射的历史E编号标明未映射。
- 追问继续使用同一个run与消息表，提供原报告上下文及问题相关全文片段，历史回答不作为事实证据。SSE包含 `stage_iteration{stage,iteration,phase,tool/action}`、`report_progress{done,total}`、`report_ready{...report}`；前端只展示轮次/工具/验收状态，不展示模型自由推理文本。完成前再次发送最新产物以恢复下载文本与表格。历史快照不自动批量修改。

| 方法 | 路径 | 参数 | 返回 | 状态码 |
|---|---|---|---|---|
| GET | `/api/exploration/preflight` | 无 | `{checked_at, sources[{name, role primary/fallback, status, detail, latency_ms, http_status}]}`（带权限的真实最小请求探测，20s 短缓存） | 200 |
| POST | `/api/exploration/runs` | `question` 2–4000字符，`depth` quick/standard/deep，`workspace_id?`，`seed_paper_ids?` 最多240，`search_online=true`，`exclude_dois?/exclude_arxiv_ids?/exclude_titles?` 各最多50 | run含options，后台执行统一研究 | 200/400/404/409/422 |
| POST | `/api/exploration/runs/{id}/report` | 无 | 从现有资料或已保存搜索记录生成报告，run变为pending/report；零材料生成 `no_results` 检索审计报告；有活动研究/追问时拒绝 | 200/400/404/409 |
| GET | `/api/exploration/runs/{id}/report` | `format` html/markdown/json，默认html | HTML独立报告（含年份图、热力图、原文及打印样式）、Markdown或JSON；HTML/Markdown为下载附件 | 200/404/422 |

验证与复现：`python -m backend.eval.survey_report_smoke --paper-ids 1 6 43 44 45 --question '研究问题' --output data/eval/survey-report` 使用现有配置生成独立HTML/JSON，不写业务记录；也可用 `--pdf /absolute/path.pdf` 替代论文ID。真实API研究#19完成了母论文输入、综合、报告、持久化、报告重生成及SSE；无母论文与联网召回复用原提供者，关闭文献联网的测试禁止外部检索。报告不是系统综述的完整覆盖证明。浏览器在1280/768/390宽度验证来源链接、放大、导出与重载；后端全量757项及末轮相关25项、前端122项与生产构建通过。

### 2.12 academic_survey_v2 契约层（2026-09-09，S0）

实施 `docs/ACADEMIC_SURVEY_AGENT_PLAN_2026-09-08.md` 的 S0 工作包：数据契约、兼容投影、迁移与接口边界已冻结并落地；**v2 执行器（worker/协议规划/报告 revision 写入）尚未接入**，显式 `workflow_version=academic_survey_v2` 创建运行返回 `400 WORKFLOW_NOT_IMPLEMENTED`。现有 v1 行为全部不变。

**数据契约（`models/research_core.py`）**：常量（`TASK_STATES`=pending/ready/running/succeeded/failed/skipped/cancelled、`RUN_STATES_V2` 增加 paused/completed/partial/cancelled、`QUALITY_STATUSES`=unchecked/internally_checked/needs_review/human_reviewed、`VERIFICATION_STATES`、`IDENTITY_DECISIONS`=allow/deny/unresolved、资产 kind/status、事件类型注册表）+ Core 交换契约（`ResearchProtocol`/`ProtocolSubquestion`/`SearchRecord`/`IdentityDecision`/`ResolvedLocation`/`EvidenceSpan`/`CoverageGap`）+ 10 张 SQLModel 表：`research_tasks`（任务账本，idempotency_key 唯一、lease_owner/lease_expires_at）、`research_task_attempts`（不含密钥）、`research_assets`（不可变：document/protocol/search_record/coverage_snapshot/report/manifest，大文件 storage_path+content_hash、小型 JSON inline_payload）、`research_evidence`（EvidenceBundle，spans 含页/节/表/bbox/字符位置与归一化 quote）、`research_relations`、`research_claims`（text_hash 绑定核验）、`research_report_revisions`（is_selected 标记当前版本，指向 report 资产）、`research_budget_events`（聚合口径：used=Σdelta_used，reserved=Σ(delta_reserved−delta_used−delta_refunded) 下限0；usage_known=false 表示按上界记账）、`research_events`（持久化事件流，SSE 断点续订按 event_id）、`research_identity_decisions`（S1 门禁审计）。全部表 int 自增主键 + 对外 uuid 字符串 ID；`revision_id="legacy"` 保留给 v1 伪版本投影。相对计划 §7.1 建议表清单新增 `research_relations` 与 `research_identity_decisions`（关系是一等查询对象、身份判定需索引检索），search_record/coverage_snapshot 以资产行存储不单列成表。

**ExplorationRun 扩展列**：`workflow_version`（默认 legacy_v1，迁移回填旧数据）、`state_version`（默认 0，v2 创建置 1，CAS 每次 +1）、`protocol_asset_id`。状态操作走 `services/research_event_log.py` 的 `cas_run_state`：**数据库条件更新为并发权威**（UPDATE 以旧状态+旧版本为 WHERE 守卫并校验受影响行数；Python 前置检查只做快速失败；冲突回滚不追加成功事件），research_events 与状态变更同事务落库。幂等重放判定：当前已是目标状态且请求无前置条件，或 `expected_state_version+1==当前版本`（同一迁移已发生）→ 返回原结果不递增版本；其余按 409 STATE_CONFLICT/STALE_STATE_VERSION/INVALID_STATE 拒绝。retry 的运行状态推进复用同一守卫（`guarded_transition`）。任务幂等键唯一性以 `(run_id, idempotency_key)` 生效（同 run 防重复调度，跨 run 同冻结输入各建独立账本；旧全局唯一索引由迁移替换）。`reconcile_interrupted_runs` 豁免 v2 运行（worker 租约与账本负责恢复，不被 API 重启误置 failed）。

**HTTP 边界（沿用 `/api/exploration` 前缀与 `{detail, code}`；仅 GET/POST）**：

| 方法 | 路径 | 请求 | 返回 | 状态码 |
|---|---|---|---|---|
| POST | `/runs`（扩展） | v1 原字段；可选 `workflow_version/protocol_options/budget/content_cutoff/policy_id` | v1 响应 + `workflow_version/state_version/protocol/current_revision`；v2 目前 400 门禁 | 200/400/404/409/422 |
| GET | `/runs/{id}/protocol` | — | 冻结协议+变更记录；v1 为 legacy 投影（options 快照、无子问题） | 200/404 |
| GET | `/runs/{id}/tasks` | `cursor/state/kind/limit` | 分页任务账本；v1 空列表 legacy=true | 200/404/422 |
| GET | `/runs/{id}/coverage` | — | 子问题覆盖+缺口+最近动作；v1 投影 `result.survey_report.coverage` | 200/404 |
| GET | `/runs/{id}/budget` | — | 各资源 limit/used/reserved/usage_known+停止原因；无凭证 | 200/404 |
| POST | `/runs/{id}/pause` | `expected_state/expected_state_version` 可选 | CAS 至 paused（safe-point 语义） | 200/400/404/409 |
| POST | `/runs/{id}/resume` | 同上 | v1 从断点恢复；v2 worker 接入前返回 `WORKFLOW_NOT_IMPLEMENTED`，不改状态 | 200/400/404/409 |
| POST | `/runs/{id}/cancel` | 同上 | 取消并保留既有产物 | 200/400/404/409 |
| POST | `/runs/{id}/retry` | `failed_only/task_ids/idempotency_key` | 失败/失效任务重入队 pending；同 key 重放返回原结果 | 200/400/404/409/422 |
| GET | `/runs/{id}/revisions` | `cursor/limit` | 不可变版本列表；v1 有报告投影单个 legacy 伪版本 | 200/404/422 |
| GET | `/runs/{id}/revisions/{rid}/report` | `format=html/markdown/json` | 指定版本报告（v1 rid=legacy 等价旧 GET report） | 200/404/422 |
| GET | `/runs/{id}/evidence/{eid}` | — | 摘录+文档版本+页/表定位；隔离资产 403 | 200/403/404 |
| GET | `/runs/{id}/stream`（扩展） | `Last-Event-ID` 头或 `after` 查询参数 | v2：回放持久化事件（带 `id:` 帧）→ run_status → live-bus（S3 注册前活动 run 返回 error 事件）；v1 行为不变 | 200/404/422 |

v1 守卫：`POST /runs/{id}/report` 与 `DELETE /runs/{id}` 对 v2 运行返回 `WORKFLOW_NOT_IMPLEMENTED`（报告 revision 重建待 S6、级联删除待 S3），防止旧路径覆盖 v2 产物。控制类端点对 v1 返回 400 `UNSUPPORTED_WORKFLOW`。旧 `GET /runs/{id}/report` 对 v2 运行解析**当前选定 revision**（`is_selected=true` 最新行；无选定版本 404，不用 result 历史快照顶替；JSON 附 `revision_id`），所有格式响应带 `X-Report-Revision` 头（v1 为 `legacy`）；报告资产统一走可暴露性检查：quarantined→403 `REPORT_NOT_EXPOSED`、orphaned/缺失/跨 run/非 report kind→404、superseded 历史版本可读（默认与指定版本端点共用 `_load_exposable_report`）。

**迁移与回滚**：`_migrate_schema` 为 exploration_runs 增列（NOT NULL 带默认，旧行自动回填 legacy_v1/0/NULL）并以 `create_all(checkfirst)` 建新表（含 research_tasks 幂等索引从全局唯一迁至 `(run_id, idempotency_key)` 唯一，DROP 旧索引建新索引），幂等；不删不改既有列。**回滚形态 = 保留本 S0 契约层代码**：v2 数据经只读端点（revisions/report/protocol/evidence）继续可读、创建门禁关闭新 v2 执行、旧写入路径（report 重建/DELETE）被守卫阻断、v1 入口照常——已由端到端测试验证。注意：直接回退到不识别 v2 的历史版本只能保证 v1 数据可读（附加列不破坏旧 SELECT，测试验证），v2 报告（资产+revision）**不能**经旧代码读取。手动清理（仅在放弃 v2 数据时执行，先停服备份；SQLite 不支持逗号多表 DROP，须逐条）：

```sql
DROP TABLE research_identity_decisions;
DROP TABLE research_events;
DROP TABLE research_budget_events;
DROP TABLE research_report_revisions;
DROP TABLE research_claims;
DROP TABLE research_relations;
DROP TABLE research_evidence;
DROP TABLE research_assets;
DROP TABLE research_task_attempts;
DROP TABLE research_tasks;
ALTER TABLE exploration_runs DROP COLUMN workflow_version;   -- SQLite >= 3.35
ALTER TABLE exploration_runs DROP COLUMN state_version;
ALTER TABLE exploration_runs DROP COLUMN protocol_asset_id;
```

**集成缝（其他 sub 接入点）**：S3 替换 `routers/exploration.py` 中 `WORKFLOW_NOT_IMPLEMENTED` 门禁为 worker 提交，并通过 `research_event_log.register_live_bus(run_id, bus)` 接入实时事件（bus 需暴露 `subscribe()` 异步迭代器）；S5 写 protocol/search_record/coverage_snapshot 资产并回填 `run.protocol_asset_id`；S6 写 report 资产 + revision 行（同事务）；S1 写 identity_decisions；S2 写 document 资产。`frontend/src/types/index.ts` 已同步全部契约类型（S7 联调用）。

验证：后端全量 838 项通过（含 `tests/test_survey_v2_contract.py` 30 项：迁移幂等/旧索引升级/回滚边界、reconcile 豁免、v2 门禁、v1 兼容投影、CAS 原子竞争（相反/同目标仅一个成功、重放区别于陈旧请求）、预算聚合、SSE 断点续订、默认报告选定 revision 与隔离拒绝、回滚形态端到端、CORS 预检/拒绝、{detail,code} 错误格式）；Ruff、前端 typecheck/124 项测试/生产构建通过。测试全部使用合成数据与临时库。2026-09-09 复审（F1–F5）后修订：状态 CAS 改为数据库条件更新、任务幂等键 run 作用域、默认报告读选定 revision、隔离资产拒绝导出、回滚口径改为 S0 契约层形态并修正多表 DROP 语法。

### 2.13 统一 LLM Core 与全局设置（2026-09-09）

`services/llm` 是所有文本生成调用的唯一协议边界。业务服务只调用 `complete_text`、`complete_json` 或 `stream_text`；OpenAI Chat Completions 与 Anthropic Messages 的 SDK 创建、参数转换、响应提取、请求 ID、错误脱敏和连接测试分别由 adapter 实现。结构化输出共享围栏清理、平衡 JSON 提取和重试，空正文/异常响应统一返回 `LLM_EMPTY_CONTENT` / `LLM_INVALID_RESPONSE_ENVELOPE`。

配置真源只有 `llm`：`provider/api_format/api_key/base_url/model/temperature/max_tokens/thinking_effort/timeout_seconds`。`api_format` 必须是 `openai_chat` 或 `anthropic_messages`，业务代码不得按 URL 猜协议；旧的 `analysis` / `translate` 段和旧环境变量只在配置边界迁移一次。主配置与导出不再写出重复模型段，密钥仍单独写入根目录 `config_key`；重新加载时 `config_key` 只注入密钥，不得覆盖主配置已保存的 provider、协议、Base URL、模型或参数。仅当旧主配置没有 `llm` 段时，才允许把旧版完整 `config_key` 的非密钥字段作为一次性兼容迁移来源。翻译提示词与解读提示词是任务参数，不再拥有独立 provider/model/key。

设置页只展示一张“全局 LLM 模型”表单。供应商预设由 `GET /api/analysis/llm/providers` 返回，选择预设会同时填充 provider、协议、Base URL 和默认模型；自定义配置必须显式选择协议。论文解析、划词操作、代码分析、相关论文说明、图谱速读、研究拆解/综合、引用标题提取和 RAG 回答均读取这份全局配置。

`GET /api/analysis/llm-profiles/{id}/key` 仅允许回环网络对端（测试客户端除外），返回头含 `Cache-Control: no-store`；私网/LAN 客户端不因伪造 `X-Forwarded-For` 获得本地信任。手动刷新相关论文时，provider、api_format、thinking、timeout 和 proxy 与当前档案一起传入统一 LLM Core。

### 2.14 legacy_v1 报告证据补读与终稿核验（2026-09-13）

本节扩展现有 `legacy_v1` 报告链，不启用 `academic_survey_v2` worker，也不改变下载与在线召回边界。`deep_research/document_evidence.py` 只索引运行已经保存的全文/摘要：按页、节和表格建立稳定 location ID，支持问题检索、相邻段落及表格上下文读取；获取文件仍由原下载层负责。补读工具不能联网获取新论文，材料不足时把缺口写入报告。

`deep_research/report_evidence.py` 是报告内共享证据存储。证据 ID 由规范化的证据类型、结构化事实、论文键和原文定位生成；重复提交相同事实与摘录会合并子问题而不新增记录。完整记录写入 `result.survey_report.evidence_store.records` 和报告阶段 checkpoint，不受单次 LLM 上下文大小或列表分页影响。`evidence_version` 是全库规范内容哈希，供终稿核验绑定使用。

报告 `version=2` 保留 `tables.frameworks/metrics/tasks/taxonomy`，新增 `tables.experimental_comparisons`。每行字段为：`method/baseline_method/task/dataset/split/metric/value/baseline_value/unit/metric_direction/hardware/batch_size/input_size/precision/comparison_limit`，以及通用 `id/evidence`。证据详情包含：

- `fact`：从材料逐字或结构化提取的上述字段；原文未说明的字段保持空值并列入 `missing_fields`。
- `source/spans`：`paper_key/document_hash/source_kind/quote/page/section/table/char_start/char_end/location_id/location_status`。无法解析定位时 `location_status=partial|unavailable`，不猜页码、章节或表号。
- `comparability`：记录比较范围与限制；`cross_paper_fairness_assumed` 固定为 false，跨论文结果不自动解释为公平对比。
- `verification`：逐字值检查、关系复核状态、失败原因和时间；引用编号只负责定位，不能代替关系或断言支持。
- `subquestion_ids`：默认按效果比较、运行效率、方法机制、局限、查新组织，可被多个子问题复用。

逐篇研读 agent 先消费固定初始材料；材料不足时可调用 `search_full_text` 和 `read_context` 定位已有全文，再提交提取并做关系复核。子问题保留效果、效率、机制、局限和查新五个稳定证据类型，但每个定位查询都从当前 `question` 提取有界主题词并叠加通用证据词，不得使用固定的图像分类/ImageNet 词表。相同查询、位置或证据重复读取不计进展。最终报告 agent 可分页检查子问题证据索引、按 ID 读取详情、从 `available_materials` 取得精确 paper key、对已获取论文定向补读和提交结构化补提取、原子提交 `summary/findings/limitations/gaps/novelty`，并调用终稿核验。旧的“每表前 24 行”摘要输入已移除；上下文只分页传索引和定向详情，完整证据始终保存在共享存储。报告阶段的外层等待时间不早于统一 LLM 配置的 `timeout_seconds`（另留 5 秒编排缓冲），避免在供应商期限之前取消请求；供应商自身超时仍按失败关闭并保留证据。

终稿核验复用 `evidence_audit`，范围包含摘要、发现、局限、缺口、查新结论及实验比较断言。失败项返回断言、原因、相关 evidence ID 和可执行修复方向。核验结果绑定 `binding.text_hash` 与 `binding.evidence_version`；文本或证据变化会使旧结果失效，修改后必须重新核验。只有当前绑定、核验完成且无 unsupported claim 时 `quality_status=internally_checked`；未运行、异常或失败均为 `needs_review`，前端不得显示“核验通过”。报告 revision 写入已有 `research_assets/research_report_revisions`，重生成新版本并保留旧版本可读；这不等于启用 v2 执行器。

`method.diagnostics` 分别记录 `locate/extract/relation_verify/synthesize/final_audit` 的耗时、调用数、失败原因；`method.token_usage` 在供应商返回 usage 时记录各阶段 token，否则明确 `usage_known=false`。逐篇任务与整份报告各有轮次/读取/调用预算；超时、预算耗尽或连续无新增有效证据时停止为 partial/needs_review，保留已完成表格、共享证据、检查点和未解决缺口。

HTTP 接口（仅 GET/POST）：

| 方法 | 路径 | 参数 | 返回 | 状态码 |
|---|---|---|---|---|
| GET | `/api/exploration/runs/{id}/evidence` | `subquestion_id?`、`cursor>=0`、`limit=1..50` | `{run_id,workflow_version,evidence_version,subquestion_id,cursor,limit,total,next_cursor,items[]}`；item 只含 ID、事实摘要、缺失/可比/核验/定位状态 | 200/404/422 |
| GET | `/api/exploration/runs/{id}/evidence/{eid}` | 路径参数 | `EvidenceDetailResponse`：结构化事实、完整 spans、缺失字段、可比性、核验和文档版本；旧报告无共享存储为 404 | 200/403/404 |
| GET | `/api/exploration/runs/{id}/revisions` | `cursor?`、`limit=1..100` | v1 新报告返回真实不可变 revision；没有 revision 的旧报告仍投影 `legacy` | 200/404/422 |
| GET | `/api/exploration/runs/{id}/revisions/{rid}/report` | `format=html|markdown|json` | 指定 v1/v2 不可变报告版本；`rid=legacy` 兼容当前旧报告 | 200/403/404/422 |

固定材料诊断：`python -m backend.eval.report_evidence_diagnostic --run-id 11 --output data/eval/run11-report-evidence-20260913.json` 仅读取本地运行和保存文本，输出旧报告命中、定向补读命中、缺失条件与耗时，不调用搜索或 LLM、不写业务库。`backend.eval.report_regen_fixed` 可基于指定 paper key 另存重生成 JSON/HTML；模型失败时保存失败关闭状态，不覆盖原运行。两者只用于链路/回归诊断，不构成准确率提升证明。

### 2.15 合规 LLM 代理传输（2026-09-14）

`services/llm_proxy/` 是统一 LLM Core 下的独立传输选择模块。全局 LLM 配置和每个配置档案新增 `proxy={enabled,base_url}`：关闭时 adapter 连接原 `llm.base_url`；开启时连接规范化后的 `llm.proxy.base_url`，协议、模型名、超时与现有 API Key 仍沿用该档案。OpenAI Chat Completions 与 Anthropic Messages 的普通请求、流式请求和连接测试走同一选择逻辑；错误中的 endpoint 指向实际代理主机，仍不输出 API Key。

代理端必须是用户有权使用的 OpenAI/Anthropic 兼容网关。模块不会注入、开放配置或改写 `User-Agent`、`Authorization`、`x-api-key`、应用名、设备 ID 等客户端身份字段，也不读取 Codex、Claude Code 或其他应用的订阅登录态；开源客户端许可证不等于第三方套餐的调用授权。代理开启但地址为空时，调用前返回“启用代理转发后必须填写代理 Base URL”；非法地址仍按统一 URL 校验拒绝。

学术 PDF 下载对初始 URL 和每个重定向目标执行公网地址校验：拒绝带凭据 URL、localhost/`.local`/`.internal`、回环、私网、链路本地、保留及非全局 IP；域名解析失败或任一解析结果不是公网地址时失败关闭。跨主机重定向不转发授权头。

现有 HTTP 路径和方法不变：`GET/POST /api/analysis/config` 的 `llm`、LLM 配置档案请求/响应、`POST /api/analysis/test-connection` 均接受 `proxy.enabled:boolean` 与 `proxy.base_url:string`。设置页在每个档案编辑器中提供“通过自托管代理转发”开关和代理 Base URL；连接测试使用当前档案的真实密钥与代理设置。CORS、`{detail,code}`、密钥脱敏及 `config_key` 单独存储规则不变。

## 3. 统一约定

### 3.1 响应格式

- 成功响应通常直接返回 JSON 对象或列表。
- 失败响应统一使用：

```json
{ "detail": "错误描述", "code": "ERROR_CODE" }
```

- `HTTPException` 的字典或字符串 `detail`、请求参数校验失败（422）和限流（429）均由应用入口转换为上述顶层格式。

### 3.2 常见状态码

| 状态码 | 含义 |
|---|---|
| `200` | 成功 |
| `400` | 参数或业务错误 |
| `404` | 资源不存在 |
| `409` | 冲突，例如任务已运行 |
| `422` | Pydantic 校验失败 |
| `429` | 请求超过默认 `120/minute` 限制 |
| `500` | 未处理异常 |

## 4. 数据模型

### 4.1 Paper

| 字段 | 说明 |
|---|---|
| `id` | 论文 ID |
| `filename` | 原始文件名 |
| `pdf_path` | 本地 PDF 路径 |
| `title/authors/venue/year/category` | 元信息 |
| `full_text` | 自动索引后的全文 |
| `index_status` | `none/indexing/indexed/failed` |
| `status` | `pending/extracting/analyzing/done/failed/indexed` |
| `code_url/code_status/code_path/code_summary` | 代码仓库信息 |
| `source` | `uploaded/expanded` |
| `level` | 论文层级 |
| `parent_paper_id` | 扩展来源 |
| `external_id` | 外部去重 ID |
| `canonical_id/doi/arxiv_id/openalex_id/s2_id` | 规范化论文身份 |
| `download_status` | `none/pending/available/unavailable/failed` |
| `source_ids` | 各 Provider 原始 ID 的可审计映射 |
| `graph_digest` | 图谱速读摘要 |

### 4.2 AnalysisResult

| 字段 | 说明 |
|---|---|
| `title/authors/venue/year` | 论文基础信息 |
| `one_sentence/problem` | 核心结论与问题 |
| `key_contributions` | 贡献列表 |
| `outline` | 章节大纲 |
| `method` | 方法结构 |
| `experiments` | 实验结构 |
| `code_mapping` | 概念到代码映射 |
| `interview_qa` | 面试问答 |
| `limitations` | 局限性 |
| `related_papers` | 相关论文 |
| `mermaid_chart` | 方法流程图 |

### 4.3 ResearchRun / ResearchGraph

| 模型 | 关键字段 |
|---|---|
| `ResearchRun` | `workspace_id/mode/status/current_stage/params/node_count/edge_count` |
| `ResearchGraph` | `workspace_id/run_id/nodes/edges/stats` |

### 4.4 Workspace / Note / Tag / Annotation

| 模型 | 关键字段 |
|---|---|
| `Workspace` | `id/name/created_at/updated_at` |
| `WorkspacePaper` | `workspace_id/paper_id/added_at` |
| `Note` | `paper_id/content/note_type/section_name/page_number` |
| `Tag` | `id/name/color` |
| `PaperTag` | `paper_id/tag_id/created_at` |
| `Annotation` | `paper_id/page_number/x/y/width/height/annotation_type/color/text/group_id` |

## 5. 接口

### 5.1 Papers

| 方法 | 路径 | 主要参数 | 返回 | 状态码 |
|---|---|---|---|---|
| POST | `/api/papers/upload` | `file`(multipart PDF) | `PaperResponse` | 200/400/422 |
| GET | `/api/papers` | `search/category/subject/venue/publication_type/provider/year/date_added/page/per_page/source/sort` | `PaperListResponse` | 200/400/422 |
| GET | `/api/papers/smart-search` | `q/category/subject/venue/publication_type/provider/year/date_added/global/include_expanded/page/per_page` | `SearchResponse` | 200/400/422 |
| GET | `/api/papers/facets` | `source=uploaded\|expanded\|all` | `subjects/venues/publication_types/providers/categories/years/dates/sorts` | 200/422 |
| POST | `/api/papers/metadata/refresh` | `source=uploaded\|expanded\|all`，默认 uploaded | `running/total/completed/unmatched`，后台继续执行 | 200/422 |
| GET | `/api/papers/metadata/status` | 无 | `running/total/completed/unmatched` | 200 |
| POST | `/api/papers/{paper_id}/metadata` | JSON：`title/authors/venue/year/publication_type/subjects/publisher` | 更新后的 `PaperResponse` | 200/404/422 |
| GET | `/api/papers/{paper_id}` | 路径参数 | `PaperResponse` | 200/404 |
| DELETE | `/api/papers/{paper_id}` | 路径参数 | `{"message": ...}` | 200/404 |
| POST | `/api/papers/{paper_id}/promote` | 路径参数 | `{"message": ...}` | 200/404 |
| POST | `/api/papers/reindex` | `include_expanded` | `{"queued": n}` | 200 |
| GET | `/api/papers/{paper_id}/export` | 路径参数 | Markdown 文本 | 200/404 |
| GET | `/api/papers/{paper_id}/pdf` | 路径参数 | PDF 文件 | 200/404 |
| GET | `/api/papers/{paper_id}/headings` | 路径参数 | 章节定位列表 | 200/404 |

### 5.2 Analysis

| 方法 | 路径 | 主要参数 | 返回 | 状态码 |
|---|---|---|---|---|
| GET | `/api/analysis/config` | 无 | `AppConfig` | 200 |
| POST | `/api/analysis/config` | `AppConfig` | `{"message": ...}` | 200 |
| GET | `/api/analysis/llm/providers` | 无 | `{providers:[{id,label,api_format,base_url,default_model}]}` | 200 |
| GET | `/api/analysis/relations/channels` | 无 | `{"channels": [...]}` | 200 |
| GET | `/api/analysis/builtin-analysis-prompt` | 无 | `{"content": ...}` | 200 |
| GET | `/api/analysis/grobid/status` | 无 | GROBID 状态 | 200 |
| POST | `/api/analysis/grobid/pull` | 无 | 拉取状态 | 200 |
| POST | `/api/analysis/test-connection` | `type/provider/api_format/api_key/base_url/model/timeout_seconds` | `{ok,message,latency_ms,request_id,api_format}` | 200/400 |
| POST | `/api/analysis/text-action` | `text/action` | `{"result": ...}` | 200/400/422 |
| POST | `/api/analysis/digest/batch` | `workspace_id/force/max_papers` | 批量速读状态 | 200/400 |
| GET | `/api/analysis/digest/status/{workspace_id}` | 路径参数 | 速读进度 | 200 |
| POST | `/api/analysis/digest/{paper_id}` | 路径参数 | 单篇速读结果 | 200/400/404 |
| POST | `/api/analysis/findpapers/test-databases` | `databases` | 数据库检测 | 200 |
| POST | `/api/analysis/import-related-papers` | `workspace_id/papers[]` | 导入结果 | 200/404 |
| GET | `/api/analysis/{paper_id}` | 路径参数 | `AnalysisResponse` | 200/404 |
| GET | `/api/analysis/{paper_id}/bibtex` | 路径参数 | BibTeX 文本 | 200/400/404 |
| GET | `/api/analysis/{paper_id}/stream` | 路径参数 | SSE | 200/404 |
| POST | `/api/analysis/{paper_id}/start` | 路径参数 | 进度信息 | 200/404 |
| GET | `/api/analysis/{paper_id}/progress` | 路径参数 | 进度信息 | 200/404 |
| POST | `/api/analysis/{paper_id}/reparse` | 路径参数 | `PaperResponse` | 200/404 |
| POST | `/api/analysis/{paper_id}/related-papers` | 路径参数 | `RelatedPaper[]` | 200/404 |

### 5.3 Code

| 方法 | 路径 | 主要参数 | 返回 | 状态码 |
|---|---|---|---|---|
| POST | `/api/code/{paper_id}/extract-url` | 路径参数 | `{"code_url": ...}` | 200/404 |
| POST | `/api/code/{paper_id}/clone` | 路径参数 | 克隆结果 | 200/400/404/500 |
| GET | `/api/code/{paper_id}/tree` | 路径参数 | 文件树 | 200/400/404 |
| GET | `/api/code/{paper_id}/file` | `path` | 文件内容或 notebook cell | 200/400/404 |
| GET | `/api/code/{paper_id}/analyze` | 路径参数 | SSE | 200/400/404 |
| GET | `/api/code/{paper_id}/analysis` | 路径参数 | `CodeAnalysisResponse` | 200/404 |
| GET | `/api/code/{paper_id}/summary` | 路径参数 | `CodeSummaryResponse` | 200/404 |

### 5.4 Workspaces

| 方法 | 路径 | 主要参数 | 返回 | 状态码 |
|---|---|---|---|---|
| GET | `/api/workspaces` | 无 | 工作区列表 | 200 |
| POST | `/api/workspaces` | `name` | `WorkspaceResponse` | 200 |
| GET | `/api/workspaces/{workspace_id}` | 路径参数 | `WorkspaceDetailResponse` | 200/404 |
| GET | `/api/workspaces/{workspace_id}/insights` | 路径参数 | `WorkspaceInsightResponse` | 200/404 |
| GET | `/api/workspaces/{workspace_id}/reading-plan` | `target_paper_id` 可选 | `WorkspaceReadingPlanResponse` | 200/404 |
| GET | `/api/workspaces/{workspace_id}/survey` | 路径参数 | `WorkspaceSurveyResponse` | 200/404 |
| POST | `/api/workspaces/{workspace_id}/compare` | `paper_a_id/paper_b_id` | `WorkspaceComparisonResponse` | 200/400/404 |
| PUT | `/api/workspaces/{workspace_id}` | `name` | `WorkspaceResponse` | 200/404 |
| DELETE | `/api/workspaces/{workspace_id}` | 路径参数 | `{"message": ...}` | 200/404 |
| POST | `/api/workspaces/{workspace_id}/papers` | `paper_id` | `{"message": ...}` | 200/404/400 |
| POST | `/api/workspaces/{workspace_id}/papers/{paper_id}/promote` | 路径参数 | `{"message": ...}` | 200/404 |
| DELETE | `/api/workspaces/{workspace_id}/papers/{paper_id}` | 路径参数 | `{"message": ...}` | 200/404 |

### 5.5 Research

| 方法 | 路径 | 主要参数 | 返回 | 状态码 |
|---|---|---|---|---|
| POST | `/api/research/workspaces/{ws_id}/runs` | `ResearchRunCreate` | `ResearchRunResponse` | 200/400/404/409 |
| GET | `/api/research/runs/{run_id}` | 路径参数 | `ResearchRunResponse` | 200/404 |
| GET | `/api/research/runs/{run_id}/stream` | 路径参数 | SSE | 200/404 |
| GET | `/api/research/workspaces/{ws_id}/graph` | 路径参数 | `ResearchGraphResponse` | 200/404 |
| GET | `/api/research/workspaces/{ws_id}/latest-run` | 路径参数 | `ResearchRunResponse` | 200/404 |
| POST | `/api/research/workspaces/{ws_id}/query` | `question/top_k/paper_ids/mode(vector|hybrid|graph)` | `RagQueryResponse(answer/citations/path_evidence/debug)`；citation 含检索来源分数与 PDF 状态 | 200/404 |
| DELETE | `/api/research/workspaces/{ws_id}` | 路径参数 | `{"message": ...}` | 200 |

### 5.6 Notes / Tags / Annotations

| 模块 | 方法 | 路径 | 返回 | 状态码 |
|---|---|---|---|---|
| Notes | GET | `/api/notes/paper/{paper_id}` | `NoteResponse[]` | 200 |
| Notes | POST | `/api/notes/paper/{paper_id}` | `NoteResponse` | 200/404 |
| Notes | PUT | `/api/notes/{note_id}` | `NoteResponse` | 200/404 |
| Notes | DELETE | `/api/notes/{note_id}` | `{"message": ...}` | 200/404 |
| Tags | GET | `/api/tags` | `TagResponse[]` | 200 |
| Tags | POST | `/api/tags` | `TagResponse` | 200/400 |
| Tags | PUT | `/api/tags/{tag_id}` | `TagResponse` | 200/404/400 |
| Tags | DELETE | `/api/tags/{tag_id}` | `{"message": ...}` | 200/404 |
| Tags | GET | `/api/tags/paper/{paper_id}` | `TagResponse[]` | 200 |
| Tags | POST | `/api/tags/paper/{paper_id}` | `{"message": ...}` | 200/404 |
| Tags | DELETE | `/api/tags/paper/{paper_id}/{tag_id}` | `{"message": ...}` | 200 |
| Tags | GET | `/api/tags/papers/{tag_id}` | 论文列表 | 200 |
| Annotations | GET | `/api/annotations/paper/{paper_id}` | `AnnotationResponse[]` | 200 |
| Annotations | POST | `/api/annotations/paper/{paper_id}` | `AnnotationResponse` | 200/404 |
| Annotations | DELETE | `/api/annotations/group/{group_id}` | `{"message": ...}` | 200/404 |
| Annotations | DELETE | `/api/annotations/{annotation_id}` | `{"message": ...}` | 200/404 |

### 5.7 System

| 方法 | 路径 | 返回 | 状态码 |
|---|---|---|---|
| GET | `/` | 健康状态与 API 版本 `5.0.1` | 200/429 |
| GET | `/api/logs/stream` | SSE 日志流；仅可信本地来源，浏览器 Origin 必须同源 | 200/403/429 |
| POST | `/api/terminal/session` | 一次性终端票据（30 秒有效，仅可信本地前端 Origin） | 200/403 |
| WS | `/api/terminal/ws` | PTY 终端；`Sec-WebSocket-Protocol: essay-terminal.<ticket>` | 101/4003/4029 |
| POST | `/api/system/open-vscode` | 打开项目内路径；仅可信本地同源前端 | 200/403 |

### 5.8 Agent Tools / MCP

| 方法 | 路径 | 主要参数 | 返回 | 状态码 |
|---|---|---|---|---|
| GET | `/api/agent-tools/manifest` | 无 | `AgentToolManifestResponse` | 200 |
| GET | `/api/agent-tools/workspaces` | 无 | 工作区列表 | 200 |
| GET | `/api/agent-tools/workspaces/{workspace_id}/papers` | 路径参数 | 工作区论文列表 | 200/404 |
| POST | `/api/agent-tools/papers/search` | `query/workspace_id/limit/include_expanded` | 论文检索结果 | 200/400/404 |
| GET | `/api/agent-tools/papers/{paper_id}` | 路径参数 | 论文结构化上下文 | 200/404 |
| GET | `/api/agent-tools/workspaces/{workspace_id}/graph` | 路径参数 | 图谱快照 | 200/404 |
| POST | `/api/agent-tools/workspaces/{workspace_id}/graph/neighbors` | `paper_id/depth/limit/edge_kinds` | 图谱邻域 | 200/400/404 |
| POST | `/api/agent-tools/workspaces/{workspace_id}/evidence` | `query/top_k/paper_ids` | 证据片段列表 | 200/400/404 |
| POST | `/api/agent-tools/workspaces/{workspace_id}/dev-brief` | `goal/top_k/paper_ids` | 论文证据约束开发 brief | 200/400/404 |
| GET | `/api/agent-tools/workflow/langgraph-spec` | 无 | LangGraph 工作流规范 | 200 |
| GET | `/api/agent-tools/workflows` | 无 | Research Agent workflow 列表 | 200 |
| GET | `/api/agent-tools/workflows/{workflow_name}` | 路径参数 | workflow 规范 | 200/404 |
| POST | `/api/agent-tools/workspaces/{workspace_id}/reading-plan` | `target_paper_id` 可选 | 工作区阅读计划 | 200/400/404 |
| POST | `/api/agent-tools/workspaces/{workspace_id}/survey` | 路径参数 | 工作区综述矩阵与草稿 | 200/400/404 |
| POST | `/api/agent-tools/runs` | `workflow/goal/workspace_id` | workflow journal 与 artifacts；失败 `{detail, code}` | 200/400/404/422/500 |

#### 5.8.1 MCP stdio

| 启动命令 | 工具来源 |
|---|---|
| `python -m backend.mcp_server` | `services/agent_tools.py` |

MCP 工具：

| 工具名 | 功能 | 参数 |
|---|---|---|
| `paper_search` | 检索本地论文库或工作区论文 | `query/workspace_id/limit/include_expanded` |
| `paper_get` | 获取单篇论文结构化上下文 | `paper_id` |
| `paper_retrieve_evidence` | 从工作区向量索引检索证据片段 | `workspace_id/query/top_k/paper_ids` |
| `graph_get` | 获取工作区图谱快照 | `workspace_id` |
| `graph_neighbors` | 获取图谱中某论文邻域 | `workspace_id/paper_id/depth/limit/edge_kinds` |
| `dev_brief` | 生成论文证据约束的开发 brief | `workspace_id/goal/top_k/paper_ids` |
| `reading_plan_build` | 构建工作区阅读计划 | `workspace_id/target_paper_id` |
| `survey_build` | 构建工作区综述矩阵与草稿 | `workspace_id` |

兼容旧工具名：`list_workspaces`、`list_papers_in_workspace`、`get_paper_analysis`、`get_citation_graph`、`query_workspace_rag`。

## 6. 前端结构

### 6.1 页面

- `HomePage`：上传入口、最近论文、工作区概览
- `ReaderPage`：PDF 预览、解析结果、标签、笔记、标注
- `LibraryPage`：统一分页浏览上传/扩展论文，组合筛选、全文搜索、元数据补全与手工修正；`PaperResponse` 增加 `subjects/publication_type/publisher/providers/metadata_sources/metadata_updated_at/doi/arxiv_id`。`publication_type` 为 journal/conference/preprint/book/other/unknown；`subject/venue/provider=__missing__` 可筛未识别值。
- `WorkspacePage`：工作区列表
- `WorkspaceDetailPage`：工作区论文管理、洞察、阅读路径、综述矩阵
- `ResearchPage`：图谱构建、RAG、节点/边详情
- `CodePage`：代码分析与终端
- `SettingsPage`：模型、主题、研究配置

### 6.2 关键组件

- `AnalysisPanel`
- `PdfViewer`
- `GraphCanvas`
- `WorkspaceInsights`
- `WorkspaceInnovationPanel`
- `WorkspaceComparison`
- `TagManager`
- `NotesPanel`
- `SelectionPopup`
- `TerminalPanel`
- `VersionPopup`

### 6.3 视觉呈现分层

- `main.tsx` 按 `globals.css -> experience.css -> pages.css` 的顺序加载全局样式；`HomePage` 在路由模块内单独加载 `home.css`，版本信息、Toast、快捷键帮助和 Tooltip 共享按组件加载的 `overlays.css`。
- `globals.css` 定义基础令牌、元素基线、旧业务组件继续使用的语义变量和通用 reduced-motion 降级规则。
- `experience.css` 定义应用壳层、主导航、上下文导航、路由登记信息、对话框、加载状态、场景几何和全局响应式网格。
- `pages.css` 以 `er-page--*` 路由根类重排 `LibraryPage`、`WorkspacePage`、`WorkspaceDetailPage`、`SettingsPage`、`ReaderPage`、`CodePage`、`ResearchPage` 和 `NotFoundPage` 的生产页面表面，不改变页面组件的业务属性和事件契约。
- `home.css` 只负责 `HomePage` 与 `UploadZone` 的 Origin 输入面、统计登记带、最近论文、快捷入口和最近工作区布局。
- `overlays.css` 只负责版本登记平面、系统通知登记条、快捷键登记表和 Portal Tooltip；对应组件继续独立处理焦点捕获、Escape、延时和关闭行为。
- 呈现层使用暖纸、墨黑、钴蓝、信号红、赭黄、硬边界与登记线；应用壳层统一移除圆角、阴影、玻璃模糊、发光和文字渐变。装饰几何使用 `aria-hidden`，主要内容、导航和操作继续使用语义 DOM。
- 宽度大于 `1000px` 时使用 12 栏；`<=1000px` 使用 8 栏；`<=700px` 使用 6 栏。壳层、首页和路由页面在同一组断点分别处理栏位、边距、导航密度及 Reader/Code/Research 工作面。

### 6.4 路由视觉状态

`Layout` 保持业务路由不变，并将 `location.pathname` 转换为只供呈现层消费的 `data-scene`、`data-route`、路由编号和登记文本：

| 路径 | `data-route` | `data-scene` | 编号 |
|---|---|---|---|
| `/` | `home` | `origin` | `00` |
| `/library` | `library` | `archive` | `01` |
| `/workspace` | `workspace` | `field` | `02` |
| `/workspace/:id` | `workspace-detail` | `field` | `02` |
| `/workspace/:id/research` | `research` | `field` | `06` |
| `/reader/:id` | `reader` | `field` | `04` |
| `/code/:id` | `code` | `process` | `05` |
| `/settings` | `settings` | `process` | `03` |
| 未匹配路由 | `not-found` | `archive` | `99` |

- `data-scene` 选择共享空间质量体、轴线和指针孔径变体；`data-route` 选择具体页面工作面。两者不写入论文、工作区、解析或研究状态。
- 论文路由保留 Reader/Code 上下文切换与来源工作区返回路径；工作区详情保留论文集合/深度研究上下文切换。
- 设置页未保存离开守卫、全局快捷键、终端面板、版本信息、Toast、错误边界和懒加载路由继续由原组件负责。

### 6.5 主题与 Motion 数据流

```text
UIConfig.theme / custom_themes / font
  -> ThemeProvider
  -> resolveTheme + applyTheme
  -> <html> CSS 变量 + data-theme + data-mode + --font-ui
  -> globals / experience / pages / home 呈现层

location.pathname
  -> Layout.routeVisual
  -> data-scene + data-route
  -> 场景几何、登记信息与路由工作面

系统 prefers-reduced-motion + 页面内 Motion 覆盖
  -> data-motion(full|reduced) + data-motion-source(system|user)
  -> CSS 动画降级 + 指针 RAF 启停
```

- 主题以 `bg/fg/surface/accent/muted/radius` 六个 seed token 为输入，派生原有背景、边界、文字、状态和代码区变量，同时输出 `--paper`、`--ink`、`--blue`、`--signal`、`--ochre` 等迁移后的结构色别名；`ThemeColors` 继续作为旧消费方的兼容契约。
- 内置主题为 `light`（构成纸面，默认）和 `dark`；两个内置主题的 `radius` 均为 `0`。自定义主题仍通过 `custom:<id>` 解析，字体仍由 UI 配置注入。
- Motion 默认跟随系统偏好，用户操作在当前 `Layout` 生命周期内设置覆盖状态。`data-motion="reduced"` 与 `prefers-reduced-motion: reduce` 都会停止或压缩主要动画并隐藏路由切场与指针孔径；系统媒体查询保留最终 CSS 降级。
- 细指针且 Motion 有效时，`pointermove` 由单个 `requestAnimationFrame` 合并，按 12/8/6 栏和 8 行吸附后直接写入 `--er-pointer-x/y`，不逐帧触发 React state；resize 会重新量化，卸载会取消 RAF 和监听。粗指针设备隐藏该装饰层。

### 6.6 视觉迁移业务边界

- React Router 路径、页面懒加载、`api/` 请求封装、请求参数、响应类型和后端状态码契约不因视觉迁移改变；迁移不新增接口，也不让 CSS 或装饰层发起网络请求。
- 首页继续通过 `listPapers`、`listWorkspaces` 读取真实数据，通过 `uploadPaperWithProgress` 上传 PDF，并在成功后进入 `/reader/:id` 的自动分析流程。文件类型、50 MB 上限、上传进度和错误反馈继续由上传组件及页面状态处理。
- Reader 的 PDF、解析、笔记、标签和标注，Code 的仓库与终端，Research 的图谱、SSE 与 RAG，以及系统 WebSocket 仍由原有页面、feature、hook 和 API 模块管理；呈现层只消费其既有 DOM 状态与 CSS 类。

## 7. 深度研究评测验收（P9）

离线 mock 评测位于 `backend/tests/evaluation/deep_research/`，文档见 `docs/DEEP_RESEARCH_EVAL.md`。

```bash
export EVAL_ALLOW_NETWORK=0
python -m backend.tests.evaluation.deep_research.eval.cli run --mode mock
python -m backend.tests.evaluation.deep_research.eval.cli gate --mode mock
python -m backend.tests.evaluation.deep_research.eval.cli compare \
  backend/tests/evaluation/deep_research/baselines/mock_v1.json \
  backend/tests/evaluation/deep_research/reports/mock_gate_report.json
pytest backend/tests/test_deep_research_eval.py -q
```

- CI `eval-fast` job 强制执行 mock gate（`EVAL_ALLOW_NETWORK=0`）。
- mock 门禁仅保证相对冻结基线不回归；真实发布需人工 Scholar 盲测（见文档），禁止自动爬取对照结果。
- 真实 30+ seed、至少 3 个子领域、双人标注与 Scholar 盲测完成前，`discovery_v2` 默认关闭。
