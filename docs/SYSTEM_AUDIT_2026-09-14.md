# 中期系统体检报告（2026-09-14）

> 范围：全仓库只读审计，不含任何源码修改。审计对象为当前工作树状态（含未提交的
> checkpoint/resume 批次与 v7.3.0 设置页批次）。
> 方法：4 路并行深探（deep_research 模块 / 后端核心 / 前端 / 安全专项）+ 全局静态扫描
> （ruff F401/F811/F841/E711/E712/E722、TODO 标记、git 卫生），所有 P1 结论均经二次人工复核。

## 一、总览

| 维度 | 数据 |
|------|------|
| 后端规模 | ≈5.6 万行 Python（不含测试，不含虚拟环境） |
| 前端规模 | ≈2.7 万行 TS/TSX |
| 最大文件 | explorer.py 2323 / analysis.py 1544 / survey_report.py 1464 / ai_config.py 1169 / exploration.py 1139 / database.py 1011；前端 DeepResearchSettings 1249 / types/index.ts 1074 / ExplorePage 1040 / PdfViewer 1012 |
| 静态扫描 | ruff 死代码信号 83 项（76 未使用导入 + 7 未使用变量），66 项可自动修复 |
| 环境卫生 | 双虚拟环境（根 `.venv` 1.4G + `backend/.venv` 1.3G）；4 个前端构建产物未被 gitignore；`draft` 任务草稿被 git 跟踪 |

**质量分层结论**：`services/llm/`、`llm_proxy/`（统一 LLM Core 与合规代理）以及新证据层
（document_evidence → report_evidence → stage_agent → evidence_audit）分层清晰、护栏设计
（quote 字面校验、CAS 状态机、密钥脱敏）质量最高。正在"变烂"的是三处周边：
**explorer.py / survey_report.py 两个编排上帝文件**、**ai_config.py 迁移期双真源**、
**analysis.py 域混杂路由**。测试纪律良好（70 个测试文件无 skip/xfail）。

---

## 二、P1 —— 正确性缺陷（建议本批次修复）

### P1-1 报告子问题硬编码单一领域，`question` 入参被完全忽略
- 证据：`backend/services/deep_research/document_evidence.py:218-235`；调用点 `survey_report.py:227,334,583`
- `report_subquestions(question)` 的 `question` 零引用，返回写死的五条子问题，query 为
  `"Model Mixer Params MACs img/s Acc ImageNet ..."` 等针对图像分类效率综述的词表。
  选材、逐篇检索、证据索引、覆盖度全部消费这份表。
- 影响：换任何其他研究问题，"按子问题分页读证据"与覆盖结论系统性指向错词表，且使用者无从察觉。
- 方向：由 LLM 从 question 生成子问题与证据词表（缓存进 result），硬编码项仅作 fallback。

### P1-2 召回覆盖维度表硬编码 CV 领域，其他领域静默失效；含拼写错误
- 证据：`backend/services/deep_research/recall_engine.py:222-247`（`"clnic"` typo 在 232 行）
- `_COVERAGE_DIMENSIONS` 只含图像分类/检测/分割等触发词，无匹配即返回空清单（250-257），
  `_uncovered` 与补搜条件随之恒空；`"clnic"` 永远匹配不到 "clinic"。
- 影响："缺什么补什么"机制只对 CV 问句生效，其他领域无声关闭；医学问句多烧补搜轮。
- 方向：覆盖维度从 decompose/focus_terms 程序化推导或 LLM 生成；修正拼写。

### P1-3 async 路由内同步阻塞，单请求可卡死整个事件循环
- 证据：`routers/analysis.py:723-731`（async 内同步 `SentenceTransformer(model)` 加载本地模型，可达数十秒）；
  `routers/workspaces.py:159-167`（async 内同步 networkx 聚类）；`routers/code.py:236-254`（SSE 生成器内同步建文件树/读文件）；
  `routers/papers.py:224-309`（smart_search 把含 full_text 的整表载入内存逐行扫描）
- 影响：阻塞期间所有并发请求、SSE 心跳、后台任务全部停摆；论文库增大后全局搜索拖垮服务。
- 方向：重负载路径统一改 `def` 路由（线程池）或 `asyncio.to_thread`（papers.py:197 已有正确示范，推广成规范）。

### P1-4 三个 engine 并存，后台分析引擎缺 busy_timeout，并发写随机 "database is locked"
- 证据：`services/database.py:224-234`（get_engine 注册 WAL+busy_timeout=5000）；
  `services/analysis_runner.py:36-43`（自建引擎，无任何 pragma）；`services/database.py:566`（init_db 又建第三个一次性引擎）
- 影响：上传后立刻做其他写操作时，后台解析随机转 failed 且报错晦涩。
- 方向：删除 `_get_engine`，统一 `from .database import get_engine`。

### P1-5 前端构建产物遮蔽源配置；产物未被 gitignore
- 证据：`frontend/tsconfig.node.json:2`（`composite: true` 使 tsc 发射 `vite.config.js`）；
  git status 未跟踪：`frontend/vite.config.js`、`vite.config.d.ts`、`tsconfig.tsbuildinfo`、`tsconfig.node.tsbuildinfo`
- 影响：Vite 解析配置时 `.js` 优先于 `.ts`，改 vite.config.ts（如 SSE 代理头）可能静默不生效；git status 长期被噪音污染。
- 方向：tsconfig.node.json 去掉 `composite` 改 `noEmit`；.gitignore 增加 `*.tsbuildinfo`、`frontend/vite.config.js`、`frontend/vite.config.d.ts`，删除已生成产物。

### P1-6 v2 run 的 resume 会落到 legacy v1 worker（S3 接入时的雷）
- 证据：`routers/exploration.py:776-786`（v2 走 CAS 置 running）→ 819-820 无条件 `_start(run_exploration(run_id, resume=True))`
- 影响：当前不可达（v2 无法创建），但 S3 打开创建门禁后，对 paused 的 v2 run 调 resume 会用 v1 逻辑覆盖 v2 产物。
- 方向：v2 分支改抛 `WORKFLOW_NOT_IMPLEMENTED` 或接入 v2 worker 调度入口。

### P1-7 统一 LLM 重构遗漏：refresh_related_papers 丢传 provider/api_format，回退按 URL 猜协议
- 证据：`routers/analysis.py:1526-1534`（只传 key/base_url/model）；`services/semantic_scholar.py:176-179`（默认 `provider=""`、`api_format=""`）；对照 `analysis_runner.py:378-388`（已传全）
- 影响：自定义网关 + Anthropic 协议下，手动"刷新相关论文"必以 openai_chat 发请求而失败——正是重构声称废除的行为。
- 方向：补传散参或改签名接收 `cfg: dict`。

### P1-8 LLM 档案明文 key 端点无本地校验，与同文件其他密钥端点防护不一致
- 证据：`routers/analysis.py:305-315`（`GET /llm-profiles/{id}/key` 直接返回明文）；对照 config/export（406-410）与 config/import（429-433）均有 `trusted_local_peer` 校验
- 影响：本机场景尚可接受（docstring 自述同一信任边界但实际未实现校验）；LAN/端口转发场景下一条 GET 即得全部真钥，档案 id 可经列表端点枚举。
- 方向：与 config/export 对齐校验，或非本地强制掩码/403。

### P1-9 探索历史删除无任何确认，单击即永久删除整份研究产物
- 证据：`frontend/src/pages/ExplorePage.tsx:637-643`（`onClick={() => void removeRun(h.id)}`）、269-277
- 影响：悬停 `×` 一键触发 run 级删除（含 SSE 产物、报告、对话），全项目其余 8 处破坏性操作均有 ConfirmDialog，唯独此处没有。
- 方向：复用 ConfirmDialog（参照 LLMProfilesSection 的 deleteTarget 模式）。

---

## 三、P2 —— 冗余 / 重复 / 废弃 / 负载

### P2-1 ai_config.py 迁移期双真源残留约 200 行、遍布 9 处；config.py 在 import 时改写 os.environ
- 证据：`services/ai_config.py:93-112,270-289,303-339,683-705,714-737,766-768,860-861,997-998,1046-1092`；
  `config.py:18-22,60-113`（import 即执行 `_apply_legacy_llm_env()` 写 MIMO_*/OPENAI_*/ANTHROPIC_* 映射）、147-173
- 写路径已全部剥离 analysis/translate 段，读侧镜像仅剩 3 个消费方。import 副作用迫使测试靠 PYTEST 特判防污染（ai_config.py:378,414）。
- 方向：定一个版本窗口删除内存镜像与 `or analysis or translate` 兜底；env 映射保留 AliasChoices，去掉 os.environ 写入。

### P2-2 召回三轨并存，且新引擎反向依赖 legacy 私有函数
- 证据：`recall_engine.py:51-53`（`from .explorer import _sanitize_query`）、353（`_dedupe_candidates, _usable_english_queries`）；`explorer.py:660-792` 旧 `recall_papers` 仍被 1822 回退与 503 预览使用；`stages/expand.py:27` + recall_orchestrator 为第三套
- 影响：去重/排除/消毒语义改一处漏两处；删改 explorer 内部函数会静默破坏新引擎。
- 方向：`_dedupe_candidates/_sanitize_query/_usable_english_queries` 下沉独立 recall 基础模块，三轨共享。

### P2-3 academic_survey_v2 契约层空转：十表中八张零写入，约 600 行路由 + event_log 写路径不可达
- 证据：`models/research_core.py:201-438`（八表全库无写入方）；`routers/exploration.py:256-262` 创建门禁使 `cas_run_state` 对现存 run 必然 400；前端 `types/index.ts:647-950` 约 300 行 v2 类型 0 引用、`api/exploration.ts:40-48` 两个 evidence 函数无调用方
- 属 S0 明示分期策略，非错误；但在 S3 落地前是"高维护成本、零数据流"的双轨。这批文件正在活跃修改中，烂尾风险实在。
- 方向：S3 排期确定前给 dormant 端点统一标记/文档化；前端死类型移入 spec 存档或删除。

### P2-4 证据层内重复实现
- 证据：`survey_report.py:216-219` `_clean` 与 `document_evidence.py:27-30` `normalize_text` 逐行等价（归一化口径分裂会直接导致 quote 字面校验误拒）；
  `survey_report.py:1289-1464` render_markdown 与 render_html 重复约 90 行表格/热力图/参考文献渲染；document_evidence 的 limit/offset 与 report_evidence 的 cursor/limit 两套分页
- 方向：统一 import normalize_text；渲染抽共享装配层。

### P2-5 负载：全文三遍重复解析 + result/checkpoint 整列 JSON 反复重写
- 证据：每篇全文被 DocumentEvidenceReader 解析三次（`survey_report.py:226-228,692-694,739-743`）；`document_evidence.py:180-187` locate_quote 对每条 quote 重归一化全部文本块 O(quotes×blocks)；
  `explorer.py:1480-1491` 在 6+ 处全量落库 result（含全部论文+报告+证据库，deep 档数 MB）；`explorer.py:269-286` 每次补读都把完整证据库写进 checkpoint
- 影响：deep 档（千篇语料）下纯 CPU 重复开销与 SQLite 单行重写集中在事件循环内，成为报告阶段瓶颈。
- 方向：每篇 reader 构建一次复用；块级 normalize 缓存；大产物拆独立表/文件引用，result 只存引用与统计。

### P2-6 后台任务生命周期缺陷
- 证据：`analysis_runner.py:177-200`（job 注册表只在下一次发起分析时清理，完成/失败 job 连同整份分析 JSON 长期滞留）；`routers/analysis.py:1449-1464`（reparse 对运行中任务静默无效，清缓存后运行中任务仍写回）；
  `terminal.py:104-110`（PTY 关闭只 SIGTERM+一次 WNOHANG，login shell 泄漏为孤儿进程）；
  裸 `asyncio.create_task` 不存引用共 8 处（papers.py:100,358；research.py:136,269；analysis.py:575,927；main.py:170——exploration.py:70-73 已有正确 `_start()` 模式未复用）
- 方向：job 结束 `call_later(TTL)` 自删；reparse 先 cancel；PTY 未退出则 SIGKILL 兜底；`_start` 提为公共 util。

### P2-7 analysis.py 上帝文件（1544 行）+ 错误处理样板大面积复制
- 证据：`routers/analysis.py` 同时承担配置 CRUD、LLM 档案、GROBID、findpapers、digest、SSE 六个域；全后端 `except ValueError → 404` 手写 26 处、"Paper not found" 13 处、promote 逻辑两份（papers.py:412-433 与 workspaces.py:308-336）
- 方向：拆 settings.py / ops / digest 独立 router；`Depends(get_paper_or_404)` 共享依赖统一错误契约。

### P2-8 JSON 修复/增量解析两套并存
- 证据：`ai_analyzer.py:134-279`（手写括号配平）与 `llm/json_utils.py:8-57`（parse_json）重复；`_repair_json` 唯一外部调用方 research_goal_planner.py:86-97 跨模块 import 私有函数；`thinking.py:52-56` 残留 bool 兼容形参
- 方向：json_utils 增加流式 partial 入口，统一切换后删旧实现与 bool 分支。

### P2-9 前端配置与基建平行拷贝
- 证据：AppConfig/UIConfig 四套平行定义（`hooks/useConfig.ts:8-38`、`features/settings/config.ts:32-204`、`types/config.ts:7-23` 死 barrel、ControlPanel.tsx:189 内联形状）；Mermaid 基建两份逐行复制且各持独立模块级主题状态（`FlowChart.tsx:7-57` vs `CodeAnalysisPanel.tsx:90-134`）；三个 SSE hook 重复同一连接管理骨架（useResearchStream/useExplorationStream/useStreaming，~150 行可收敛）；`api/papers.ts:153-166,263-276` 绕过统一 client 自带第三份错误解析
- 方向：抽 `lib/mermaid.ts` + `useEventChannel(url, handlers)` 原语；useConfig 改 import settings/config 的类型。

### P2-10 超长函数/上帝文件清单（重构目标）
- `explorer.py:1494-2235` run_exploration ~742 行；`survey_report.py:559-1244` build_survey_report ~686 行（8 个内嵌闭包+完整 ReAct 循环）；`recall_engine.py:332-689` run_target_recall ~358 行；`database.py:237-553` _migrate_schema ~319 行（10+ 异构迁移块串行、无版本号、papers 列清单 28 项、每次启动全量 PRAGMA+回填扫描）；
  前端 ExplorePage.tsx 1040 行 6 类职责 31 个 useState；DeepResearchSettings.tsx 1249 行 7 个 section（Suspense 边界因静态 import 永不生效）
- 方向：阶段抽独立函数/类 + 显式状态 dataclass；_migrate_schema 改声明式 `[(version, fn)]` + schema_version 表；前端四大块各自拆文件。

### P2-11 死代码清扫清单（删除前逐项 grep 复核）
- 后端：`explorer.py:925-932` `_citation_dynamics/_cohort_percentiles`（仅定义无调用）；`explorer.py:655-657` `_cap_papers` 死参数；`routers/exploration.py:64` `_RUN_STATE_PATTERN` 零引用；`main.py:323-358` `/api/system/open-vscode` 死端点（前端已改 `vscode://file` 协议，拖着一个 subprocess.Popen 攻击面）；`routers/papers.py:562-651` 90 行与实际路由脱节的死注释；`papers.py:20` 死导入
- 前端：`lib/spotlight.ts` 全库无调用且对应 CSS 已 `display:none !important` 禁用（globals.css:438）；`types/config.ts` 死 barrel；`DeepResearchSettings.tsx:32` 空 CARD 常量
- 全局：ruff 83 项（76 F401 + 7 F841），66 项可 `--fix` 自动修

### P2-12 探索对话轮询引发全页重渲染
- 证据：`ExplorationConversation.tsx:14,33`（内部 messages state 且每轮上抛全量数组）、34（2s 轮询）；`ExplorePage.tsx:123,187-192`（父级再存一份，allPapers 依赖 messages 触发整详情页 memo 重算）
- 方向：papers 合并下沉进 Conversation，父页只订阅派生增量。

### P2-13 SSRF 防护不对称 + 信任边界过宽
- 证据：PDF 下载路径无私网校验（`deep_research/pdf_downloader.py:75-94` 仅校验 http(s) 前缀），对照 git clone 有完整防护（`code_analyzer.py:52-71` validate_url_not_internal）；URL 来源含 LLM 从恶意 PDF 提炼的 related_papers 与 OpenAlex 返回链接 → 盲 SSRF；
  `terminal_access.py:52` `trusted_local_peer` 把全部 RFC1918 私网源 + 可伪造 XFF 头当"本地可信"，是终端/密钥导出/配置导入三者的共同边界——默认 127.0.0.1 绑定下不可达，一旦 `HOST=0.0.0.0`（.env.example 即此示例）整体失守
- 方向：resilient_get/download_pdf 统一复用 validate_url_not_internal + DNS 解析后 IP 复核；trusted_local_peer 只信回环，私网代理显式配置。

---

## 四、P3 —— 加固与卫生建议

1. **双虚拟环境**：根 `.venv`（1.4G，AGENTS.md 指定的正主）与 `backend/.venv`（1.3G，疑似误建）并存，确认后删除后者。
2. **`draft` 文件被 git 跟踪**：根目录 287 行任务草稿（2312.10997v5 盲测任务）混在仓库里，应移入 docs/ 或移出版本控制。
3. `@types/dompurify` 是废弃 stub（dompurify 3.x 自带类型），移除。
4. SSE 心跳策略不一致：research/exploration 15s、log_stream 30s、analysis 流无心跳（相关论文搜索最长 45s 静默，经代理易被掐断）；`load_config` 每请求多次全量读盘+深合并，加 mtime 缓存。
5. checkpoint 读-改-写未统一加锁（`explorer.py:209-244` 两函数不持锁 vs 247-286 持锁）；PDF 下载异常整体吞掉（survey_report.py:664-677，full_text=0 时无法诊断）；enrich 预算检查在信号量外有小幅超发窗口（explorer.py:838-849）；regenerate_report 活跃检查把目标 run 自身计入（exploration.py:179-184，paused run 必须先 resume 才能重建报告）。
6. 深度研究 run 仅按 workspace 互斥，可跨 workspace 无限并发；SSE 无连接数上限（对照终端 WS 有 3 连接上限）。加进程级全局信号量。
7. embedding/reranker 的真实 key 实际存于 `data/ai_config.json`，与"密钥只存 config_key"的设计声明不符（该文件 600 权限且已 gitignore，属纵深防御缺口非泄露）。
8. 前端定时器生命周期不统一（ControlPanel 递归 setTimeout 链裸奔、SettingsPage toast 定时器无清理，对照 PdfViewer 的 timersRef 集中登记模式）。

## 五、已验证无问题的面（负结论）

- **命令注入**：全仓无 shell=True/os.system/eval/exec；git clone 有域名白名单 + `--` 注入拦截 + SSRF 校验。
- **前端 XSS**：零 `as any`；3 处 dangerouslySetInnerHTML 全经 DOMPurify；react-markdown 全部未开 rehype-raw。
- **路径穿越**：上传 UUID 命名；code 文件读取双保险 commonpath + realpath 校验；导出文件名消毒。
- **上传**：扩展名 + `%PDF` magic + 50MB 分块三重校验。
- **CORS**：精确 origin 白名单 + credentials，非 `*`。
- **密钥与 git**：全历史无 .env/config_key/ai_config.json 痕迹；LLM 错误脱敏只带 endpoint/model/request_id。
- **测试纪律**：70 个后端测试文件无 skip/xfail。

## 六、建议修复批次（先审完在途改动再动工）

当前工作树有 2026-09-13 checkpoint/resume 批次与 v7.3.0 设置页批次未提交。**建议先审查提交在途改动，再开以下批次**，避免优化改动与在途改动纠缠。

| 批次 | 内容 | 主要文件范围 | 验收标准 |
|------|------|--------------|----------|
| B0 卫生（半小时） | .gitignore 补口 + 删构建产物；删双 venv；draft 移出跟踪；ruff --fix 66 项 | .gitignore、frontend/tsconfig.node.json、4 个产物文件 | `git status` 干净；`npm run build` 通过 |
| B1 正确性（P1 全部） | P1-1~P1-9 | document_evidence.py、recall_engine.py、analysis.py、analysis_runner.py、database.py、workspaces.py、code.py、papers.py、exploration.py、semantic_scholar.py、ExplorePage.tsx | 非感动词问句跑一次探索，子问题与覆盖维度随问题变化；测试全绿 |
| B2 死代码清扫（P2-11） | 逐项 grep 复核后删除 | 上表所列 | ruff 清零；全测试通过 |
| B3 引擎与任务生命周期（P1-4 收尾 + P2-6） | engine 统一、job 自删、PTY 兜底、_start util | analysis_runner.py、database.py、terminal.py、各 router | 并发写压测无 SQLITE_BUSY；开关终端 10 次无孤儿进程 |
| B4 结构收敛（P2-1/2/7/8/10） | 迁移残留退役、召回下沉、analysis.py 拆分、_migrate_schema 版本化 | ai_config.py、config.py、recall 基础模块、analysis.py→settings.py 等 | 行为无变化（契约测试守护）；各文件 <800 行 |
| B5 负载与安全加固（P2-5/12/13 + P3-4/6） | reader 复用、result 拆存、SSRF 校验、trusted_local_peer 收紧、全局信号量 | survey_report.py、document_evidence.py、explorer.py、pdf_downloader.py、terminal_access.py、research.py | deep 档报告阶段耗时对比；内网 URL 下载被拒 |
| B6 前端收敛（P2-9/12 + P1-5/9） | mermaid/SSE/配置类型三合并、ExplorePage 拆分 | 前端各处 | typecheck + test + build 全绿 |

## 七、覆盖范围说明

- 深探：deep_research 全模块 + research_core/exploration 契约层；后端核心 services/routers/models/main/config + tests 粗扫；前端 src 全量 + 构建配置；安全专项（路径/注入/SSRF/密钥/CORS/XSS/资源耗尽）。
- 未深探：`services/agent_tools.py`、`research_harness.py`、`paper_identity.py`、`domain_synthesizer.py` 仅经调用链抽查，未逐行审计；`docs/`、`backend/eval/` 评估脚本未审计。

---

## 八、2026-09-14 复核与修复记录

已按当前工作树逐条复核 P1-1–P1-9。P1-1/2/3/5/6/7/8/9 属实并已修复；P1-4 的长期分裂引擎问题属实并已统一，但 `init_db` 原先的第三个对象是启动迁移用的一次性 engine，“三个长期并存”的表述过重。生产启动现也复用共享 engine，只有显式传入其他数据库 URL 的迁移/测试才创建并随后释放一次性 engine。

修复要点：

- 报告证据子问题从当前问题及 decompose 英文检索式提取有界主题词，去除固定 ImageNet/CV 词表；召回覆盖增加实际检索式派生的通用主题维度，并修正 `clinic` 拼写。
- 本地 embedding 模型加载、工作区洞察、代码树/静态分析和全库智能搜索移出 asyncio 事件循环；分析 worker 与生产 `init_db` 复用带 WAL/`busy_timeout=5000` 的共享 engine。
- v2 resume 在状态变更前返回 `WORKFLOW_NOT_IMPLEMENTED`，不再落入 legacy worker；手动刷新相关论文透传 provider/api_format/thinking/timeout/proxy。
- 明文 key 端点收紧到回环对端并设置 `no-store`；探索删除加入不可撤销确认对话框。
- TypeScript 不再发射 `vite.config.js/.d.ts/*.tsbuildinfo`，生成物已移入废纸篓且补充 ignore；误建的 `backend/.venv` 已移入废纸篓，根 `.venv` 保留。
- P2-13 安全项同批修复：PDF 初始 URL/重定向均做公网 DNS/IP 校验，跨主机不转发授权头；本地信任不再接受私网对端或伪造 XFF。复核还发现并修正了原代码仓库 URL 校验吞掉自身私网 IP 异常的潜在校验缺陷（当前 clone 域名白名单仍是前置防线）。

验证：后端 926 项、前端 132 项全部通过；Ruff（CI 口径）、Python 编译、前端 typecheck、生产构建、CORS 契约与 `git diff --check` 通过。未调用真实 LLM/学术源，未提交、未推送。P2 的上帝文件/双轨迁移/渲染与任务生命周期等结构性债务未在本次正确性批次大规模重构。
