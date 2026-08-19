import { useCallback, useEffect, useRef, useState } from 'react'
import type { RagQueryResponse } from '@/types'
import { queryRag } from '@/api/research'
import { CitationChip } from './CitationChip'
import { LoadingSpinner } from '@/ds-entry'
import { toast } from '@/hooks/useToast'

type RagMode = 'vector' | 'hybrid' | 'graph'

/** 一轮问答 —— 问题、模式与答案绑在一起，翻回去时对得上。 */
interface RagTurn {
  id: number
  question: string
  mode: RagMode
  result: RagQueryResponse
}

/** 会话内保留多少轮问答（只在内存里，不落盘）。 */
const MAX_TURNS = 10

const MODE_LABEL: Record<RagMode, string> = {
  vector: '向量',
  hybrid: '混合',
  graph: '图谱',
}

interface RagQueryBoxProps {
  wsId: number
  paperIds?: number[]
  topN?: number
}

export function RagQueryBox({ wsId, paperIds, topN = 5 }: RagQueryBoxProps) {
  const [question, setQuestion] = useState('')
  const [mode, setMode] = useState<RagMode>('vector')
  const [loading, setLoading] = useState(false)
  /* 每轮问答留在列表里。此前新问题会把上一条答案连同引用一起清空，
     而跨论文问答本来就是反复追问的场景 —— 上一轮的证据链不该被冲掉。 */
  const [turns, setTurns] = useState<RagTurn[]>([])
  const [error, setError] = useState<string | null>(null)
  const [showDebug, setShowDebug] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const turnIdRef = useRef(0)

  // 离开面板时中止在途查询，不让回调打在已卸载的组件上
  useEffect(() => () => abortRef.current?.abort(), [])

  const handleQuery = async () => {
    const asked = question.trim()
    if (!asked) return

    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setLoading(true)
    setError(null)
    try {
      const res = await queryRag(wsId, asked, topN, paperIds, mode, controller.signal)
      if (controller.signal.aborted) return
      turnIdRef.current += 1
      setTurns(prev => [{ id: turnIdRef.current, question: asked, mode, result: res }, ...prev].slice(0, MAX_TURNS))
      setQuestion('')
    } catch (e: unknown) {
      if (controller.signal.aborted) return
      setError(e instanceof Error ? e.message : '查询失败')
    } finally {
      if (!controller.signal.aborted) setLoading(false)
      if (abortRef.current === controller) abortRef.current = null
    }
  }

  const handleAbort = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setLoading(false)
  }, [])

  const copyAnswer = useCallback(async (turn: RagTurn) => {
    const citations = turn.result.citations.map(
      (c, i) => `[${i + 1}] ${c.paper_title}${c.section_name ? ` · ${c.section_name}` : ''}`,
    )
    const text = [`Q: ${turn.question}`, '', turn.result.answer, ...(citations.length ? ['', '引用来源：', ...citations] : [])].join('\n')
    try {
      await navigator.clipboard.writeText(text)
      toast({ type: 'success', message: '已复制答案与引用' })
    } catch {
      toast({ type: 'error', message: '复制失败，请手动选择文本' })
    }
  }, [])

  return (
    <aside className="rs-rag" aria-label="跨论文问答">
      <div className="rs-rag__head">
        <h3 className="rs-rag__title">跨论文问答</h3>
        <div className="flex overflow-hidden border text-[10px]" style={{ borderColor: 'var(--border)' }}>
          {([
            { key: 'vector' as const, label: '向量' },
            { key: 'hybrid' as const, label: '混合' },
            { key: 'graph' as const, label: '图谱' },
          ]).map(opt => (
            <button
              key={opt.key}
              type="button"
              onClick={() => setMode(opt.key)}
              className="px-2 py-0.5 transition-colors"
              style={
                mode === opt.key
                  ? { backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }
                  : { color: 'var(--text-muted)' }
              }
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      <div className="p-3" style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="flex gap-2">
          <input
            type="text"
            value={question}
            onChange={e => setQuestion(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleQuery()}
            placeholder="输入你的问题…"
            aria-label="跨论文问答输入"
            className="flex-1 px-3 py-2 text-[12px] focus:outline-none transition-colors duration-200"
            style={{
              backgroundColor: 'var(--bg-elevated)',
              border: '1px solid var(--border)',
              color: 'var(--text-primary)',
            }}
            onFocus={e => { e.currentTarget.style.borderColor = 'var(--accent)' }}
            onBlur={e => { e.currentTarget.style.borderColor = 'var(--border)' }}
          />
          {loading ? (
            <button
              type="button"
              onClick={handleAbort}
              className="focus-glow press px-3.5 py-2 text-[12px] font-medium transition-opacity hover:opacity-90"
              style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
              title="中止本次检索"
            >
              中止
            </button>
          ) : (
          <button
            type="button"
            onClick={handleQuery}
            disabled={loading || !question.trim()}
            className="focus-glow press px-3.5 py-2 text-[12px] font-medium transition-opacity disabled:opacity-50 disabled:cursor-not-allowed hover:opacity-90"
            style={{ backgroundColor: 'var(--accent)', color: '#ffffff' }}
          >
            提问
          </button>
          )}
        </div>
        <p className="text-[10px] mt-1.5" style={{ color: 'var(--text-muted)' }}>
          {mode === 'graph'
            ? '图谱模式：向量 + BM25 + 图谱 PPR 融合，并附加路径解释。'
            : mode === 'hybrid'
              ? '混合模式：Dense + BM25 融合后重排序。'
              : '向量模式：兼容原有 Dense 检索与重排序。'}
        </p>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {error && (
          <p className="text-[12px]" style={{ color: 'var(--error)' }}>{error}</p>
        )}

        {loading && (
          <div className="flex items-center gap-2">
            <LoadingSpinner size="xs" />
            <span className="text-[12px]" style={{ color: 'var(--text-muted)' }}>检索中…</span>
          </div>
        )}

        {turns.map(turn => {
          const hasDebug = turn.result.citations.some(c => c.debug) || (turn.result.debug?.length ?? 0) > 0
          return (
            <article key={turn.id} className="space-y-2" style={{ borderTop: '1px solid var(--border)', paddingTop: 12 }}>
              <header className="flex items-start justify-between gap-2">
                <p className="text-[12px] font-medium leading-snug" style={{ color: 'var(--text-secondary)' }}>
                  <span style={{ color: 'var(--text-muted)' }}>Q · {MODE_LABEL[turn.mode]}　</span>
                  {turn.question}
                </p>
                <button
                  type="button"
                  onClick={() => copyAnswer(turn)}
                  className="focus-glow shrink-0 text-[10px] px-1.5 py-0.5"
                  style={{ border: '1px solid var(--border)', color: 'var(--text-muted)' }}
                  title="复制答案与引用"
                >
                  复制
                </button>
              </header>

              <p className="text-[13px] leading-relaxed" style={{ color: 'var(--text-primary)' }}>
                {turn.result.answer}
              </p>

              {(turn.result.path_evidence?.length ?? 0) > 0 && (
                <div className="space-y-2">
                  <span className="text-label" style={{ color: 'var(--text-muted)' }}>图谱路径</span>
                  {turn.result.path_evidence!.map((item, i) => (
                    <div
                      key={i}
                      className="px-3 py-2 text-[11px] leading-relaxed"
                      style={{
                        border: '1px solid var(--border)',
                        backgroundColor: 'var(--bg-elevated)',
                        color: 'var(--text-secondary)',
                      }}
                    >
                      {item.text}
                    </div>
                  ))}
                </div>
              )}

              {turn.result.citations.length > 0 && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-label" style={{ color: 'var(--text-muted)' }}>引用来源</span>
                    {hasDebug && (
                      <button
                        type="button"
                        onClick={() => setShowDebug(v => !v)}
                        className="text-[10px] underline"
                        style={{ color: 'var(--text-muted)' }}
                      >
                        {showDebug ? '隐藏检索调试' : '展开检索调试'}
                      </button>
                    )}
                  </div>
                  {turn.result.citations.map((c, i) => (
                    <CitationChip
                      key={i}
                      citation={c}
                      index={i + 1}
                      showDebug={showDebug}
                      debug={c.debug ?? turn.result.debug?.[i]}
                    />
                  ))}
                </div>
              )}
            </article>
          )
        })}

        {turns.length === 0 && !loading && !error && (
          <p className="text-[12px] text-center mt-10 leading-relaxed" style={{ color: 'var(--text-muted)', opacity: 0.7 }}>
            构建图谱后可进行跨论文语义检索
          </p>
        )}

        {turns.length >= MAX_TURNS && (
          <p className="text-[10px] text-center" style={{ color: 'var(--text-muted)' }}>
            只保留最近 {MAX_TURNS} 轮问答
          </p>
        )}
      </div>
    </aside>
  )
}
