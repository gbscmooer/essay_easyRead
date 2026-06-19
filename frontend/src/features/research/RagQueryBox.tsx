import { useState } from 'react'
import type { RagQueryResponse } from '@/types'
import { queryRag } from '@/api/research'
import { CitationChip } from './CitationChip'

interface RagQueryBoxProps {
  wsId: number
}

export function RagQueryBox({ wsId }: RagQueryBoxProps) {
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<RagQueryResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleQuery = async () => {
    if (!question.trim()) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const res = await queryRag(wsId, question.trim())
      setResult(res)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '查询失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="w-full md:w-80 shrink-0 flex flex-col rounded-2xl overflow-hidden" style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)' }}>
      {/* 标题 */}
      <div className="px-4 py-3" style={{ borderBottom: '1px solid var(--border)' }}>
        <h3 className="text-heading text-[13px]" style={{ color: 'var(--text-primary)' }}>跨论文问答</h3>
      </div>

      {/* 输入区 */}
      <div className="p-3" style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="flex gap-2">
          <input
            type="text"
            value={question}
            onChange={e => setQuestion(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleQuery()}
            placeholder="输入你的问题…"
            className="flex-1 px-3 py-2 text-[12px] rounded-lg focus:outline-none"
            style={{
              backgroundColor: 'var(--bg-elevated)',
              border: '1px solid var(--border)',
              color: 'var(--text-primary)',
            }}
          />
          <button
            onClick={handleQuery}
            disabled={loading || !question.trim()}
            className="px-3.5 py-2 text-[12px] font-medium rounded-full transition-opacity disabled:opacity-50 hover:opacity-90"
            style={{ backgroundColor: 'var(--accent)', color: '#ffffff' }}
          >
            {loading ? '…' : '提问'}
          </button>
        </div>
      </div>

      {/* 结果区 */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {error && (
          <p className="text-[12px]" style={{ color: 'var(--error)' }}>{error}</p>
        )}

        {loading && (
          <div className="flex items-center gap-2">
            <div className="w-3.5 h-3.5 border rounded-full animate-spin" style={{ borderColor: 'var(--border)', borderTopColor: 'var(--accent)' }} />
            <span className="text-[12px]" style={{ color: 'var(--text-muted)' }}>检索中…</span>
          </div>
        )}

        {result && (
          <>
            <p className="text-[13px] leading-relaxed" style={{ color: 'var(--text-primary)' }}>
              {result.answer}
            </p>
            {result.citations.length > 0 && (
              <div className="space-y-2">
                <span className="text-label" style={{ color: 'var(--text-muted)' }}>引用来源</span>
                {result.citations.map((c, i) => (
                  <CitationChip key={i} citation={c} index={i + 1} />
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
