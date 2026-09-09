# S0 契约交付审核 — 2026-09-09

审核对象：`docs/S0_CONTRACT_DELIVERY_2026-09-09.md` 及对应的当前工作树实现。依据：`docs/ACADEMIC_SURVEY_AGENT_PLAN_2026-09-08.md`。

**结论：暂不通过“契约已冻结、可供后续模块直接依赖”的验收。** 已有骨架可以保留；以下 5 项需由 S0 修正并复验。S1/S8 可继续独立的身份纯函数、合成案例和评分协议工作；S3/S6 所依赖的状态、任务、报告契约应等修正后再冻结。

本轮只审核，未修改业务代码，未启动真实研究、下载目标论文或读写生产研究数据库。

## 验证证据与范围

- 复跑 `backend/tests/test_survey_v2_contract.py`：**20 passed，1 warning，2.01s**。warning 是 Starlette/httpx 弃用提示，与以下问题无关。
- 使用独立内存 SQLite 库、合成 run/asset/revision/task，直接调用现有状态服务与报告路由函数，复现 F1–F4。F1 用两个 Session 预读同一版本，确定性模拟请求交错，不是吞吐压测。
- 对照当前实现与 Git HEAD 的旧报告读取代码，审核回滚承诺；在内存 SQLite 验证文档的多表 DROP 语句。
- 未重跑交付文档所述后端 828 项、前端 124 项及构建，这些是交付者记录，不作为本轮独立验证结果。
- 尚属 S1–S8 的身份解析、全文获取、worker、写作和评测未实现，本身不作为 S0 缺陷。以下问题集中于已实现或已宣称冻结的边界。

## F1 · P1：状态更新不是原子的 compare-and-swap

位置：[research_event_log.py:150](/Users/katisarrow/essay_read/backend/services/research_event_log.py:150)，更新和提交位于 167–175 行。

函数先读取 run，在 Python 中检查 expected_state/version，再按主键写回；更新 SQL 没有以旧状态和版本作为条件。同一个 Session 内检查和提交不能阻止并发请求覆盖彼此。

复现：建立 running/version=4 的 v2 run；两个 Session 都先读取并持有对象。第一 Session 用 expected_state=running、expected_state_version=4 请求 paused；第二 Session 用同样旧版本请求 cancelled。两次都成功。

实际输出：最终 cancelled/version=5；事件同时包含 paused/version=5、cancelled/version=5。正确结果应是一个成功，另一个 409。否则用户操作与 worker 状态推进会丢失更新，事件无法按版本还原状态。

修正：采用数据库条件更新并检查受影响行数，或等效的数据库并发控制；状态和事件保持原子提交，冲突不追加成功事件。另外应明确同目标状态的重放如何区别于陈旧请求，目前在版本检查前直接返回成功，不能证明是同一幂等请求。

验收：两个请求竞争同一旧版本时仅一个成功，另一请求 409，版本只增一次且只有一个成功事件；覆盖相反目标以及 worker/API 竞争。

## F2 · P2：任务幂等键全局唯一，阻断同协议再次运行

位置：[research_core.py:207](/Users/katisarrow/essay_read/backend/models/research_core.py:207)，键组成说明在 221 行。

任务属于单个 run，但唯一索引只包含 idempotency_key。按当前注释，以任务类型、协议 hash、文档 hash、子问题和模型/prompt 版本构造键，同协议同材料的另一运行会产生相同键。

复现：run A 写入 extract 任务；run B 写入相同键的 extract 任务，提交得到 `UNIQUE constraint failed: research_tasks.idempotency_key`。第二次研究无法建立自己的账本。直接沿用 A 的任务也不等价，因为状态、尝试和预算都绑定 A。

修正：区分同一 run 的重复调度与跨 run 的资产复用。通常使用 run_id + idempotency_key 唯一约束；若保留全局索引，必须强制把 run 命名空间加入键并同步生产者。跨运行缓存应独立于执行身份。已有 S0 数据库需要索引迁移，不能只改模型声明。

验收：同 run 重复调度不生成重复任务；两个 run 对相同冻结输入可建立独立账本；旧 S0 库升级后行为一致。

## F3 · P2：默认报告接口没有读取当前选定 revision

位置：[exploration.py:103](/Users/katisarrow/essay_read/backend/routers/exploration.py:103)。

`GET /runs/{id}/report` 对 v1/v2 都只读 run.result.survey_report。计划 §7.2 和 ResearchReportRevision 模型说明要求 v2 返回当前选定版本。

复现：按新契约建立 report asset 和 is_selected=true 的 revision，run.result 为空。指定 revision 的 JSON 读取成功，默认报告读取返回 404 REPORT_NOT_FOUND。若 result 留有旧快照，还存在返回过时报告的风险。

修正：v2 默认入口解析当前选定 revision，复用指定版本资产的读取与导出逻辑，并提供版本标识；保持 v1 路径。明确未选版本的响应，不能用历史 result 静默顶替。

验收：只持久化资产和 revision 即可经默认入口导出；切换版本后默认入口随之变化，指定旧版本仍保持原内容；覆盖 JSON、Markdown、HTML 和无选定版本的情况。

## F4 · P2：报告资产隔离后仍可导出

位置：[exploration.py:824](/Users/katisarrow/essay_read/backend/routers/exploration.py:824)。

指定版本报告入口查询 asset 后直接返回 inline_payload，没有检查隔离状态。相邻 evidence 入口已将 quarantined 资产设为 403，报告出口却缺少保护。

复现：创建可读 revision，再将 report asset.status 改为 quarantined，指定版本仍返回完整 `{"markdown":"synthetic report"}`。因污染或内容问题被隔离的报告仍会对外提供。

修正：默认及指定版本报告复用内容可暴露性检查，至少拒绝隔离资产；校验资产属于该 run 且种类正确。不可变内容与访问状态变化应分别处理。历史 superseded 版本是否可读须明确，不能简单把所有非 active 状态都一概拒绝。

验收：隔离前可读，隔离后默认/指定版本的三种导出都拒绝且不泄漏正文；正常历史版本仍符合既定读取规则。

## F5 · P2：回滚可读性的承诺没有实现或验证依据

位置：[交付文档:54](/Users/katisarrow/essay_read/docs/S0_CONTRACT_DELIVERY_2026-09-09.md:54)；测试：[test_survey_v2_contract.py:97](/Users/katisarrow/essay_read/backend/tests/test_survey_v2_contract.py:97)。

文档承诺“直接回退到 v1 代码”后已有 v2 报告仍只读可用。测试仅 SELECT question，证明的是附加列不妨碍旧字段读取。旧报告代码只读 result.survey_report，并不知道 research_report_revisions/assets；按新契约保存的报告不会因旧列可 SELECT 就变得可用。

此外，57–59 行以逗号分隔多个表的 DROP TABLE 在 SQLite 无效，内存库实测为 `OperationalError: near ",": syntax error`。

修正：明确真正可用的回滚版本/策略，例如保留 v2 只读适配层、关闭 v2 新执行入口，并阻止旧写入路径改变 v2 数据。不能把直接退回完全不识别 v2 的版本描述成已满足只读要求。修正文档 SQL 示例，不在生产库执行删除。

验收：临时库建立含 report asset/revision 的 v2 合成记录，切到声明的回滚形态，通过 HTTP 读取报告与版本，同时确认旧写入路径不能覆盖 v2；补充回滚/升级往返测试。若尚不能支持，应明确未交付，不能继续写“测试验证”。

## 交回 S0 agent 的修复边界

1. 先修 F1/F2，重新确认共享状态和任务身份契约，补并发与旧索引升级测试。
2. 修 F3/F4，统一默认/指定版本报告的读取和隔离规则，补接口测试。
3. 修 F5，提交可执行回滚方案及端到端证据，同步交付文档和 document.md，不只修改测试名称。
4. 保持 v2 创建门禁，不启动真实研究，不扩展到 S3 worker 或 S6 写作实现。独立 worker 的跨进程事件传递与断点恢复仍需 S3 专项验收，进程内 live-bus 注册本身不能证明成立。
5. 按 F1–F5 提交修复、测试和残余限制后复审。保护当前工作树其他改动，不自行提交或推送。

复验通过后，才建议把 S0 从“骨架已交付”更新为“基础契约可供后续模块依赖”。这不代表已达到学术报告质量目标，质量仍需证据链和独立评测验证。
