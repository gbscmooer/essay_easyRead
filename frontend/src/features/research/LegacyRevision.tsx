import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getLegacyVersions, reviseResearch } from '@/api/exploration'
import type { ExplorationDepth, ExplorationPaper, ExplorationRun } from '@/types'
import type { LegacyVersionRow, LegacyVersions } from '@/types/researchRevision'

const VERDICT_LABELS: Record<string, string> = {
  done_extensively: '充分实现', done_partial: '部分实现', emerging: '近期新兴', gap: '未见实现', unclear: '证据不足',
}
const MAX_LISTED = 30

/** 八阶段研究的修正/补充：改问题、加条件、剔除文献，生成新版本并复用已召回材料。 */
export function LegacyRevision({ run, papers, disabled, onCreated }: {
  run: ExplorationRun
  papers: ExplorationPaper[]
  disabled: boolean
  onCreated: (run: ExplorationRun) => void
}) {
  const [versions, setVersions] = useState<LegacyVersions | null>(null)
  const [editing, setEditing] = useState(false)
  const [question, setQuestion] = useState(run.question)
  const [extra, setExtra] = useState('')
  const [reuse, setReuse] = useState(true)
  const [online, setOnline] = useState(run.options?.search_online !== false)
  const [depth, setDepth] = useState<ExplorationDepth>(run.depth)
  const [excluded, setExcluded] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    setVersions(null); setEditing(false); setQuestion(run.question); setExtra(''); setExcluded([]); setError('')
    void getLegacyVersions(run.id, controller.signal).then(v => { if (!controller.signal.aborted) setVersions(v) })
      .catch(() => { /* 版本链只是辅助信息，读取失败不影响修正 */ })
    return () => controller.abort()
  }, [run.id, run.question])

  const submit = async () => {
    setBusy(true); setError('')
    try {
      const child = await reviseResearch(run.id, {
        question: question.trim(), expected_state_version: run.state_version ?? 0, depth,
        additional_questions: extra.split('\n').map(s => s.trim()).filter(s => s.length >= 2).slice(0, 7),
        reuse_materials: reuse, exclude_asset_ids: excluded, search_online: online,
      })
      onCreated(child)
    } catch (e) {
      setError(e instanceof Error ? e.message : '修正提交失败')
    } finally {
      setBusy(false)
    }
  }

  const row = (v: LegacyVersionRow) => (
    <li key={v.id}>
      {v.id === run.id ? <strong>#{v.id}（当前）</strong> : <Link to={`/explorer/${v.id}`}>#{v.id}</Link>}
      {' '}{v.question.split('\n')[0]}
      {v.verdict && <> · {VERDICT_LABELS[v.verdict] ?? v.verdict}</>}
      {v.conditions.length > 0 && <> · 补充条件 {v.conditions.length} 项</>}
    </li>
  )
  const chain = versions ? [...versions.ancestors, versions.current, ...versions.children] : []

  return (
    <section className="survey-section research-revision" aria-label="修正与补充">
      <h3>修正与补充</h3>
      <p className="survey-note">修改问题、追加条件或剔除不相关文献后生成新版本；复用本次已召回的文献并按新问题重新筛选，原版本保留可对照。</p>
      {chain.length > 1 && <ol className="research-revision__chain" aria-label="版本链">{chain.map(row)}</ol>}
      {!editing
        ? <button type="button" className="er-btn er-btn--secondary er-btn--sm" disabled={disabled} onClick={() => setEditing(true)}>修正问题或材料</button>
        : <form onSubmit={e => { e.preventDefault(); void submit() }}>
          <label>修正后的研究问题<textarea value={question} maxLength={4000} required minLength={2} onChange={e => setQuestion(e.target.value)} /></label>
          <label>补充条件（每行一项，最多 7 项）<textarea value={extra} onChange={e => setExtra(e.target.value)} placeholder="例如：只看 2023 年以后的工作" /></label>
          <label>研究深度<select value={depth} onChange={e => setDepth(e.target.value as ExplorationDepth)}>
            <option value="lite">轻量</option><option value="quick">快览</option><option value="standard">标准</option><option value="deep">深挖</option>
          </select></label>
          <label><input type="checkbox" checked={reuse} onChange={e => setReuse(e.target.checked)} />复用本次已召回文献</label>
          <label><input type="checkbox" checked={online} onChange={e => setOnline(e.target.checked)} />联网补充检索</label>
          {papers.length > 0 && <fieldset>
            <legend>剔除文献（新版本不再使用）</legend>
            {papers.slice(0, MAX_LISTED).map(p => <label key={p.key}>
              <input type="checkbox" checked={excluded.includes(p.key)}
                onChange={e => setExcluded(cur => e.target.checked ? [...cur, p.key] : cur.filter(k => k !== p.key))} />
              {p.title}{p.year ? `（${p.year}）` : ''}
            </label>)}
          </fieldset>}
          {error && <p role="alert" className="survey-note">{error}</p>}
          <div className="ex-empty-warning__actions">
            <button type="submit" className="er-btn er-btn--primary er-btn--sm" disabled={busy || question.trim().length < 2 || (!reuse && !online)}>
              {busy ? '提交中…' : '生成修正版本'}
            </button>
            <button type="button" className="er-btn er-btn--secondary er-btn--sm" onClick={() => setEditing(false)}>取消</button>
          </div>
        </form>}
    </section>
  )
}
