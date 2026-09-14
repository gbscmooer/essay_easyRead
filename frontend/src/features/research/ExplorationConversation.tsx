import { useEffect, useRef, useState } from 'react'
import { FileText, Send, RotateCcw } from 'lucide-react'
import { askExploration, getExplorationMessages } from '@/api/exploration'
import type { ExplorationMessage, ExplorationPaper } from '@/types'
import { EvidenceMarkdown, PaperTitle } from './paperLinks'
import { EvidenceAuditPanel } from './EvidenceAuditPanel'

export function ExplorationConversation({ runId, papers, disabled, onMessages, question, setQuestion, selected, onClearSelection }: {
  runId: number; papers: ExplorationPaper[]; disabled: boolean
  onMessages: (messages: ExplorationMessage[]) => void
  question: string; setQuestion: (s: string) => void
  selected: ExplorationPaper | null; onClearSelection: () => void
}) {
  const [messages, setMessages] = useState<ExplorationMessage[]>([])
  const [searchMore, setSearchMore] = useState(false)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const alive = useRef(true)
  const active = messages.some(m => m.status === 'pending' || m.status === 'running')
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false }
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    const load = async () => {
      try {
        const rows = await getExplorationMessages(runId, controller.signal)
        if (controller.signal.aborted) return
        setMessages(rows); onMessages(rows); setLoaded(true); setError('')
        if (rows.some(m => ['pending', 'running'].includes(m.status))) timer = setTimeout(load, 2000)
      } catch (e) {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : '对话加载失败')
      }
    }
    void load()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [runId, refresh, onMessages])

  const send = async (text = question, search = searchMore, keys = selected ? [selected.key] : []) => {
    if (!text.trim() || sending || active || disabled || !loaded) return
    setSending(true); setError('')
    try {
      const row = await askExploration(runId, text.trim(), search, keys)
      if (!alive.current) return
      setMessages(old => [...old, row]); setQuestion(''); onClearSelection()
      setRefresh(n => n + 1)
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : '发送失败')
    } finally { if (alive.current) setSending(false) }
  }

  return <section className="ex-conversation" aria-label="连续追问">
    <div className="ex-chat-log" aria-live="polite">
      {!loaded && !error && <p>正在加载对话…</p>}
      {loaded && messages.length === 0 && <p className="ex-chat-empty">暂无追问</p>}
      {messages.map(m => <article key={m.id} className="ex-chat-turn">
        <header><strong>你</strong><time>{new Date(m.created_at).toLocaleString()}</time></header>
        <p className="ex-chat-question">{m.question}</p>
        <strong>AI</strong>
        {m.status === 'done' && <div className="ex-report"><EvidenceMarkdown text={m.answer} papers={papers} citations={m.context.citations} /></div>}
        {m.status === 'done' && <EvidenceAuditPanel audit={m.context.evidence_audit} />}
        {['pending', 'running'].includes(m.status) && <p role="status">{m.search_more ? '正在检索资料并回答…' : '正在分析已有资料…'}</p>}
        {m.status === 'failed' && <div role="alert">{m.error_message}
          <button type="button" className="ex-icon" title="重试追问" aria-label="重试追问" disabled={active || sending || disabled}
            onClick={() => void send(m.question, m.search_more, m.selected_keys)}><RotateCcw size={17} /></button>
        </div>}
        {!!m.evidence_keys.length && <ul className="ex-chat-sources">{m.evidence_keys.map(k => {
          const p = papers.find(paper => paper.key === k)
          return p ? <li key={k}><FileText size={14} /><PaperTitle paper={p} /></li> : null
        })}</ul>}
        {!!m.context.queries?.length && <details><summary>本轮检索资料（{m.papers?.length ?? 0} 篇）</summary>
          {m.context.queries.map(q => <p key={q}><code>{q}</code></p>)}
          <ul>{(m.papers ?? []).map(p => <li key={p.key}><PaperTitle paper={p} /></li>)}</ul>
        </details>}
        {m.context.warnings?.map(w => <p key={w} className="ex-transparency__warn">{w}</p>)}
      </article>)}
    </div>
    {error && <div role="alert" className="ex-empty--error">{error}<button type="button" className="ex-icon" title="重新加载对话" aria-label="重新加载对话" onClick={() => setRefresh(n => n + 1)}><RotateCcw size={17} /></button></div>}
    <form className="ex-chat-compose" onSubmit={e => { e.preventDefault(); void send() }}>
      {selected && <div className="ex-chat-selection">针对：{selected.title}<button type="button" onClick={onClearSelection}>取消</button></div>}
      <textarea aria-label="继续提问" value={question} maxLength={4000} rows={3} placeholder="继续提问…"
        onChange={e => setQuestion(e.target.value)} disabled={disabled || sending || active}
        onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void send() } }} />
      <div className="ex-chat-compose-actions">
        <label><input type="checkbox" checked={searchMore} onChange={e => setSearchMore(e.target.checked)} disabled={sending || active} />补充检索</label>
        <button type="submit" className="ex-ask__run" disabled={!loaded || !!error || disabled || sending || active || !question.trim()}><Send size={17} />{sending || active ? '回答中…' : '发送'}</button>
      </div>
    </form>
  </section>
}
