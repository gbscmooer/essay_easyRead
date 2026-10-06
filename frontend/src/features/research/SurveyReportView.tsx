import { useMemo, useState } from 'react'
import { BookOpen, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Download, FileText, FlaskConical, Grid3X3, Layers, ListTree, RefreshCw, Table2 } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { CoverageMatrix, SurveyEvidence, SurveyReport, SurveyRow } from '@/types/survey'
import { PaperTitle, EvidenceMarkdown } from './paperLinks'
import { ChartView } from './ChartView'
import '@/styles/survey-report.css'

type Ref = SurveyReport['references'][number]
const TITLES: Record<string, string> = {
  name: '框架 / 工具', kind: '类型', target: '评估对象', aspects: '评估维度', metrics: '度量指标',
  aspect: '评估维度', metric: '指标', description: '材料说明', task: '下游任务', subtask: '子任务',
  dataset: '数据集', method: '方法', category: '技术类别',
  baseline_method: '对照方法', split: '数据划分', value: '结果', baseline_value: '对照结果', unit: '单位',
  metric_direction: '指标方向', hardware: '硬件', batch_size: 'Batch size', input_size: '输入规模',
  precision: '精度', comparison_limit: '可比性限制',
}

function Evidence({ items, references }: { items: SurveyEvidence[]; references: Map<string, Ref> }) {
  return <details className="survey-evidence"><summary>{new Set(items.map(x => x.paper_key)).size} 篇来源</summary>
    {items.map((e, i) => <div key={i}>
      {references.has(e.paper_key) && <PaperTitle paper={references.get(e.paper_key)!} />}
      <span className="survey-source-kind">{e.source_kind === 'abstract' ? '摘要' : '全文片段'}</span>
      <small>{[e.evidence_id, e.page ? `p.${e.page}` : '', e.section, e.table, e.location_status === 'unavailable' ? '定位不可用' : ''].filter(Boolean).join(' · ')}</small>
      <blockquote>{e.quote}</blockquote>
    </div>)}
  </details>
}

function SummaryTable({ rows, columns, references, caption }: {
  rows: SurveyRow[]; columns: string[]; references: Map<string, Ref>; caption: string
}) {
  const [expanded, setExpanded] = useState(false)
  const [query, setQuery] = useState('')
  const filtered = useMemo(() => rows.filter(row => columns.some(key => String(row[key]).toLowerCase().includes(query.toLowerCase()))), [rows, columns, query])
  if (!rows.length) return <p className="survey-empty">已检查材料中尚未提取到有原文依据的条目。</p>
  return <>
    {rows.length > 8 && <input className="survey-filter" type="search" aria-label={`筛选${caption}`} placeholder="筛选条目" value={query} onChange={e => setQuery(e.target.value)} />}
    <div className="survey-table-scroll"><table className="survey-table">
      <caption>{caption}</caption>
      <thead><tr>{columns.map(c => <th scope="col" key={c}>{TITLES[c]}</th>)}<th scope="col">原文依据</th></tr></thead>
      <tbody>{(expanded ? filtered : filtered.slice(0, 12)).map(row => <tr key={row.id}>
        {columns.map(c => <td key={c}>{Array.isArray(row[c]) ? (row[c] as string[]).join(' · ') || '未说明' : String(row[c] || '未说明')}</td>)}
        <td><Evidence items={row.evidence} references={references} /></td>
      </tr>)}</tbody>
    </table></div>
    {filtered.length > 12 && <button type="button" className="survey-text-button" onClick={() => setExpanded(!expanded)}>{expanded ? '收起' : `展开全部 ${filtered.length} 条`}</button>}
  </>
}

export function SurveyHeatmap({ matrix, title, references }: { matrix: CoverageMatrix; title: string; references: Map<string, Ref> }) {
  const [page, setPage] = useState(0)
  const [selected, setSelected] = useState<{ row: string; column: string; keys: string[] } | null>(null)
  const counts = new Map(matrix.cells.map(c => [`${c.row}:${c.column}`, c]))
  const peak = Math.max(1, ...matrix.cells.map(c => c.count))
  const offset = Math.min(page * 6, Math.max(0, Math.ceil(matrix.columns.length / 6) - 1) * 6)
  const cols = matrix.columns.slice(offset, offset + 6)
  return <div className="survey-matrix">
    <ChartView title={title}>
      {!matrix.cells.length ? <p className="survey-empty">暂无可统计的对应关系。</p> : <div className="survey-table-scroll"><table className="survey-heat" aria-label={title}>
        <thead><tr><th scope="col">{title.includes('数据集') ? '任务 / 数据集' : '维度 / 指标'}</th>{cols.map(c => <th scope="col" key={c}>{c}</th>)}</tr></thead>
        <tbody>{matrix.rows.map((r, i) => <tr key={r}><th scope="row">{r}</th>{cols.map((c, j) => {
          const cell = counts.get(`${i}:${offset + j}`)
          return <td key={c}><button type="button" disabled={!cell} aria-label={`${r}，${c}：${cell?.count ?? 0} 篇证据`}
            style={{ background: cell ? `rgba(40, 139, 125, ${.12 + .65 * cell.count / peak})` : 'var(--bg-elevated)', color: cell && cell.count / peak > .55 ? '#fff' : 'var(--text-primary)' }}
            onClick={() => setSelected({ row: r, column: c, keys: cell?.paper_keys ?? [] })}>{cell?.count ?? '—'}</button></td>
        })}</tr>)}</tbody>
      </table></div>}
    </ChartView>
    <div className="survey-matrix-footer"><span>有原文依据的论文数 · 空白表示未提取到关系</span>
      {matrix.columns.length > 6 && <div className="survey-pager"><button title="上一组列" aria-label={`上一组${title}`} disabled={offset === 0} onClick={() => setPage(Math.max(0, page - 1))}><ChevronLeft size={16} /></button>
        <span>{offset + 1}–{Math.min(offset + 6, matrix.columns.length)} / {matrix.columns.length}</span>
        <button title="下一组列" aria-label={`下一组${title}`} disabled={offset + 6 >= matrix.columns.length} onClick={() => setPage(page + 1)}><ChevronRight size={16} /></button></div>}
    </div>
    {selected && <div className="survey-cell-sources"><strong>{selected.row} / {selected.column}</strong><ul>{selected.keys.map(key => <li key={key}>{references.has(key) && <PaperTitle paper={references.get(key)!} />}</li>)}</ul></div>}
  </div>
}

function ReferencesSection({ references }: { references: Ref[] }) {
  const [expanded, setExpanded] = useState(false)
  if (!references.length) return null

  const limit = 5
  const hasMore = references.length > limit
  const visible = expanded ? references : references.slice(0, limit)

  const toggle = () => {
    if (expanded) {
      setExpanded(false)
      document.getElementById('survey-references')?.scrollIntoView?.({ behavior: 'smooth' })
    } else {
      setExpanded(true)
    }
  }

  return (
    <section id="survey-references" className="survey-section">
      <div className="survey-references-header">
        <h3>
          <BookOpen size={18} />参考文献
          <span className="survey-ref-count">（共 {references.length} 篇）</span>
        </h3>
        {hasMore && expanded && (
          <button type="button" className="survey-text-button" onClick={toggle}>
            <span>收起</span>
            <ChevronUp size={14} />
          </button>
        )}
      </div>
      <ol className="survey-references">
        {visible.map(p => (
          <li key={p.key}>
            <PaperTitle paper={p} />
            <span>{p.year ?? '年份未记录'}</span>
          </li>
        ))}
      </ol>
      {hasMore && (
        <button type="button" className="survey-text-button" onClick={toggle}>
          <span>{expanded ? '收起' : `展开全部 ${references.length} 篇`}</span>
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
      )}
    </section>
  )
}

export function SurveyReportView({ report, runId, busy, onGenerate, error }: {
  report?: SurveyReport; runId: number; busy: boolean; onGenerate: () => void; error?: string
}) {
  const references = useMemo(() => new Map((report?.references ?? []).map(p => [p.key, p])), [report])
  const citations = useMemo(() => Object.fromEntries((report?.references ?? []).map((p, i) => [String(i + 1), p.key])), [report])
  const years = useMemo(() => Object.entries(report?.bibliometrics?.by_year ?? {}).sort(([a], [b]) => Number(a) - Number(b)).map(([year, count]) => ({ year, count })), [report])
  const planningFailed = report?.method?.stop_reason === 'planning_failed'
  const modelFailedBeforeSearch = report?.method?.stop_reason === 'model_error' && report.coverage.corpus === 0
    && (report.search_summary?.attempts?.length ?? 0) === 0
  const selectionFailed = report?.method?.stop_reason === 'material_selection_failed'
  const selectionEmpty = report?.method?.stop_reason === 'material_selection_empty'
  const noResults = report?.status === 'no_results' && !planningFailed && !modelFailedBeforeSearch
  const delivery = report?.delivery
  const frozenAnswerStatus = delivery?.answer_status ?? (
    delivery?.narrative_source === 'frozen_answers' && report?.coverage.used === 0 ? 'none' : undefined
  )
  const isPreliminary = delivery?.narrative_source === 'preliminary'
  const isUnavailable = delivery?.narrative_source === 'none'
  // 执行与答案生成分列显示；报告生成后即结束，不再展示核验状态。
  const execChip = !report ? '' : modelFailedBeforeSearch ? '模型调用失败 · 未执行搜索' : selectionEmpty ? '未形成阅读组合' : selectionFailed ? '选材未完成' : planningFailed ? '规划失败 · 未执行搜索' : noResults ? '执行完成 · 检索未命中'
    : report.status === 'completed' ? '执行完成'
    : report.status === 'partial' ? '执行部分完成'
    : report.status === 'unavailable' ? '执行未完成' : '执行状态未知'
  const answerChip = !report ? '' : planningFailed || modelFailedBeforeSearch ? '答案未生成' : noResults ? '答案未生成 · 检索审计'
    : !delivery ? '旧版报告 · 生成状态未记录'
    : frozenAnswerStatus === 'none' ? '尚无通过核查的答案'
    : frozenAnswerStatus === 'partial' ? '已有部分有据回答'
    : delivery?.narrative_source === 'final_draft' || delivery?.narrative_source === 'frozen_answers' ? '答案已生成'
    : isPreliminary ? '答案未生成 · 仅前置预览' : '答案未生成'
  const statusBadge = !report ? '' : modelFailedBeforeSearch ? '模型调用失败 · 未执行搜索' : selectionEmpty ? '未形成阅读组合 · 具体缺口已保留' : selectionFailed ? '选材工具未完成 · 材料已保留' : planningFailed ? '规划失败 · 未执行搜索' : noResults ? '检索未命中 · 报告已生成'
    : isUnavailable ? '未生成终稿 · 保留证据表'
    : isPreliminary ? '前置综合预览 · 终稿未生成'
    : delivery?.delivery_status === 'ready' || report.quality_status === 'internally_checked' ? '报告已生成'
    : delivery?.delivery_status === 'partial' ? '报告部分生成'
    : report.status === 'completed' ? '报告已生成'
    : report.status === 'partial' ? '报告部分生成'
    : '证据表待生成'
  return <article className="survey-report" aria-label="研究报告">
    <div className="survey-toolbar"><div><BookOpen size={20} /><h2>研究报告</h2>{report && <span className="survey-status-chips" title={statusBadge}><b>{execChip}</b><b>{answerChip}</b></span>}</div>
      <div>
        {report && <><a href={`/api/exploration/runs/${runId}/report?format=html`} title="下载含图表的 HTML 报告" aria-label="下载 HTML 报告"><Download size={16} /><span>HTML</span></a>
          <a href={`/api/exploration/runs/${runId}/report?format=markdown`} title="下载 Markdown 报告" aria-label="下载 Markdown 报告"><FileText size={16} /><span>Markdown</span></a></>}
        <button type="button" onClick={onGenerate} disabled={busy} title={report ? '从已有材料与搜索记录重新生成报告' : '生成报告'}><RefreshCw size={16} />{busy ? '生成中' : report ? '重新生成' : '生成报告'}</button>
      </div>
    </div>
    {error && <p role="alert" className="survey-error">{error}</p>}
    {report && isPreliminary && <p role="status" className="survey-note">本报告未生成最终草稿，以下叙述为前置综合的<b>预览</b>（preliminary），不是最终报告{delivery?.error_code ? `（失败原因：${delivery.error_code}）` : ''}。</p>}
    {report && isUnavailable && <p role="status" className="survey-note">本次运行未产出终稿叙述{delivery?.error_code ? `（失败原因：${delivery.error_code}）` : ''}；报告保留证据表与材料入口，不包含领域结论。</p>}
    {!report ? <p className="survey-empty">{busy ? '报告生成中，已取得的资料与统计可在其他视图查看。' : '这条研究尚未生成结构化报告。已有资料可以继续使用。'}</p> : noResults ? <>
      <nav className="survey-toc" aria-label="报告目录"><a href="#survey-search">已执行的搜索</a><a href="#survey-directions">建议补搜方向</a><a href="#survey-limits">结论边界</a></nav>
      <section id="survey-search" className="survey-section"><h3><FileText size={18} />已执行的搜索</h3>
        <p>{report.summary}</p>
        <dl className="survey-stat-strip"><div><dt>正式召回</dt><dd>0<small> 篇</small></dd></div><div><dt>检索通道</dt><dd>{report.search_summary?.attempt_count ?? 0}<small> 个</small></dd></div><div><dt>通道命中</dt><dd>{report.search_summary?.total_channel_hits ?? 0}<small> 条</small></dd></div><div><dt>可引用证据</dt><dd>0<small> 条</small></dd></div></dl>
        <div className="survey-table-scroll"><table className="survey-table survey-search-table"><caption>本次运行记录的预览与正式检索</caption><thead><tr><th scope="col">阶段</th><th scope="col">轮次</th><th scope="col">数据源</th><th scope="col">检索式</th><th scope="col">排序</th><th scope="col">上限</th><th scope="col">命中</th></tr></thead><tbody>
          {(report.search_summary?.attempts ?? []).map((attempt, i) => <tr key={`${attempt.phase}-${attempt.round}-${attempt.query}-${i}`}><td>{attempt.phase === 'preview' ? '预览' : '正式'}</td><td>{attempt.round ?? '—'}</td><td>{attempt.provider || '未记录'}</td><td>{attempt.query || '未记录'}</td><td>{attempt.order || '—'}</td><td>{attempt.limit ?? '—'}</td><td>{attempt.hits}</td></tr>)}
          {!report.search_summary?.attempts?.length && <tr><td colSpan={7}>旧记录未保存逐通道检索明细。</td></tr>}
        </tbody></table></div>
      </section>
      <section id="survey-directions" className="survey-section"><h3><ListTree size={18} />建议补搜方向</h3><p className="survey-note">以下内容是查询策略建议，不是关于领域现状的事实结论。</p><ul>{(report.search_summary?.directions ?? []).map((text, i) => <li key={i}>{text}</li>)}</ul>{!report.search_summary?.directions?.length && <p className="survey-empty">当前搜索记录不足以自动形成补搜方向。</p>}</section>
      <section id="survey-limits" className="survey-section"><h3><Layers size={18} />结论边界</h3><p>{report.search_summary?.interpretation || '本次零命中不能证明全领域不存在相关工作。'}</p><ul>{report.warnings.map((text, i) => <li key={i}>{text}</li>)}</ul></section>
    </> : report.chapters ? <>
      <section className="survey-section"><h3>研究结论与依据</h3><EvidenceMarkdown text={planningFailed ? '研究规划未完成，尚未执行文献检索与分析；当前没有研究结论。' : report.summary} papers={report.references} citations={citations} />
        <p>停止原因：{({ required_questions_supported: '必答问题已有依据支持', no_progress: '多次尝试未补充有效依据',
          no_actionable_gap: '当前没有可执行的补充方向', resource_limit: '已到本次研究的资源上限', action_limit: '已到本次研究的工作轮次上限',
          material_selection_empty: '未选出可执行的阅读组合，具体缺口已保留', material_selection_failed: '选材工具未完成，已保留召回材料', planning_failed: '研究计划未通过校验', model_error: '模型服务调用未完成' } as Record<string, string>)[String(report.method?.stop_reason)] ?? '未记录'} · {report.quality_status === 'internally_checked' ? '已进行内部支持检查' : '仍有未解决问题'}；尚未经人工评审。</p>
        <dl className="survey-stat-strip survey-stat-strip--5">{[['相关候选', report.coverage?.corpus ?? 0], ['可读材料', report.coverage?.with_material ?? 0],
          ['已读材料', report.coverage?.examined ?? 0], ['答案引用', report.coverage?.used ?? report.references?.length ?? 0],
          ['全文材料', report.coverage?.full_text ?? 0]].map(([name, count]) => <div key={name}><dt>{name}</dt><dd>{count}<small> 篇</small></dd></div>)}</dl>
      </section>
      {report.chapters.map(chapter => <section className="survey-section" key={chapter.question_id}>
        <h3>{chapter.title}</h3>
        <EvidenceMarkdown text={chapter.text} papers={report.references} citations={citations} />
        <details><summary>原文依据（{chapter.evidence.length} 条）</summary>
          {chapter.evidence.map(evidence => <blockquote key={evidence.evidence_id}>
            <p>{evidence.paper_key && references.has(evidence.paper_key) && <PaperTitle paper={references.get(evidence.paper_key)!} />}
              <span className="survey-source-kind">{evidence.source_kind === 'abstract' ? '摘要' : '全文片段'}</span></p>
            <p>{evidence.fact}</p>
            {evidence.spans.map((span, i) => <p key={i}>{span.quote}{span.page ? `（第 ${span.page} 页）` : ''}</p>)}
          </blockquote>)}
        </details>
      </section>)}
      <ReferencesSection references={report.references} />
    </> : (report.references ?? []).length === 0 ? <p className="survey-empty">这是旧版无材料记录，尚未保存可审计的检索明细；可点击“重新生成”生成零命中报告。</p> : <>
      <nav className="survey-toc" aria-label="报告目录">{[['scope', '摘要与范围'], ['taxonomy', '技术分类'], ['frameworks', '评估框架'], ['metrics', '维度与指标'], ['tasks', '任务与数据集'], ['experiments', '实验比较'], ['limits', '局限'], ['references', '参考文献']].map(([id, label]) => <a key={id} href={`#survey-${id}`}>{label}</a>)}</nav>
      <section id="survey-scope" className="survey-section"><h3><FileText size={18} />摘要与研究范围</h3>
        <EvidenceMarkdown text={report.summary || '本次材料尚未形成跨文献摘要。'} papers={report.references} citations={citations} />
        {report.findings.length > 0 && <><h4>跨文献发现</h4><ul>{report.findings.map((text, i) => <li key={i}><EvidenceMarkdown text={text} papers={report.references} citations={citations} /></li>)}</ul></>}
        <dl className="survey-stat-strip">{[['材料总量', report.coverage?.corpus ?? 0], ['已提取证据', report.coverage?.examined ?? 0], ['全文片段', report.coverage?.full_text ?? 0], ['仅摘要', report.coverage?.abstract_only ?? 0]].map(([name, count]) => <div key={name}><dt>{name}</dt><dd>{count}<small> 篇</small></dd></div>)}</dl>
        <div className="survey-year-chart"><h4>本次材料的发表年份分布</h4><ResponsiveContainer width="100%" height={190}><BarChart data={years} margin={{ top: 12, right: 12, left: -18, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" /><XAxis dataKey="year" tick={{ fill: 'var(--text-secondary)', fontSize: 11 }} /><YAxis allowDecimals={false} tick={{ fill: 'var(--text-secondary)', fontSize: 11 }} /><Tooltip /><Bar dataKey="count" name="论文数" fill="#288b7d" maxBarSize={44} />
        </BarChart></ResponsiveContainer></div>
      </section>
      <section id="survey-taxonomy" className="survey-section"><h3><ListTree size={18} />技术分类与研究范式</h3><SummaryTable rows={report.tables?.taxonomy ?? []} columns={['category', 'description']} references={references} caption="Technical Taxonomy" /></section>
      <section id="survey-frameworks" className="survey-section"><h3><FlaskConical size={18} />评估框架汇总</h3><SummaryTable rows={report.tables?.frameworks ?? []} columns={['name', 'kind', 'target', 'aspects', 'metrics']} references={references} caption="Summary of Evaluation Frameworks" /></section>
      <section id="survey-metrics" className="survey-section"><h3><Grid3X3 size={18} />评估维度与指标</h3><SummaryTable rows={report.tables?.metrics ?? []} columns={['aspect', 'metric', 'description']} references={references} caption="Summary of Metrics Applicable to Evaluation Aspects" />
        {report.matrices?.aspect_metric && <SurveyHeatmap matrix={report.matrices.aspect_metric} title="评估维度 × 指标" references={references} />}
      </section>
      <section id="survey-tasks" className="survey-section"><h3><Table2 size={18} />下游任务与数据集</h3><SummaryTable rows={report.tables?.tasks ?? []} columns={['task', 'subtask', 'dataset', 'method']} references={references} caption="Downstream Tasks and Datasets" />
        {report.matrices?.task_dataset && <SurveyHeatmap matrix={report.matrices.task_dataset} title="下游任务 × 数据集" references={references} />}
      </section>
      <section id="survey-experiments" className="survey-section"><h3><FlaskConical size={18} />实验结果、条件与可比性</h3><SummaryTable rows={report.tables?.experimental_comparisons ?? []} columns={['method', 'baseline_method', 'task', 'dataset', 'split', 'metric', 'value', 'baseline_value', 'unit', 'metric_direction', 'hardware', 'batch_size', 'input_size', 'precision', 'comparison_limit']} references={references} caption="Experimental Comparisons and Conditions" /></section>
      <section id="survey-limits" className="survey-section"><h3><Layers size={18} />局限与未解决问题</h3>
        <ul>{[...(report.limitations ?? report.gaps ?? []), ...report.warnings].map((text, i) => <li key={i}><EvidenceMarkdown text={text} papers={report.references} citations={citations} /></li>)}</ul>
        {((report.coverage?.failed ?? 0) > 0 || (report.coverage?.rejected_rows ?? 0) > 0) && <p className="survey-note">提取失败 {report.coverage?.failed ?? 0} 篇；原文、字段或关系复核未通过 {report.coverage?.rejected_rows ?? 0} 条。</p>}
        <p className="survey-note">报告生成后流程结束，未进行额外真实性核验。</p>
      </section>
      <ReferencesSection references={report.references} />
    </>}
  </article>
}
