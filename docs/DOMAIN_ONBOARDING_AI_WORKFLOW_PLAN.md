# 深度研究升级计划：面向学者冷启动的「AI 领域全景透视工作流」

> **文档版本**：v1.4（已实施）  
> **创建日期**：2026-09-01  
> **状态**：已实施（Implemented — 见文末「实施记录」）  
> **目标模块**：`backend/services/deep_research/`、`frontend/src/features/research/`
> **总目标锚点**：见根目录 `GOAL.md`（毕设最高优先级指导文档）

---

## 1. 背景与问题定义（Executive Summary & Context）

### 1.1 校园网机构订阅直连与跨学科能力现状
在本次前期改造中，系统已成功打通**山东大学（SDU）校园网机构订阅数据库**与 **Elsevier 官方 Scopus 核心检索库**：
- **覆盖顶级出版商**：Elsevier (ScienceDirect/EV)、SpringerLink、Nature Portfolio、Wiley Online Library、ACS (American Chemical Society)、Taylor & Francis、Europe PMC；
- **全学科支持**：突破传统仅支持计算机/arXiv 的局限，覆盖生化环材、临床医学、工程制造、经管社科等跨领域文献检索与免密机构下载；
- **级联回退保障（Cascade Fallback）**：构建了“机构直连 $\rightarrow$ Europe PMC $\rightarrow$ Unpaywall $\rightarrow$ OpenAlex $\rightarrow$ CrossRef $\rightarrow$ 学术着陆页 Meta 嗅探”多级容错流水线，已通过全套 392 项自动化测试。

### 1.2 “深度研究”的核心使命：学者冷启动（Zero-to-One Domain Onboarding）
深度研究面向的最核心科研场景是：
> **“当学者完全不了解某个全新领域时，只需输入 1 个概念或 1 篇种子论文，系统自动检索、计算并扩展该领域全局文献，提炼出前沿热点、演进脉络与主流技术流派，帮助学者在不必逐篇精读具体论文的前提下，快速建立起对该领域的系统性认知。”**

### 1.3 现有可视化图谱的核心缺陷：信息严重稀释（“毛线球”困境）
目前深度研究的可视化结果存在明显的学术信息稀释问题：
1. **节点全是无差别的圆圈**：仅靠大小反映 PageRank 或被引量，学者完全看不出该论文在领域里是**开山鼻祖、突破黑马、评测基准还是工程库**；
2. **连边全是模糊的细线**：仅表达“有引用关系”，未指示是**技术改进、流派竞争、Baseline 对比还是跨界借鉴**；
3. **物理排布杂乱无章**：力导向物理引擎生成的浮动圆球缺乏时间演进感与流派分区，学术信息密度极低。

---

## 2. 核心架构设计：算法拓扑 + AI 认知的双引擎协同

纯算法（图论/统计）只能处理“结构与数字”，无法理解“学术语义与因果”；纯大模型又极易产生文献幻觉。因此，我们确立 **「纯算法做骨架，AI 注入灵魂」** 的协同架构：

```mermaid
flowchart TD
    subgraph 纯算法阶段 (低成本/抗幻觉/秒级剪枝)
        A[30~50 篇真实文献池] --> B[8 通道多维关联度算法 graphkit]
        B --> C[PageRank 权威度计算]
        B --> D[Louvain 社区流派划分]
        B --> E[Disparity Filter 骨架剪枝]
        C & D & E --> F[提炼出 10~15 篇核心骨干图谱拓扑]
    end

    subgraph 新增: AI 领域全景认知工作流 (stage_synthesize)
        F --> Task1[Task 1: 节点学术角色研判 & 一句话极简卡片]
        F --> Task2[Task 2: 连边意图挖掘 & 学术因果关系标注]
        F --> Task3[Task 3: 技术流派归纳 & 优缺点对比提炼]
        Task1 & Task2 & Task3 --> Task4[Task 4: 生成「领域全景透视简报」Domain Briefing]
    end

    subgraph 前端高密度呈现 (免读单篇即懂全局)
        Task1 & Task2 --> GraphView[语义图谱: 角色徽章 + 因果关系边 + 演进时间线]
        Task3 & Task4 --> ExecutivePanel[领域全景透视专属面板: 3 分钟读懂领域全局]
    end
```

---

## 3. AI 认知工作流（`stage_synthesize`）详细技术规范

在深度研究流水线 `pipeline.py` 中，在 `stage_graph` 之后新增独立阶段 `stage_synthesize`。

### 3.1 Task 1：节点学术角色研判与极简一句话提炼
* **输入**：论文 Title, Abstract, Year, Scopus Citation Velocity, PageRank, Degree。
* **LLM 研判输出**（直接丰富至节点对象）：
  1. `role`: 学术角色分类
     - `landmark`（🏛️ 奠基基石）：高历史被引、领域开山之作；
     - `rising_star`（🔥 前沿黑马）：近 1~2 年发表、引用斜率陡峭、当前焦点；
     - `benchmark`（📊 标准基准）：提出公认 Dataset/Benchmark 的代表文献；
     - `tool_sota`（🛠️ 开源工具/工程标杆）：高星开源实现、落地代码代表；
     - `survey`（📖 权威综述）：跨分支引用的全局全景总结；
  2. `short_takeaway`：**一句话核心创新**（不超过 25 字，如：“*提出选择性状态空间 (S6)，实现长序列线性推理*”）；
  3. `key_techniques`：2~3 个核心方法标签（如 `["SSM", "Linear Complexity", "Hardware-Aware"]`）。

### 3.2 Task 2：连边意图与学术因果关系挖掘
* **输入**：骨干连边（Paper A $\rightarrow$ Paper B）及引用上下文句子（Citation Context）。
* **LLM 研判输出**（直接丰富至连边对象）：
  1. `relation_type`: 关系类型
     - `improves`（➡️ 改进与优化）：在 A 的基础上改进了计算复杂度、精度或训练稳定性；
     - `replaces`（⚔️ 范式替代/竞争）：使用截然不同的流派方案解决 A 针对的同一痛点；
     - `evaluates_on`（📏 基准评测）：将 A 作为实验 Baseline 或测试数据集；
     - `inspires`（🔗 跨界借鉴）：引入 A 的跨学科理论并做领域迁移；
  2. `relation_summary`：一句话因果说明（如：“*重构了 A 的注意力机制，解决了长文本显存二次方爆炸问题*”）。

### 3.3 Task 3：技术流派（Paradigms）归纳与竞争格局
* **输入**：Louvain 算法聚类出的各个论文簇（Clusters）。
* **LLM 研判输出**：
  1. `paradigm_name`：流派具象化名称（如：“*状态空间模型 (SSM) 路线*”）；
  2. `core_philosophy`：核心哲学思想（一句话概括）；
  3. `pros_and_cons`：优势（如推理极快）与软肋（如联想记忆较弱）；
  4. `representative_paper_ids`：该派系的 2~3 篇核心代表作。

### 3.4 Task 4：生成「领域全景透视简报」（Domain Executive Briefing）
* **AI 生成一份免读单篇论文的高质量 Markdown 综述**，结构包含：
  1. **【领域本质与核心矛盾】**：该方向到底在解决什么科学/工程痛点？
  2. **【技术演进时间轴（Timeline）】**：按年份列出里程碑事件与技术范式转移；
  3. **【主流技术路线横向对比表】**：各流派在计算复杂度、精度、适用场景的表格对比；
  4. **【标准评测体系】**：公认必测的 Benchmark、Baseline 与主要指标；
  5. **【近 1 年爆发热点】**：最新顶会/期刊最密集的攻坚方向；
  6. **【未解决的公开挑战（Open Problems）】**：当前共有痛点，直接为学者启发论文创新点。

---

## 4. 数据模型与前后端契约规范

### 4.1 后端模型扩展（`backend/models/research.py`）

```python
class NodeSemantic(BaseModel):
    role: str  # landmark | rising_star | benchmark | tool_sota | survey | regular
    short_takeaway: str  # 一句话创新点
    key_techniques: list[str] = []

class EdgeSemantic(BaseModel):
    relation_type: str  # improves | replaces | evaluates_on | inspires | cites
    relation_summary: str  # 一句话关系因果

class ParadigmInfo(BaseModel):
    cluster_id: int
    name: str
    core_philosophy: str
    pros: list[str]
    cons: list[str]
    representative_paper_ids: list[int]

class ExecutiveBriefing(BaseModel):
    domain_problem: str
    milestone_timeline: list[dict]
    paradigms_comparison: list[dict]
    benchmark_standards: list[str]
    recent_hotspots: list[str]
    open_challenges: list[str]
    markdown_report: str
```

在 `ResearchGraph` 中持久化 `synthesis` 字段：
```python
class ResearchGraph(SQLModel, table=True):
    ...
    synthesis: Optional[dict] = Field(default=None, sa_column=Column(JSON))
```

### 4.2 SSE 实时流式事件扩展
在深度研究运行过程中推送：
1. `stage_start: {"stage": "synthesize"}`
2. `synthesis_ready: {"briefing": {...}, "paradigms": [...], "enriched_nodes": {...}, "enriched_edges": {...}}`
3. `stage_done: {"stage": "synthesize"}`

---

## 5. 前端可视化与交互重构规范

### 5.1 节点形态与视觉分级
* **徽章标识**：节点圆圈左上角或顶部浮动显眼的图标徽章（🏛️ 奠基 / 🔥 黑马 / 📊 基准 / 🛠️ 工具 / 📖 综述）；
* **高信息密度卡片**：节点直接渲染小字标签：`[年份 · 简称 · 一句话核心创新]`，无需逐个点击放大；
* **悬浮卡片（Hover Tooltip）**：展示“解决痛点”、“核心招式”、“实验效果”。

### 5.2 语义连边与因果箭头
* 连边带有**方向箭头**与**颜色编码**（绿色=改进继承，橙色=竞争替代，蓝色=评测基准，紫色=跨界借鉴）；
* 鼠标悬浮连边时，弹出悬浮框显示具体的因果说明。

### 5.3 三大多维结构化视图（多视角一键切换）
1. **【编年史演进时序图（Timeline / River View）】**：
   - X 轴为年份（2018 $\rightarrow$ 2026），Y 轴为技术流派；
   - 节点按发表年份从左到右水平排布，连边呈现技术水流般的汇聚与演进；
2. **【流派阵营泳道图（Paradigm Swimlanes）】**：
   - 自动生成 3~4 个卡片盒泳道，各派系代表作聚在盒内，盒头直接展示流派优劣势对比；
3. **【骨干地铁路线图（Subway Map）】**：
   - 隐藏次要旁支，只展示 8~10 篇关键主干，标注新手必读路线。

### 5.4 专属面板：「领域全景透视（Executive Briefing）」
* 在深度研究页面增加一个独立的 **「📜 领域全景透视」** Tab/Drawer；
* 格式化渲染 AI 生成的结构化报告，学者进入工作区第一眼阅读此报告，3 分钟通晓领域全局。

---

## 6. 分阶段实施路线图（Implementation Roadmap）

| 阶段 | 核心任务与交付物 | 预计耗时 |
| :--- | :--- | :--- |
| **Phase 1: 后端 AI 认知引擎** | 1. 编写 `backend/services/deep_research/domain_synthesizer.py`<br>2. 接入 LLM 结构化抽取（角色、因果边、流派、综述简报）<br>3. 在 `pipeline.py` 中接入 `stage_synthesize` | 1 天 |
| **Phase 2: 数据存储与契约打通** | 1. 扩展 `ResearchGraph` 数据库模型与 Schema<br>2. 完善 SSE 流式推送协议与 API 路由返回 | 0.5 天 |
| **Phase 3: 前端图谱高密度重构** | 1. 前端 `GraphView` 升级支持角色徽章与卡片化外露<br>2. 升级带方向与颜色编码的语义连边组件<br>3. 实现悬浮即看三句话创新点 | 1 天 |
| **Phase 4: 全景简报面板与时序视图** | 1. 实现前端「📜 领域全景透视」专属 Markdown 综述面板<br>2. 新增【技术演进编年史（Timeline）】水平时序布局模式 | 1 天 |
| **Phase 5: 全套测试与验收** | 1. 编写后端 unit test (`test_domain_synthesizer.py`)<br>2. 端到端测试跨学科真实领域（生化环材、医学、大模型等）冷启动效果 | 0.5 天 |

---

## 7. 审查确认与后续开发

本规划方案已将本次对话中关于**校园网机构订阅直连**、**跨学科多源解析**、**冷启动免读单篇的科研痛点**以及**信息可视化重构方案**完整集合。

---

## 8. 实施记录（v1.1，2026-09-01）

评审后按修订路线完成开发，与 v1.0 原案的差异均已固化在代码与测试中：

### 已交付
- **Phase 0 数据铺垫**：召回候选的 `abstract` + `citation_count` 随 `PaperWork` 落盘到
  `Paper` 表（`stages/expand.py` → `paper_identity.apply_identity_to_paper`），节点 meta 带 `citation_count`。
- **Elsevier/Scopus 首选源**：新增 `providers/scopus.py`（Scopus Search / Abstract Retrieval REF 视图 /
  REF() 正向引用；推荐通道无免费接口返回空）。`research.provider` 默认 `auto`：检测到
  `elsevier_api_key`（config 或环境变量）即首选 Scopus；ScopusProvider 连续鉴权失败内置熔断，
  `FailoverProvider` 透明回退 OpenAlex——扩展不会因无效 Key 饿死。设置页可选 auto/scopus/openalex/s2/local，
  elsevier_api_key 走 GET 掩码 / POST 保留的密钥通道。
- **stage_synthesize**（`domain_synthesizer.py`）：Task1 节点角色 / Task2 引用因果 / Task3 流派 /
  Task4 全景简报。复用 ai_analyzer 的重试与 JSON 修复；批量化调用（默认 15 节点/批、20 边/批，上限 60/60，
  `research.synthesize.enabled` 开关）；无 Key 跳过、单任务失败保留 partial 产物。
- **契约**：`ResearchGraph.synthesis` JSON 列（含迁移）；`GET /workspaces/{id}/graph` 返回 synthesis；
  SSE 新增 `stage_start/stage_done: synthesize` 与 `synthesis_ready`（done run 回放同样携带）；
  `reference_backfill.rebuild_workspace_graph` 重建图谱时同步重建语义层。
- **前端**：节点角色徽章（🏛️🔥📊🛠️📖，油墨专色描边）+ 高倍缩放下外显一句话创新；
  cites 边按因果着色（改进=石绿/替代=朱红/评测=赭石/借鉴=绛紫）+ 悬浮与详情面板因果块；
  「📜 领域全景」抽屉（BriefingPanel：核心矛盾 / 演进时间轴 / 流派卡片含优劣势与代表作跳转 /
  评测体系 / 热点 / 公开挑战 / Markdown 综述）；`synthesis_ready` 到达即合并进图谱无需重建。

### 与 v1.0 原案的刻意偏差（评审结论）
1. **Task 2 只研判 cites 边**：related 边是无向主题相似，无引用证据，不猜因果（防幻觉）。
2. **Task 1 输入降级为「召回摘要或速读总结」**：Scopus 引用速度（velocity）无历史数据点，
   以被引数 + 年份 + PageRank 替代；摘要来自 Phase 0 落盘。
3. **砍掉泳道图 / 地铁路线图**：v1 只做力导向图语义增强 + 全景简报面板（Timeline 视图留 v2）。
4. **结构化子模型**：`BriefingMilestone` 等替代 `list[dict]`；流派代表作统一用节点 id `paper:{id}`。
5. **工期**：后端引擎 + 契约 1.5 天、前端 1 天（三视图砍掉后成立）。

### 验证
- 后端 404 项测试全过（含 `test_domain_synthesizer.py` 12 项：无 Key 跳过 / 仅 cites 边 /
  枚举越界归一 / partial 容错 / 全失败返回 None）。
- 前端 typecheck + 83 项测试全过；临时库迁移冒烟（papers.abstract / citation_count /
  research_graphs.synthesis）通过。
- Scopus 真实 API 冒烟：本机（校外网）Key 401 → 3 次熔断 → 自动回退 OpenAlex 正常召回。

---

## 9. 实施记录（v1.2，2026-09-01）：领域总览首屏（信息集中化）

用户反馈 v1.1 后的核心痛点未根除：图谱骨架按「目标平均度 8」留边，百篇论文即 400+ 条
关系边，节点-连线布局仍是一张毛球（Ghoniem et al. 2005 的对比研究结论：该密度下
node-link 可读性崩塌）。v1.2 把可视化拆成「总览层」与「关系层」，**总览为默认首屏**，
力导向图降级为可切换的细节视图。

### 已交付（纯前端，零后端改动）
- **`features/research/domainAggregates.ts`**（纯函数、可单测）：按 `meta.cluster` 分桶、
  按 `meta.year` 计年、related 边去重后计算 Callon 密度（簇内平均边权）与中心性
  （篇均外部关联）、近三年占比锚定数据 maxYear、代表作按 synthesis 顺序 + importance 补齐。
  范式名优先级：synthesis > TF-ICF 簇标签 > 编号兜底——**不依赖 LLM key**。
- **`features/research/DomainOverview.tsx`** 三个互补视图回答入门三问：
  1. **子方向 × 年份热力图**（CSS grid）：格子深浅 = 该流派当年论文数，悬停显详情，
     行头点击查看代表作——"领域热度何时烧到哪里、现在聚焦什么"；
  2. **战略坐标四象限**（SVG）：横轴跨流派中心性、纵轴簇内密度，分割线取有效子方向中位数，
     四象限标注 核心引擎 / 内聚深耕 / 跨域桥梁 / 边缘方向，气泡大小 = 论文规模，
     近三年占比 ≥45% 加 🔥——"谁是核心、谁在上升"；
  3. **子方向卡片**：synthesis 可用时带理念 / 优劣势 / 代表作（角色徽章 + 年份 + 被引），
     「在图谱中查看」跳转图谱视图并选中节点。
  颜色与图谱同源（`clusterInk(簇号)`），同一子方向两视图颜色一致。
- **ResearchPage**：`📊 总览 / 🕸️ 图谱` 视图切换（总览默认），EdgeFilter 仅图谱视图显示。

### 设计决策
- 热力图选「子方向 × 年份」而非论文×论文邻接矩阵：前者 O(流派数×15) 直接回答宏观问题，
  后者在 200+ 篇时格子过小且语义稀薄（矩阵留作后续的关系深挖二级视图）。
- 聚合全部前端计算：图谱持久化字段（cluster/year/citation_count/importance/边权）已足够，
  不引入新的后端契约；synthesis 缺失时优雅降级为 TF-ICF 关键词标签。

### 验证
- 前端 typecheck + 94 项测试全过（新增 `domainAggregates.test.ts` 8 项契约：
  分桶排序 / 名称优先级 / 年份裁剪 / 边去重 / 近三年窗口 / 代表作顺序；
  `DomainOverview.test.tsx` 3 项渲染与交互）。
- 生产构建通过。

---

## 10. 实施记录（v1.3，2026-09-01）：领域探索——问句驱动的免读领域认知

GOAL.md 核心命题的直接实现：学者提一个自然语言问句（如「用 AI 识别肿瘤有没有人做，
做得怎样」），系统自动完成 拆解 → 检索 → 计量 → 综合，全程不读单篇论文、不依赖模型记忆。

### 已交付
- **后端流水线 `deep_research/explorer.py`**（独立于工作区 pipeline，问句冷启动）：
  1. **decompose**：LLM 把口语问题拆成英文学术检索式 + 问句意图（novelty/maturity/landscape）；
     无 Key / 调用失败 → 原句直检回退（`fallback: true` 全链路透传）；
  2. **recall**：复用 provider 层 `search_works`（Scopus 首选 / OpenAlex 熔断回退），
     canonical_key 去重合并（被引取大、字段互补），超量裁剪预留 1/4 席位给近三年新工作；
  3. **metrics + cluster**（纯算法，客观性来源）：引用速度 = 被引/年龄、突增 = 近窗口+高速度、
     年份直方图；标题+摘要 TF-IDF 词法相似图 → Louvain 社区 → **与深度研究图谱同一套
     分词/社区/TF-ICF 标签口径**（复用 `graph_analytics`），孤立点 cluster=-1；
  4. **synthesize**：LLM 只基于召回材料——查新判定（五档枚举 + 证据 E 编号回链）、
     实现程度、流派（复用 domain_synthesizer 的范式任务）、热点/空白/后续检索式、Markdown 报告；
     无 Key / 证据 <3 篇自动跳过，统计视图照常可用；
  5. **铁律护栏**：回退检索（查全率不可信）下 LLM 判「gap」会被强制降级为「unclear」
     并压低置信度——空白结论必须与检索失败区分（真实冒烟中发现并修复）。
- **契约**：`ExplorationRun` 表（question/depth/status/decompose/result JSON 列，create_all 自动建表）；
  SSE 事件 `stage_*` / `recall_progress` / `metrics_ready` / `synthesis_ready` / `done`（独立事件总线
  注册表，与 research run 的 id 空间隔离）；done run 二次订阅回放产物。
- **路由 `/api/exploration`**：POST /runs（并发互斥 409）、GET /runs（历史轻量列表）、
  GET /runs/{id}、DELETE /runs/{id}、GET /runs/{id}/stream（15s 心跳）。
- **前端 `/explore` 领域探索页**（导航「领域探索」，register SCOUT）：
  - 提问表单（深度档位 快览/标准/深挖 = 3/4/5 式 · 80/160/240 篇上限）+ 示例问题 + 历史记录；
  - 五阶段进度条（拆解→检索→计量→综合→落盘）；
  - **结论卡**：五档查新判定徽章 + 证据充分度条 + 判定依据 + 可点击的支撑证据；
  - **检索透明度卡**：实际使用的检索式 / 数据源 / 去重后总数 / 年份跨度 / 近 2 年占比 /
    回退警告——把「系统性」变成可见的界面元素（对比网页问 GPT 的核心差异）；
  - **领域总览**：直接复用 v1.2 的 `DomainOverview`（热力图 + 战略坐标 + 范式卡片），
    探索产物按同构约定组装成 ResearchGraph 形状（`graph.nodes[i] ↔ papers[i]`）；
  - **证据列表**：🏛️ 高被引经典 + 🔥 近期上升（按速度），行点击展开证据详情卡
    （摘要 / 被引 / 速度 / DOI 原文链接）；
  - 热点/空白 chips + 「建议下一步」一键填入提问框 + Markdown 报告。
- **文档治理**：新增根目录 `GOAL.md`（总目标/铁律/文档地图）；删除已被取代的
  `DEEP_RESEARCH_REDESIGN.md`（v3.0.0 明确取代）与 `SPECIFICATIONS.md`（AGENTS.md 架构概览的
  旧版重复）；AGENTS.md 挂 GOAL.md 引用。

### 设计决策
- **探索 ≠ 深度研究**：探索从问句冷启动、纯元数据 + LLM 综合轻链路（无 PDF 下载/GROBID/向量化），
  数分钟内出结论；学者想深挖时再进工作区深度研究。两者共享 provider / 聚类口径 / 总览可视化。
- **产物即图谱**：探索结果组装成 ResearchGraph 形状而非新契约，v1.2 总览视图零改动复用。
- **LLM 输出的枚举与证据在归一化层硬校验**：越界档位归 unclear、不存在的证据编号丢弃、
  置信度夹紧——模型永远不能发明第五种结论或引用不存在的论文。

### 验证
- 后端 418 项测试全过（新增 `test_explorer.py` 14 项：拆解回退/检索式消毒、去重合并/近期席位、
  速度与突增锚定 now_year、双主题聚类+孤立点、图谱形状、无 Key/证据不足跳过、枚举置信度证据
  归一化、gap 降级护栏、stub provider 端到端两例）。
- 前端 typecheck + 98 项测试全过（新增 `ExplorePage.test.tsx` 4 项：表单提交、成品视图全要素+
  证据点开、降级态、失败态）；生产构建通过。
- 真实 API 冒烟：OpenAlex 召回 21 篇 + deepseek 综合产出完整 verdict/流派/报告；
  并暴露「回退检索下 gap 误判」问题 → 已加护栏并用单测锁死。

### v1.3.1 迭代（2026-09-01）：Scopus 主源工程——自适应限速与熔断回退加固

应用户要求把 Elsevier/Scopus 固化为检索主源（山大机构订阅），免费源仅做兜底：

- **auto 档无条件 Scopus 首选**（`providers/__init__.py`）：有 Key 用 Key，无 Key 走
  校园网机构 IP 直连；鉴权失败熔断后透明回退 OpenAlex。校外无 Key 环境付出约 2 次快速
  401（约 2s）后照常回退，不为少数环境牺牲主源质量。用户运行时配置已切回 auto。
- **AdaptiveThrottle**（`rate_limiter.py`）：付费库防 429 + 放开并行的自适应限速——
  请求间隔在 [floor, cap] 随成败自适应（成功 ×0.8 收敛到 0.12s ≈ 8 req/s；429 尊重
  Retry-After 并 ×1.6 降速），信号量封顶并发 4；跨任务发射槽保证间隔下限。
  ScopusProvider 弃用旧的 0.6s 串行间隔接入；**探索召回的各检索式改为并行发射**。
- **检索质量过滤（排除干扰）**：Scopus 追加 `DOCTYPE(ar OR cp OR re OR sh OR ip)`、
  OpenAlex 追加 `type:article|review|preprint|book-chapter`（保留 arXiv 预印本），
  剔除 letter/note/erratum/editorial 噪声类型；检索式消毒剥离模型夹带的布尔语法
  （TITLE-ABS-KEY/AND/OR，防二次包裹错乱）。
- **熔断回退加固**（真实冒烟发现）：并行下多条查询可能同时落在刚熔断的主源上拿到
  空结果——① 熔断阈值 3→2（401 对同一 Key+IP 是确定性的）；②
  `FailoverProvider.search_works` 在 primary 中途死亡时当场改走备用源重试；
  ③ 召回层对落空检索式整批改走备用源补捞。`aclose` 显式关闭双方连接。
- **验证**：后端 431 项测试全过（新增：AdaptiveThrottle 收敛/封顶/并发/间隔 5 项、
  质量过滤与 auto 首选策略 5 项、并行召回与熔断恢复 3 项）；前端 typecheck + 98 项全过。
  真实端到端冒烟：本机出口非校园网且存量 Key 失效 → Scopus 2×401 熔断 → 透明回退
  OpenAlex（academic 过滤）→ 拆解出干净英文检索式 → 召回 73 篇高相关文献
  （CAMELYON16 / BRATS / TransUNet / MedSAM 均被点名）→ 正确判定
  「已被充分实现 · 置信 0.95」——对比迭代前同问题因召回垃圾误判「gap」。
- **已知边界（2026-09-02 校园网实测校准）**：出口确认为山大教育网（219.231.160.1，
  山东威海）后复测——① Scopus Search **无 Key 仍 401**：检索 API 只认 API Key，校园机构
  IP 不放行（此前「山大 IP 可免 Key 直连」的假设是错的，代码与设置页文案已纠正；
  auto 无 Key 改为直接 OpenAlex，不再白付 2×401）；② ScienceDirect Article Retrieval
  **无 Key 200**：校园 IP 权益覆盖全文下载通道，直链下载工程直接受益——但本机 TUN
  全局代理会把流量送出海外出口吞掉该权益，学术域名需加直连规则；③ 存量 Elsevier Key 的 401
  **实为认证头名错误（见 v1.3.2），Key 一直有效**，修正后 Scopus 主源即真实生效。
  近期占比（4%）偏低提示 OpenAlex 相关性排序偏经典，后续可加「按年份排序的近期式」
  提升热点新鲜度。

### v1.3.2 排障记录（2026-09-02，校园网实测）：Scopus 主源四个连环坑

用户切到山大校园网后端到端验证，暴露并修复了四个真实缺陷（前三个都会让有效 Key
看起来像"无效"，曾导致 v1.3.1 误判用户 Key 失效——**Key 实际一直有效**）：

1. **认证头名错误**：`X-APIKey` → Elsevier 只认 `X-ELS-APIKey`（或 `apikey` 参数）。
   错头名时有效 Key 也返回 401 "Invalid API Key"，与真无效无法区分。已修 + 回归测试。
2. **整串引号 = 精确短语**：`TITLE-ABS-KEY("a b c d")` 要求逐字连续出现（6 词短语
   0 命中）；去引号后 loose AND（同查询 2058 命中）。`build_search_query` 已改。
3. **单页上限 25**：`count>25` 直接 HTTP 400（standard 档 per_query=40 全灭的根因）。
   `search_works` 改为 25/页按 `start` 翻页补齐。
4. **日期字段名**：响应只给 `prism:coverDate`（请求 publicationDate 也不给），年份
   解析改读 coverDate；in-press 的占位未来年份由 compute_metrics 的 now+1 守卫兜住。
5. **附带**：DeepSeek（推理型）空回复三连修——content 空时回退取 `reasoning_content`、
   `_llm_json` 把 JSONDecodeError 视为可重试、JSON 调用 max_tokens 全面提额 4000
   （hidden reasoning 与 content 共享预算且长度方差 227~3075，预算不足时 content 被截空）。

**最终验收**（真实校园网 + 真实 Key）：「用 AI 识别肿瘤有没有人做」standard 档 →
provider=scopus，去重后 160 篇、年份覆盖 160/160、近两年占比 51.2%（OpenAlex 回退链路
仅 4%——Scopus 相关性排序对近期文献显著更友好，热点新鲜度问题随之消解）、6 个子方向、
rising 榜（2025 multi-omics 速度 42/年 🔥）、verdict=done_extensively conf 0.95、
全程约 2 分钟。后端 435 项测试全过（新增头名/分页/年份三个回归锁）。

### v1.4 迭代（2026-09-02）：文献计量学升级——让「哪些论文更重要」有出处

用户提问「Elsevier/山大订阅有没有更靠谱的统计指标指认重要论文」，先做**权限实测**
再按实测结果实施（结论同时存入记忆 `project-elsevier-api-entitlements`）：

**权限实测（个人 dev Key + 校园网 IP）**：
- ✅ Serial Title API：期刊 SJR / SNIP / CiteScore 可用；`?source-id=a,b,c` 逗号分隔
  **批量**（实测 3 刊一次返回；Nature SJR 19.713 / SNIP 10.607 / CiteScore 77.7）。
- ✅ Scopus Search 可请求 `source-id,prism:publicationName`（期刊身份零额外调用）。
- ❌ Citation Overview / Citation Count API：纯数字 scopus_id 正确传入仍 404
  RESOURCE_NOT_FOUND——逐年引用曲线属机构订阅级 entitlement，个人 Key 不放行。
- ⚠️ Abstract Retrieval：`view=FULL` 返回空壳（无权限）；field 参数版只给 idxterms，
  `dc:description`（摘要）与 `authkeywords` 均不给 → **摘要/关键词统一改走 OpenAlex**。
- 替代结论：OpenAlex work 自带 `counts_by_year`（免费、引用计数含预印本，对 CS 前沿
  更灵敏；同一篇 ResNet 两边总量 229184 vs 227875，口径差异属正常）。Scopus 与 OpenAlex
  的正确定位是**分工**而非谁强谁弱：前者人工经营（期刊遴选/受控索引/检索相关性），
  后者开放动态（逐年引用免费/预印本一等公民）——本系统 Scopus 管召回与权威度，
  OpenAlex 只取引用动态与覆盖面补充。

**实施（探索流水线新增 enrich 阶段，全部非致命）**：
1. **同年百分位（ESI 高被引的语料内复刻）**：按发表年份分 cohort 算被引百分位——
   年龄无关的公平指标（老论文不再天然占优）；召回语料只是领域抽样，绝对 top 1% 噪声
   大，取**同年语料前 5% 且 cohort≥5** 打 `highly_cited` 徽章。
2. **近 2 年被引增量 + 引用轨迹**：OpenAlex 按 DOI 逐篇补 `counts_by_year`（并发 8），
   得 `recent_citations` / `citation_velocity_2y`；突增判定优先用近 2 年速度（老论文
   全周期速度会高估当前动量），缺数据退回全周期速度。轨迹 = 近 2 年均速 vs 之前 2 年
   （发表满 4 年才定型：rising ≥1.5× / declining ≤0.5× / 其间 steady）。
3. **期刊级指标**：Scopus 检索带回 source-id → Serial Title 批量查 SJR/SNIP/CiteScore
   挂到论文与「主阵地期刊」榜（语料内 ≥2 篇才上榜）；OpenAlex-only 期刊按 ISSN 补查。
4. **摘要/关键词富化**：同一次 OpenAlex DOI 查询带回摘要（Scopus 路径此前只有标题
   可聚，聚类与 LLM 材料质量大幅提升）与 keywords（**score 是 0~1 标度**，实测阈值
   3.0 全滤空 → 改 0.5 + 学科级泛化词停用表）→ **关键词×年份热力图**（GOAL 承诺的
   热力图兑现；≥3 篇才入图，Top 20 词）。
5. **LLM 材料同步升级**：证据行加 近2年被引/同年前5%/引用爬升·回落/期刊名，检索概况
   加主阵地期刊与高频关键词近两年计数——LLM 的热点/空白结论锚定在更硬的统计上。

**踩坑记录**：① keywords score 标度搞错（首轮冒烟 0/155 篇有关键词，阈值修正后 95+）；
② 摘要覆盖率上来后 synthesize 主任务的 markdown 输出被 4000 max_tokens 截断（推理模型
hidden reasoning 共享预算，重试全灭，run 14 verdict 丢失）→ 探索综合提额 8000 后恢复
（run 15 verdict done_extensively 0.85、零重试）。paradigms 子任务存在偶发模型方差失败
（run 14 成功 5 个 / run 15 失败一次），非致命降级，设计内。

**验收**（真实 Key 三轮冒烟，标准档「多模态大模型医学影像诊断」）：
- run 15（全绿）：115 篇（去重后），摘要 87、keywords 95、SJR 97，关键词热力图
  16 词×5 年（Medical imaging Σ23 其中近 2 年 15——曲线形态与领域爆发期一致），
  主阵地期刊 IEEE JBHI（4 篇 SJR 1.624）等，highly_cited 7（2026 放射学基础模型
  综述同年 P99.4），surge 41，verdict done_extensively 0.85 且点名证据。
- run 14：SJR 117/155、摘要 117、关键词热力图 20 词×13 年，用于验证 keywords 修复。
- 后端 446 项测试全过（新增 11 项：百分位/轨迹/突增偏好/期刊榜/关键词矩阵/去重合并
  extra/enrich 非致命/Serial Title 分块解析/检索携带期刊身份/OpenAlex 动态字段）；
  前端 typecheck + 98 项 + build 全过。
