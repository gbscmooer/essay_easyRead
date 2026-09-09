# S0 工作包交付报告：academic_survey_v2 契约与集成

执行日期：2026-09-09
执行范围：`docs/ACADEMIC_SURVEY_AGENT_PLAN_2026-09-08.md` §9.2 的 **S0 契约与集成**——冻结数据契约、兼容投影、数据库迁移、接口边界与集成方案。
边界声明：未实现 S1–S8 业务逻辑；未读取/上传/导入 2312.10997v5.pdf；未覆盖任何历史研究；未提交、未推送。契约实施后的现行口径已同步至 [document.md](/Users/katisarrow/essay_read/document.md) §2.12 与 [AGENTS.md](/Users/katisarrow/essay_read/AGENTS.md)。

---

## 一、修改文件清单与理由

| 文件 | 变更 | 理由 |
|---|---|---|
| `backend/models/research_core.py` **新增** | 契约常量（任务/运行/质量/身份/核验状态、资产 kind、事件类型注册表）、Core Pydantic 契约（`ResearchProtocol`/`ProtocolSubquestion`/`SearchRecord`/`IdentityDecision`/`ResolvedLocation`/`EvidenceSpan`/`CoverageGap`）、10 张 SQLModel 表 | 计划 §7.1 Core 契约的 Pydantic/TypeScript 落地；S0 独占文件 |
| `backend/models/exploration.py` | `ExplorationRun` 增加 `workflow_version`（默认 `legacy_v1`）、`state_version`（默认 0）、`protocol_asset_id` | v2 运行挂靠现有表；单调递增版本支撑事务内 CAS |
| `backend/models/schemas.py` | `ExplorationRunCreate` 扩展 5 个可选 v2 字段（`workflow_version/protocol_options/budget/content_cutoff/policy_id`）；新增 14 个请求/响应模型；`ExplorationRunResponse`/`Summary` 增加版本与摘要字段 | HTTP 契约冻结（计划 §7.2）；与前端 `types/index.ts` 严格对应 |
| `backend/services/database.py` | 迁移新增 3 列（NOT NULL 带默认，旧行自动回填）+ `create_all(checkfirst)` 兜底建新表；`reconcile_interrupted_runs` 豁免 v2 运行 | 只增不删的幂等迁移；计划 §7.1「v2 由 worker 租约和账本决定恢复，不被 API 启动误置 failed」 |
| `backend/services/research_event_log.py` **新增** | `append_event`/`replay_events`（断点续订）、`cas_run_state`（幂等重放/409/同事务事件）、`budget_snapshot`（聚合口径冻结）、`find_retry_event`（重试幂等登记）、live-bus 注册缝 | 持久化事件是状态来源之一；S3 接入 worker 的服务层缝；routers 只做转发 |
| `backend/routers/exploration.py` | v2 创建门禁（`WORKFLOW_NOT_IMPLEMENTED`，S3 替换）+ `options["workflow"]` 输入快照；12 个新端点（protocol/tasks/coverage/budget/pause/resume/cancel/retry/revisions/revisions report/evidence/stream 扩展）；v1 legacy 投影；旧 report 重建与 DELETE 对 v2 加守卫 | 接口边界冻结 + 兼容投影 |
| `backend/tests/conftest.py` | 导入 research_core 模型注册新表 | 测试库 schema 与生产一致 |
| `backend/tests/test_survey_v2_contract.py` **新增** | 20 项契约测试（见第五节） | 计划 §10.1 中 S0 负责行的覆盖 |
| `frontend/src/types/index.ts` | 同步全部契约类型（`WorkflowVersion`/`SurveyTaskState`/`SurveyRunStatusV2`/协议/任务/覆盖/预算/控制/重试/revision/证据），`ExplorationRun` 扩展可选字段 | AGENTS.md 类型对应约定；S7 联调底座（仅类型，不改 api/页面） |
| `document.md` | 新增 §2.12（契约/HTTP 表/迁移回滚/集成缝/验证） | 计划 §11.7 文档同步 |
| `AGENTS.md` | 后端目录新增 `research_core.py`、`research_event_log.py`；数据流新增第 8 条 | 同上 |
| `draft` | S0 波次计划与结果记录 | 计划 §9.1 要求 |

工作树保护：本轮开始时已有 24 个已修改文件 + 10 个未跟踪文件（此前轮次改动，`git diff --stat` 1097 insertions/162 deletions），全部保留未回滚，S0 改动叠加其上。

## 二、数据契约冻结要点

**状态常量**（值即数据库存储值，`models/research_core.py`）：

- 任务：`pending / ready / running / succeeded / failed / skipped / cancelled`
- v2 运行（与阶段分离）：`pending / running / paused / completed / partial / failed / cancelled`（活动态 = 前三者）
- 质量（独立于运行状态）：`unchecked / internally_checked / needs_review / human_reviewed`——内部模型检查不能自动提升为 human_reviewed
- 核验：`abstract_supported / fulltext_supported / unverified / contradicted / insufficient`——摘要只支撑摘要级陈述，实验细节/比较/归因需正文或表格证据
- 身份：`allow / deny / unresolved`；预算资源：`llm_tokens / llm_calls / http_requests / download_bytes / wall_clock_minutes`（provider_scope 细分如 `http_requests:scopus` 由 S3 记账）

**10 张表**（全部 int 自增主键 + 对外 uuid 字符串 ID；`revision_id="legacy"` 保留给 v1 伪版本投影）：

`research_tasks`（任务账本=执行真源，幂等键唯一性以 **(run_id, idempotency_key)** 生效——同 run 防重复调度、跨 run 同冻结输入各建独立账本，跨运行内容复用由资产层 content_hash 承担；lease_owner/lease_expires_at）、`research_task_attempts`（不含密钥）、`research_assets`（不可变：document/protocol/search_record/coverage_snapshot/report/manifest；大文件 storage_path+content_hash、小型 JSON inline_payload；status=active/quarantined/superseded/orphaned）、`research_evidence`（EvidenceBundle：spans 含页/节/表/bbox/字符位置与归一化 quote，doc_content_hash 防资产替换后引用漂移）、`research_relations`、`research_claims`（text_hash 绑定核验，正文改写即失效）、`research_report_revisions`（is_selected 标记当前版本，指向 report 资产）、`research_budget_events`、`research_events`（持久化事件流）、`research_identity_decisions`。

**相对计划 §7.1 建议表清单的两处偏离**（已记录理由）：新增 `research_relations`（关系是一等查询对象，核验流水线需按状态检索）与 `research_identity_decisions`（门禁需按 family 索引检索，事件流是 append-only 不适合做门禁索引）；`SearchRecord` 与覆盖快照以资产行（`content_kind`）存储，不单列成表。

**预算聚合口径**（冻结于 `ResearchBudgetEvent` docstring 与 `budget_snapshot`）：
`used = Σ delta_used`；`reserved = Σ(delta_reserved − delta_used − delta_refunded)` 整体下限 0；`usage_known=false` 表示「响应丢失、按上界记账」窗口，不得归零。

## 三、迁移与回滚

**迁移**（`_migrate_schema`，启动自动执行，已测试幂等）：

1. `exploration_runs` 增列：`workflow_version TEXT NOT NULL DEFAULT 'legacy_v1'`、`state_version INTEGER NOT NULL DEFAULT 0`、`protocol_asset_id TEXT`——旧数据（含研究 #20/23/24）自动落默认值，不重写历史；
2. 新建 10 张 v2 表（`create_all(checkfirst)` 兜底，正常路径由 init_db 建表）。

**回滚**（2026-09-09 复审 F5 修正口径）：**声明的回滚形态是「保留本 S0 契约层代码」**——v2 数据经只读端点（revisions / report / protocol / evidence / tasks / budget）继续可读；创建门禁天然关闭新 v2 执行（`WORKFLOW_NOT_IMPLEMENTED`）；旧写入路径（`POST /runs/{id}/report`、`DELETE /runs/{id}`）被守卫阻断不能改动 v2 数据；v1 入口照常。以上已由端到端测试 `test_rollback_form_keeps_v2_readonly_and_blocks_writes` 验证。**注意边界**：直接回退到不识别 v2 的历史版本，只能保证 v1 数据可读（附加列不破坏旧列 SELECT，`test_rollback_keeps_v2_rows_readable_by_v1_reader` 验证列级兼容），v2 报告（资产 + revision）不能经旧代码读取——不能把该形态描述为满足"v2 报告仍只读"。仅在彻底放弃 v2 数据时手动执行（先停服备份；SQLite 不支持逗号分隔多表 DROP，须逐条）：

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

第一轮不删除任何旧字段/表（计划 §11.4）。

## 四、API 契约样例

创建（v2 目前门禁，输入校验契约已生效；S3 移除门禁后即为真实入口）：

```json
POST /api/exploration/runs
{"question": "请对大语言模型中的检索增强生成（RAG）开展一项面向研究者的文献综述…",
 "depth": "deep",
 "workflow_version": "academic_survey_v2",
 "budget": {"llm_tokens": 1000000, "llm_calls": 400},
 "content_cutoff": "2024-03-27T23:59:59Z",
 "policy_id": "policy-abc"}

→ 400 {"detail": "academic_survey_v2 执行器尚未接入（等待 S3/S5），当前请使用默认工作流",
       "code": "WORKFLOW_NOT_IMPLEMENTED"}
```

任务分页（合成 v2 数据实测）：

```json
GET /api/exploration/runs/1/tasks?state=failed&limit=50
→ {"run_id": 1, "workflow_version": "academic_survey_v2", "legacy": false,
   "items": [{"task_id": "a1b2…", "kind": "extract", "subquestion_id": "sq1",
              "state": "failed", "dependencies": [], "idempotency_key": null,
              "error_code": "TIMEOUT", "created_at": "…", "updated_at": "…"}],
   "next_cursor": null, "total": 1}
```

暂停与幂等/冲突语义：

```json
POST /api/exploration/runs/1/pause   {"expected_state_version": 4}
→ 200 {"run_id": 1, "status": "paused", "state_version": 5,
       "idempotent_replay": false, "detail": "已请求在安全点暂停"}

重复 pause      → 200 idempotent_replay=true，state_version 不递增（返回原结果）
expected_state_version=1 → 409 {"code": "STALE_STATE_VERSION"}
expected_state="running" → 409 {"code": "STATE_CONFLICT"}
终态再 pause    → 409 {"code": "INVALID_STATE"}
```

预算：

```json
GET /api/exploration/runs/1/budget
→ {"run_id": 1, "workflow_version": "academic_survey_v2", "legacy": false,
   "resources": [
     {"resource": "llm_calls", "limit": 400, "used": 100, "reserved": 50,
      "unit": "count", "usage_known": false}],
   "stop_reason": "budget_exhausted"}
```

重试（幂等登记在 `run_retry` 事件）：

```json
POST /api/exploration/runs/1/retry   {"failed_only": true, "idempotency_key": "retry-1"}
→ {"run_id": 1, "status": "pending", "state_version": 6,
   "retried_tasks": 1, "idempotent_replay": false}
同 key 重复请求 → idempotent_replay=true、retried_tasks 原值；未知 task_ids → 404 TASK_NOT_FOUND
```

SSE v2（`GET /runs/{id}/stream`）：帧带 `id: <event_id>`，支持 `Last-Event-ID` 头或 `?after=` 查询参数断点续订（两者等价，已测试）；回放持久化事件后发 `run_status`，活动运行接 live-bus（S3 注册前返回明确 error 事件）。未知游标 → 422 `INVALID_CURSOR`。

默认报告入口（复审 F3/F4 后）：`GET /runs/{id}/report` 对 v2 运行解析当前选定 revision，所有格式响应带 `X-Report-Revision` 头，JSON 额外附 `revision_id` 字段；切换 `is_selected` 后默认入口随之变化，指定旧版本仍返回原内容；无选定版本 404（不用 result 历史快照顶替）；报告资产 quarantined → 403（默认与指定版本、三种格式统一拒绝），superseded 历史版本可读，orphaned/跨 run/非 report kind → 404。

完整端点表（12 个新端点 + 扩展）见 `document.md` §2.12；全部仅 GET/POST，错误统一 `{detail, code}`，CORS 预检由应用级中间件处理（已测试允许 Origin 放行、非允许 Origin 拒绝）。

## 五、v1 兼容性说明

- **v1 行为零变化**：不传 `workflow_version` 的创建、旧报告导出/重建、追问、删除、SSE 全部不变（`test_create_v1_without_workflow_version_still_works`、`test_v1_stream_unchanged` 等回归验证）；响应新增字段均有默认值，前端旧代码无感。
- **legacy 投影**：新端点对 v1 运行返回——protocol 给 options 快照（`legacy=true`，无子问题）；tasks/budget 给空集合；coverage 原样投影 `result.survey_report.coverage`；revisions 给单个 `revision_id="legacy"` 伪版本（其 report 与旧 `GET /runs/{id}/report` 等价）；evidence 固定 404（v1 无独立证据对象）；控制类操作（pause/resume/cancel/retry）对 v1 返回 400 `UNSUPPORTED_WORKFLOW`。
- **双向保护**：旧 `POST /runs/{id}/report` 与 `DELETE /runs/{id}` 对 v2 运行返回 `WORKFLOW_NOT_IMPLEMENTED`（报告 revision 重建待 S6、级联删除待 S3），防止 v1 路径覆盖 v2 产物；`reconcile_interrupted_runs` 不再把 v2 活动运行误置 failed（worker 租约负责恢复，已测试豁免生效）。
- **历史数据**：迁移只增列带默认值，研究 #20/23/24 与全部历史报告、options 快照、下载素材不重算不覆盖。

## 六、测试及结果

环境：本机 `.venv`（Python 3.13），合成数据与临时库，无网络请求。

| 命令 | 结果 |
|---|---|
| `cd backend && LLM_API_KEY=test-key-for-ci ../.venv/bin/python -m pytest tests/` | **838 passed**（原 808 + S0 契约 20 + 复审修复 10，0 failed） |
| `.venv/bin/ruff check backend/ --select E,F,W --ignore E501,E402,F401,F841,W291,W293` | All checks passed |
| `npm run typecheck --prefix frontend` | 通过 |
| `npm run test:run --prefix frontend` | 124 passed（24 文件） |
| `npm run build --prefix frontend` | ✓ built in 1.32s |

`tests/test_survey_v2_contract.py` 30 项覆盖：

1. 迁移：空库建表、旧形状库升级（列默认值回填、旧数据可读）、重复迁移幂等、任务幂等索引从全局唯一迁至 run 作用域唯一（旧索引升级后行为一致）、附加列不破坏 v1 读取（列级兼容边界已注明）；
2. `reconcile_interrupted_runs`：v1 running → failed，v2 running 保持（豁免）；
3. 创建：v2 门禁 400 + `{detail, code}` 顶层格式、未知 workflow_version 422、v1 默认创建正常且 options 无 workflow 快照键、`_v2_options_snapshot` 快照往返（含 content_cutoff ISO 序列化）；
4. v1 legacy 投影全套（protocol/tasks/budget/coverage/revisions/legacy report/evidence 404/控制 400）；
5. v2 契约：protocol 资产链与变更记录、任务分页/状态过滤/游标（含 422 无效游标）、覆盖快照与协议回退、预算聚合（reserved/used/usage_known/stop_reason 优先级：revision > budget_stop 事件）、证据详情（spans 定位 + 文档身份 + public_available_at）、revision 列表与三格式导出、GET run 的 protocol/current_revision 摘要；
6. CAS：pause→resume→cancel 版本轨迹、幂等重放、STALE_STATE_VERSION/STATE_CONFLICT/INVALID_STATE 409、事件同事务落库；**并发竞争**（复审 F1）：两请求携带同一旧版本竞争相反/相同目标仅一个成功、另一 409、版本只增一次、冲突不追加成功事件；重放判定区别于陈旧请求（`expected_state_version+1==当前版本` 视为同一迁移重放，落后更多 409）；
7. retry：只重入队失败任务（succeeded 不动）、幂等重放、未知任务 404、pending 态 409；
8. SSE：持久化事件回放（`id:` 帧）、Last-Event-ID 与 `?after=` 等价、未知游标 422、v1 流不变；
9. CORS：新 POST 端点预检放行（allow-origin=前端 Origin）+ 实际 POST、非允许 Origin 拒绝。

**局限（如实声明）**：v2 执行器未接入，「运行中」run 的实时事件、真实 worker 崩溃恢复、租约续期属 S3 范围——本轮以 live-bus 注册缝 + 明确 error 事件表达，未声称质量提升（符合波次 0 退出条件：接口冻结、v1 读取不变）。

## 七、需要其他 agent 提供的接口

**共享文件所有权（S0 独占，跨边界改动向 S0 提请求）**：`models/schemas.py`、`models/exploration.py`、`models/research_core.py`、`services/database.py`、`services/research_event_log.py`、`routers/exploration.py`、`tests/conftest.py`、`frontend/src/types/index.ts`。

| Sub | 需要提供的接口 / 接入点 |
|---|---|
| **S3 任务运行** | ① 替换 `routers/exploration.py` 中 `WORKFLOW_NOT_IMPLEMENTED` 门禁为 worker 提交（`_v2_options_snapshot` 与 run 的 workflow 字段已备好）；② 调 `research_event_log.register_live_bus(run_id, bus)` 接实时事件（bus 需暴露 `subscribe()` 异步迭代器，参照现有 exploration bus）；③ 写 `research_tasks` 租约/attempts 与 `research_budget_events`（reserve→settle，usage_unknown 按上界记 delta_used 且 usage_known=false）；④ v2 删除的级联清理（当前 DELETE 被 400 门禁保护） |
| **S5 研究闭环** | 写 `content_kind=protocol / search_record / coverage_snapshot` 资产并回填 `run.protocol_asset_id`；协议 hash 与 budget 限额供 GET protocol/budget 读取（覆盖快照 payload 含 `subquestions[]` 与 `gaps[]`，字段见 `CoverageGap` 契约） |
| **S6 综合报告** | 同一事务写 report 资产 + `research_report_revisions`（含 `is_selected` 切换）；接管 v2 的 `POST /runs/{id}/report`（当前被门禁保护，创建新 revision 而非覆盖） |
| **S1 隔离身份** | 写 `research_identity_decisions`（allow/deny/unresolved + policy_hash + attestation）；deny 只回通用状态给执行器，细节留在审计表 |
| **S2 文档访问** | 写 `content_kind=document` 资产（content_hash / public_available_at / parse_status / structure_index；`ResolvedLocation` 列表存 `source_record["locations"]`）；quarantined 资产的证据 API 已按 403 `EVIDENCE_NOT_EXPOSED` 处理 |
| **S7 产品界面** | `frontend/src/types/index.ts` 契约类型已冻结可直接使用；api/hooks/页面为 S7 所有，但请勿修改全局 types（归 S0）；mock 联调可用合成 v2 行（参照测试 `v2_env` fixture 构造） |
| **S8 独立评测** | 评分器只读 `research_events`/`research_assets`/`research_claims` 等冻结快照；报告冻结以 revision + asset_manifest_hash 为锚 |

## 八、共享文件接入顺序建议（后续波次）

1. S1/S8 可立即基于本契约开跑（波次 0 其余两项）；
2. S3 移除创建门禁前，先完成 live-bus 注册与租约写入（波次 1 退出条件：进程中断可恢复、预算不重置、事件断点重放——回归入口为 `tests/test_survey_v2_contract.py` + S3 新增崩溃恢复测试）；
3. S6 接入 revision 写入前，`POST /runs/{id}/report` 的 v2 分支保持门禁，避免旧路径覆盖；
4. 每波集成由 S0 串行合并共享文件，sub 只交模块与适配补丁（计划 §9.3）。

## 九、复审修订记录（2026-09-09 第二轮，对照 S0_CONTRACT_REVIEW_2026-09-09.md）

| 编号 | 修复内容 | 验收测试 |
|---|---|---|
| F1 · P1 | `cas_run_state` 改为数据库条件更新（UPDATE 以旧状态+旧版本为 WHERE 守卫 + 受影响行数校验，抽出 `guarded_transition` 供 retry 复用）；幂等重放判定明确为「无前置条件，或 `expected_state_version+1==当前版本`（同一迁移已发生）」，区别于陈旧请求；冲突回滚不追加成功事件 | `test_concurrent_cas_opposite_targets_only_one_wins`（相反目标：仅一个成功、版本只增一次、无冲突事件）、`test_concurrent_cas_same_target_only_one_wins`（同目标）、`test_cas_replay_distinguishes_same_transition_from_stale`（重放 vs 陈旧） |
| F2 · P2 | 幂等唯一索引改为 `(run_id, idempotency_key)`（模型 + `_migrate_schema` DROP 旧全局索引/建新索引）；docstring 明确 run 命名空间语义与跨运行复用由资产层承担 | `test_task_idempotency_scoped_to_run`（跨 run 同键独立账本、同 run 重复冲突）、`test_task_idempotency_index_migrates_from_global`（旧索引升级 + 行为一致） |
| F3 · P2 | `GET /runs/{id}/report` 对 v2 解析当前选定 revision（`_selected_revision` + 复用资产读取），JSON 附 `revision_id`、响应附 `X-Report-Revision` 头（v1 为 `legacy`）；无选定版本 404，不用 result 快照顶替 | `test_v2_default_report_resolves_selected_revision`（含切换选定后默认入口变化、指定旧版本原内容）、`test_v2_default_report_without_selection_rejects_stale_result` |
| F4 · P2 | 默认与指定版本报告共用 `_load_exposable_report`：quarantined→403 `REPORT_NOT_EXPOSED`、orphaned/缺失/跨 run/非 report kind→404、superseded 历史版本可读（不可变内容与访问状态分别处理） | `test_quarantined_report_asset_blocks_all_exports`（隔离前后 × 默认/指定 × 三格式）、`test_report_asset_visibility_rules`（superseded 可读、跨 run 404） |
| F5 · P2 | 回滚口径改为「保留 S0 契约层形态」（本文 §三重写）；多表 DROP 逗号语法修正为逐条（SQLite 不支持逗号多表 DROP）；列级兼容测试注明边界（不声称 v2 报告可经旧代码读取）；document.md §2.12 同步 | `test_rollback_form_keeps_v2_readonly_and_blocks_writes`（HTTP 只读可访问 + 新 v2 执行关闭 + 旧写入路径阻断 + v1 照常）、`test_rollback_keeps_v2_rows_readable_by_v1_reader`（边界注明） |

复审修复后全量门禁：后端 **838 passed**（契约文件 30 项）、Ruff、前端 typecheck / 124 项 / 生产构建全部通过。并发竞争测试的实现说明：身份映射为弱引用，测试须持有实例强引用才能构造"陈旧内存视图 + 守卫拦截"的真实交错（已注释）；守卫更新在独立 SQLite 连接上验证过 rowcount 语义（新鲜条件 1 / 陈旧条件 0）。

残余限制（如实声明，不随本轮修复消失）：独立 worker 的跨进程事件传递、租约续期与真实崩溃恢复仍属 S3 专项验收，进程内 live-bus 注册与单进程会话竞争测试不能证明其成立；superseded/orphaned 之外的新资产状态语义如后续引入须回 S0 登记。
