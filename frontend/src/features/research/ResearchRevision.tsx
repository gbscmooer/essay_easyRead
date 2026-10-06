import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { getResearchVersions, pauseExploration, reviseResearch } from '@/api/exploration'
import { getWorkspace, listWorkspaces } from '@/api/workspaces'
import type { WorkspaceItem, WorkspacePaper } from '@/api/workspaces'
import type { ExplorationDepth, ExplorationRun } from '@/types'
import type { ResearchVersion, ResearchVersions } from '@/types/researchRevision'

const statusLabels: Record<string, string> = { pending: '准备中', running: '运行中', paused: '已暂停', completed: '已完成', partial: '部分完成', failed: '执行失败', cancelled: '已停止' }

export function ResearchRevision({ run, onCreated }: { run: ExplorationRun; onCreated: (run: ExplorationRun) => void }) {
  const [versions, setVersions] = useState<ResearchVersions | null>(null)
  const [editing, setEditing] = useState(false)
  const [question, setQuestion] = useState(run.question)
  const [extra, setExtra] = useState('')
  const [reuse, setReuse] = useState(true)
  const [excluded, setExcluded] = useState<string[]>([])
  const [depth, setDepth] = useState<ExplorationDepth>(run.depth)
  const [online, setOnline] = useState(run.options?.search_online !== false)
  const [workspaces, setWorkspaces] = useState<WorkspaceItem[]>([])
  const [workspace, setWorkspace] = useState('')
  const [papers, setPapers] = useState<WorkspacePaper[]>([])
  const [selected, setSelected] = useState<number[]>([])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const lifetime = useRef<AbortController | null>(null)
  const lastStatus = useRef(run.status)

  useEffect(() => {
    const controller = new AbortController()
    lifetime.current = controller
    setVersions(null); setQuestion(run.question); setEditing(false); setExcluded([]); setExtra('')
    setReuse(true); setDepth(run.depth); setOnline(run.options?.search_online !== false)
    setWorkspace(''); setPapers([]); setSelected([]); setBusy(false); setMessage(''); setError('')
    void getResearchVersions(run.id, controller.signal).then(value => {
      if (!controller.signal.aborted) setVersions(value)
    }).catch(() => { if (!controller.signal.aborted) setError('修订信息暂时不可用，请重新打开研究后重试') })
    return () => controller.abort()
  }, [run.id, run.question, run.depth])

  useEffect(() => {
    const controller = new AbortController()
    let loading = false
    const load = async () => {
      if (loading) return
      loading = true
      try {
        const value = await getResearchVersions(run.id, controller.signal)
        if (!controller.signal.aborted) setVersions(value)
      } catch { /* Existing metadata stays usable; submission always checks fresh state. */ }
      finally { loading = false }
    }
    if (lastStatus.current !== run.status) void load()
    lastStatus.current = run.status
    const timer = ['running', 'pending'].includes(run.status) ? window.setInterval(() => void load(), 5000) : null
    return () => { controller.abort(); if (timer !== null) window.clearInterval(timer) }
  }, [run.id, run.status])

  useEffect(() => {
    if (!editing) return
    let alive = true
    void listWorkspaces().then(value => { if (alive) setWorkspaces(value) })
      .catch(() => { if (alive) setError('工作区列表暂时不可用') })
    return () => { alive = false }
  }, [editing])

  useEffect(() => {
    let alive = true
    setSelected([]); setPapers([])
    if (workspace) void getWorkspace(Number(workspace)).then(value => { if (alive) setPapers(value.papers) })
      .catch(() => { if (alive) setError('补充论文列表暂时不可用') })
    return () => { alive = false }
  }, [workspace])

  async function submit() {
    if (busy || !question.trim()) return
    const controller = lifetime.current
    if (!controller || controller.signal.aborted) return
    setBusy(true); setError(''); setMessage('正在检查当前研究状态…')
    try {
      let current = await getResearchVersions(run.id, controller.signal)
      if (['running', 'pending'].includes(current.status)) {
        setMessage('正在暂停研究，等待当前工作结束…')
        await pauseExploration(run.id)
      }
      const deadline = Date.now() + 200000
      while (current.worker_active || ['running', 'pending'].includes(current.status)) {
        if (controller.signal.aborted) return
        if (Date.now() > deadline) throw new Error('当前工作尚未结束。旧研究已保留，请稍后重试生成修订版。')
        await new Promise<void>(resolve => window.setTimeout(resolve, 1000))
        current = await getResearchVersions(run.id, controller.signal)
      }
      if (controller.signal.aborted) return
      setMessage('正在创建修订版，材料会重新筛选与分析…')
      const child = await reviseResearch(run.id, {
        question: question.trim(), expected_state_version: current.state_version, depth,
        additional_questions: extra.split('\n').map(value => value.trim()).filter(Boolean),
        reuse_materials: reuse, exclude_asset_ids: excluded, search_online: online,
        ...(selected.length ? { workspace_id: Number(workspace), seed_paper_ids: selected } : {}),
      })
      if (!controller.signal.aborted) onCreated(child)
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : '修订失败，请刷新后重试')
    } finally {
      if (!controller.signal.aborted) { setBusy(false); setMessage('') }
    }
  }

  function versionLink(value: ResearchVersion) {
    const tokens = value.usage?.find(item => item.resource === 'llm_tokens')
    return <li key={value.run_id}><Link to={`/explorer/${value.run_id}`}>研究 #{value.run_id}：{value.question}</Link>
      {' · '}{statusLabels[value.status] ?? value.status}{tokens ? ` · ${tokens.usage_known ? '' : '保守记账 '}${tokens.used.toLocaleString()} token` : ' · 尚无用量账本记录'}</li>
  }

  return <section className="survey-section research-revision" aria-label="研究修订与版本">
    <h3>补充或修正研究</h3>
    <p>修订会保留旧报告，生成独立的新研究版本。复用文献会重新筛选、取证；新版本产生单独用量。</p>
    {!!versions?.ancestors?.length && <><p>此前版本</p><ul>{versions.ancestors.map(versionLink)}</ul></>}
    {!!versions?.children?.length && <><p>由此生成的修订版</p><ul>{versions.children.map(versionLink)}</ul></>}
    {error && <p role="alert">{error}</p>}
    {message && <p role="status">{message}</p>}
    {!editing ? <button type="button" disabled={!versions} onClick={() => setEditing(true)}>修改问题或材料</button> : <form onSubmit={event => { event.preventDefault(); void submit() }}>
      <fieldset disabled={busy}>
        <label>修订后的研究问题<textarea value={question} maxLength={4000} required minLength={2} onChange={event => setQuestion(event.target.value)} /></label>
        <label>补充问题（每行一项，最多7项）<textarea value={extra} onChange={event => setExtra(event.target.value)} /></label>
        <label>研究深度<select value={depth} onChange={event => setDepth(event.target.value as ExplorationDepth)}>
          <option value="lite">轻量</option><option value="quick">快览</option><option value="standard">标准</option><option value="deep">深入</option>
        </select></label>
        <label><input type="checkbox" checked={online} onChange={event => setOnline(event.target.checked)} />联网补充材料</label>
        <label><input type="checkbox" checked={reuse} onChange={event => setReuse(event.target.checked)} />复用已有原始文献</label>
        {!!versions?.materials?.length && <details><summary>排除已有文献（后续检索也会排除）</summary>
          {versions.materials.map(material => <label key={material.asset_id}><input type="checkbox" checked={excluded.includes(material.asset_id)}
            onChange={event => setExcluded(event.target.checked ? [...excluded, material.asset_id] : excluded.filter(id => id !== material.asset_id))} />
            {material.title}（{material.source_kind === 'abstract' ? '摘要' : '全文'}）</label>)}
        </details>}
        <label>加入工作区论文<select value={workspace} onChange={event => setWorkspace(event.target.value)}><option value="">不增加</option>
          {workspaces.map(value => <option key={value.id} value={value.id}>{value.name}</option>)}
        </select></label>
        {papers.map(paper => <label key={paper.id}><input type="checkbox" checked={selected.includes(paper.id)}
          onChange={event => setSelected(event.target.checked ? [...selected, paper.id] : selected.filter(id => id !== paper.id))} />{paper.title || paper.filename}</label>)}
        <button type="submit" disabled={!versions}>{['running', 'pending'].includes(run.status) ? '暂停并生成修订版' : '生成修订版'}</button>
        <button type="button" onClick={() => setEditing(false)}>收起</button>
      </fieldset>
    </form>}
  </section>
}
