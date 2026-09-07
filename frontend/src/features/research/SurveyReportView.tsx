import { useMemo, useState } from 'react'
import { BookOpen, ChevronLeft, ChevronRight, Download, FileText, FlaskConical, Grid3X3, Layers, ListTree, RefreshCw, Table2 } from 'lucide-react'
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
}

function Evidence({ items, references }: { items: SurveyEvidence[]; references: Map<string, Ref> }) {
  return <details className="survey-evidence"><summary>{new Set(items.map(x => x.paper_key)).size} 篇来源</summary>
    {items.map((e, i) => <div key={i}>
      {references.has(e.paper_key) && <PaperTitle paper={references.get(e.paper_key)!} />}
      <span className="survey-source-kind">{e.source_kind === 'abstract' ? '摘要' : '全文片段'}</span>
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

export function SurveyReportView({ report, runId, busy, onGenerate, error }: {
  report?: SurveyReport; runId: number; busy: boolean; onGenerate: () => void; error?: string
}) {
  const references = useMemo(() => new Map((report?.references ?? []).map(p => [p.key, p])), [report])
  const citations = useMemo(() => Object.fromEntries((report?.references ?? []).map((p, i) => [String(i + 1), p.key])), [report])
  const years = useMemo(() => Object.entries(report?.bibliometrics.by_year ?? {}).sort(([a], [b]) => Number(a) - Number(b)).map(([year, count]) => ({ year, count })), [report])
  return <article className="survey-report" aria-label="研究报告">
    <div className="survey-toolbar"><div><BookOpen size={20} /><h2>研究报告</h2>{report && <span>{report.status === 'completed' ? '已生成' : report.status === 'partial' ? '部分完成' : '证据表待生成'}</span>}</div>
      <div>
        {report && <><a href={`/api/exploration/runs/${runId}/report?format=html`} title="下载含图表的 HTML 报告" aria-label="下载 HTML 报告"><Download size={16} /><span>HTML</span></a>
          <a href={`/api/exploration/runs/${runId}/report?format=markdown`} title="下载 Markdown 报告" aria-label="下载 Markdown 报告"><FileText size={16} /><span>Markdown</span></a></>}
        <button type="button" onClick={onGenerate} disabled={busy} title={report ? '从已有材料重新生成报告' : '从已有材料生成报告'}><RefreshCw size={16} />{busy ? '生成中' : report ? '重新生成' : '生成报告'}</button>
      </div>
    </div>
    {error && <p role="alert" className="survey-error">{error}</p>}
    {!report ? <p className="survey-empty">{busy ? '报告生成中，已取得的资料与统计可在其他视图查看。' : '这条研究尚未生成结构化报告。已有资料可以继续使用。'}</p> : <>
      <nav className="survey-toc" aria-label="报告目录">{[['scope', '摘要与范围'], ['taxonomy', '技术分类'], ['frameworks', '评估框架'], ['metrics', '维度与指标'], ['tasks', '任务与数据集'], ['limits', '局限'], ['references', '参考文献']].map(([id, label]) => <a key={id} href={`#survey-${id}`}>{label}</a>)}</nav>
      <section id="survey-scope" className="survey-section"><h3><FileText size={18} />摘要与研究范围</h3>
        <EvidenceMarkdown text={report.summary || '本次材料尚未形成跨文献摘要。'} papers={report.references} citations={citations} />
        {report.findings.length > 0 && <><h4>跨文献发现</h4><ul>{report.findings.map((text, i) => <li key={i}><EvidenceMarkdown text={text} papers={report.references} citations={citations} /></li>)}</ul></>}
        <dl className="survey-stat-strip">{[['材料总量', report.coverage.corpus], ['已提取证据', report.coverage.examined], ['全文片段', report.coverage.full_text], ['仅摘要', report.coverage.abstract_only]].map(([name, count]) => <div key={name}><dt>{name}</dt><dd>{count}<small> 篇</small></dd></div>)}</dl>
        <div className="survey-year-chart"><h4>本次材料的发表年份分布</h4><ResponsiveContainer width="100%" height={190}><BarChart data={years} margin={{ top: 12, right: 12, left: -18, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" /><XAxis dataKey="year" tick={{ fill: 'var(--text-secondary)', fontSize: 11 }} /><YAxis allowDecimals={false} tick={{ fill: 'var(--text-secondary)', fontSize: 11 }} /><Tooltip /><Bar dataKey="count" name="论文数" fill="#288b7d" maxBarSize={44} />
        </BarChart></ResponsiveContainer></div>
      </section>
      <section id="survey-taxonomy" className="survey-section"><h3><ListTree size={18} />技术分类与研究范式</h3><SummaryTable rows={report.tables.taxonomy} columns={['category', 'description']} references={references} caption="Technical Taxonomy" /></section>
      <section id="survey-frameworks" className="survey-section"><h3><FlaskConical size={18} />评估框架汇总</h3><SummaryTable rows={report.tables.frameworks} columns={['name', 'kind', 'target', 'aspects', 'metrics']} references={references} caption="Summary of Evaluation Frameworks" /></section>
      <section id="survey-metrics" className="survey-section"><h3><Grid3X3 size={18} />评估维度与指标</h3><SummaryTable rows={report.tables.metrics} columns={['aspect', 'metric', 'description']} references={references} caption="Summary of Metrics Applicable to Evaluation Aspects" />
        <SurveyHeatmap matrix={report.matrices.aspect_metric} title="评估维度 × 指标" references={references} />
      </section>
      <section id="survey-tasks" className="survey-section"><h3><Table2 size={18} />下游任务与数据集</h3><SummaryTable rows={report.tables.tasks} columns={['task', 'subtask', 'dataset', 'method']} references={references} caption="Downstream Tasks and Datasets" />
        <SurveyHeatmap matrix={report.matrices.task_dataset} title="下游任务 × 数据集" references={references} />
      </section>
      <section id="survey-limits" className="survey-section"><h3><Layers size={18} />局限与未解决问题</h3>
        <ul>{[...report.gaps, ...report.warnings].map((text, i) => <li key={i}><EvidenceMarkdown text={text} papers={report.references} citations={citations} /></li>)}</ul>
        {(report.coverage.failed > 0 || report.coverage.rejected_rows > 0) && <p className="survey-note">提取失败 {report.coverage.failed} 篇；原文、字段或关系复核未通过 {report.coverage.rejected_rows} 条。</p>}
      </section>
      <section id="survey-references" className="survey-section"><h3><BookOpen size={18} />参考文献</h3><ol className="survey-references">{report.references.map(p => <li key={p.key}><PaperTitle paper={p} /><span>{p.year ?? '年份未记录'}</span></li>)}</ol></section>
    </>}
  </article>
}
