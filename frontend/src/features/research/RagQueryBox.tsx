import { useState } from 'react'
import type { RagQueryResponse } from '@/types'
import { queryRag } from '@/api/research'
import { CitationChip } from './CitationChip'
import { LoadingSpinner } from '@/ds-entry'

type RagMode = 'vector' | 'hybrid' | 'graph'

interface RagQueryBoxProps {
  wsId: number
  paperIds?: number[]
  topN?: number
}

export function RagQueryBox({ wsId, paperIds, topN = 5 }: RagQueryBoxProps) {
  const [question, setQuestion] = useState('')
  const [mode, setMode] = useState<RagMode>('vector')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<RagQueryResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showDebug, setShowDebug] = useState(false)

  const handleQuery = async () => {
    if (!question.trim()) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const res = await queryRag(wsId, question.trim(), topN, paperIds, mode)
      setResult(res)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '查询失败')
    } finally {
      setLoading(false)
    }
  }

  const hasDebug = result?.citations.some(c => c.debug) || (result?.debug?.length ?? 0) > 0

  return (
    <div className="w-full md:w-80 shrink-0 flex flex-col rounded-2xl overflow-hidden" style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)' }}>
      <div className="px-4 py-3 flex items-center justify-between gap-2" style={{ borderBottom: '1px solid var(--border)' }}>
        <h3 className="text-heading text-[13px]" style={{ color: 'var(--text-primary)' }}>跨论文问答</h3>
        <div className="flex rounded-lg overflow-hidden border text-[10px]" style={{ borderColor: 'var(--border)' }}>
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
            className="flex-1 px-3 py-2 text-[12px] rounded-lg focus:outline-none transition-colors duration-200"
            style={{
              backgroundColor: 'var(--bg-elevated)',
              border: '1px solid var(--border)',
              color: 'var(--text-primary)',
            }}
            onFocus={e => { e.currentTarget.style.borderColor = 'var(--accent)' }}
            onBlur={e => { e.currentTarget.style.borderColor = 'var(--border)' }}
          />
          <button
            type="button"
            onClick={handleQuery}
            disabled={loading || !question.trim()}
            className="focus-glow press px-3.5 py-2 text-[12px] font-medium rounded-full transition-opacity disabled:opacity-50 disabled:cursor-not-allowed hover:opacity-90"
            style={{ backgroundColor: 'var(--accent)', color: '#ffffff' }}
          >
            {loading ? '…' : '提问'}
          </button>
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

        {result && (
          <>
            <p className="text-[13px] leading-relaxed" style={{ color: 'var(--text-primary)' }}>
              {result.answer}
            </p>
            {(result.path_evidence?.length ?? 0) > 0 && (
              <div className="space-y-2">
                <span className="text-label" style={{ color: 'var(--text-muted)' }}>图谱路径</span>
                {result.path_evidence!.map((item, i) => (
                  <div
                    key={i}
                    className="rounded-lg px-3 py-2 text-[11px] leading-relaxed"
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
            {result.citations.length > 0 && (
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
                {result.citations.map((c, i) => (
                  <CitationChip
                    key={i}
                    citation={c}
                    index={i + 1}
                    showDebug={showDebug}
                    debug={c.debug ?? result.debug?.[i]}
                  />
                ))}
              </div>
            )}
          </>
        )}

        {!result && !loading && !error && (
          <p className="text-[12px] text-center mt-10 leading-relaxed" style={{ color: 'var(--text-muted)', opacity: 0.7 }}>
            构建图谱后可进行跨论文语义检索
          </p>
        )}
      </div>
    </div>
  )
}
