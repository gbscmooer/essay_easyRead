import { useState } from 'react'
import { adjustExploration } from '@/api/exploration'
import type { ExplorationPaper } from '@/types'

const MAX_LISTED = 40
const STAGE_LABELS: Record<string, string> = { screen: '筛选', select: '选材', extract: '证据提取', synthesize: '综合', report: '报告' }

/** 暂停时干预（八阶段）：剔除文献、追加检索式；点击“继续研究”后生效。 */
export function PauseAdjust({ runId, papers }: { runId: number; papers: ExplorationPaper[] }) {
  const [open, setOpen] = useState(false)
  const [queries, setQueries] = useState('')
  const [note, setNote] = useState('')
  const [excluded, setExcluded] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState('')
  const [error, setError] = useState('')
  const addQueries = queries.split('\n').map(s => s.trim()).filter(s => s.length >= 3).slice(0, 5)

  const submit = async () => {
    setBusy(true); setError(''); setSaved('')
    try {
      const out = await adjustExploration(runId, { exclude_keys: excluded, add_queries: addQueries, note: note.trim() })
      const redo = out.intervention.invalidated_stages.map(stage => STAGE_LABELS[stage] ?? stage)
      setSaved(`已保存调整${redo.length ? `，继续时将重新执行：${redo.join('、')}` : ''}。点击“继续研究”生效。`)
      setExcluded([]); setQueries(''); setNote(''); setOpen(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : '调整保存失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="survey-section research-revision" aria-label="暂停时调整">
      <h3>暂停时调整检索</h3>
      <p className="survey-note">研究已在安全点暂停。可以剔除不相关的文献或追加检索式，继续研究时按调整后的材料重新筛选与分析。</p>
      {saved && <p className="survey-note" role="status">{saved}</p>}
      {!open
        ? <button type="button" className="er-btn er-btn--secondary er-btn--sm" onClick={() => setOpen(true)}>调整检索与材料</button>
        : <form onSubmit={e => { e.preventDefault(); void submit() }}>
          <label>追加英文检索式（每行一条，最多 5 条）<textarea value={queries} onChange={e => setQueries(e.target.value)} placeholder="例如：laparoscopic surgery video vision language model" /></label>
          {papers.length > 0 && <fieldset>
            <legend>剔除文献（已召回 {papers.length} 篇，列出前 {Math.min(MAX_LISTED, papers.length)} 篇）</legend>
            {papers.slice(0, MAX_LISTED).map(p => <label key={p.key}>
              <input type="checkbox" checked={excluded.includes(p.key)}
                onChange={e => setExcluded(cur => e.target.checked ? [...cur, p.key] : cur.filter(k => k !== p.key))} />
              {p.title}{p.year ? `（${p.year}）` : ''}
            </label>)}
          </fieldset>}
          <label>调整说明（可选）<textarea value={note} maxLength={500} onChange={e => setNote(e.target.value)} /></label>
          {error && <p role="alert" className="survey-note">{error}</p>}
          <div className="ex-empty-warning__actions">
            <button type="submit" className="er-btn er-btn--primary er-btn--sm" disabled={busy || (!excluded.length && !addQueries.length)}>
              {busy ? '保存中…' : '保存调整'}
            </button>
            <button type="button" className="er-btn er-btn--secondary er-btn--sm" onClick={() => setOpen(false)}>取消</button>
          </div>
        </form>}
    </section>
  )
}
