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
    } catch (e: any) {
      setError(e.message || '查询失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="w-80 shrink-0 flex flex-col rounded-xl border overflow-hidden" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-surface)' }}>
      {/* 标题 */}
      <div className="px-4 py-2.5 border-b" style={{ borderColor: 'var(--border)' }}>
        <h3 className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>跨论文问答</h3>
      </div>

      {/* 输入区 */}
      <div className="p-3 border-b" style={{ borderColor: 'var(--border)' }}>
        <div className="flex gap-2">
          <input
            type="text"
            value={question}
            onChange={e => setQuestion(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleQuery()}
            placeholder="输入你的问题..."
            className="flex-1 px-3 py-1.5 text-xs rounded-lg border"
            style={{
              backgroundColor: 'var(--bg-base)',
              borderColor: 'var(--border)',
              color: 'var(--text-primary)',
            }}
          />
          <button
            onClick={handleQuery}
            disabled={loading || !question.trim()}
            className="px-3 py-1.5 text-xs font-medium rounded-lg transition-colors disabled:opacity-50"
            style={{ backgroundColor: 'var(--accent)', color: 'var(--bg-base)' }}
          >
            {loading ? '...' : '提问'}
          </button>
        </div>
      </div>

      {/* 结果区 */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {error && (
          <p className="text-xs text-red-400">{error}</p>
        )}

        {loading && (
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 border border-gray-600 border-t-blue-500 rounded-full animate-spin" />
            <span className="text-xs" style={{ color: 'var(--text-muted)' }}>检索中...</span>
          </div>
        )}

        {result && (
          <>
            <p className="text-xs leading-relaxed" style={{ color: 'var(--text-primary)' }}>
              {result.answer}
            </p>
            {result.citations.length > 0 && (
              <div className="space-y-1.5">
                <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>引用来源</span>
                {result.citations.map((c, i) => (
                  <CitationChip key={i} citation={c} index={i + 1} />
                ))}
              </div>
            )}
          </>
        )}

        {!result && !loading && !error && (
          <p className="text-xs text-center mt-8" style={{ color: 'var(--text-muted)', opacity: 0.5 }}>
            构建图谱后可进行跨论文语义检索
          </p>
        )}
      </div>
    </div>
  )
}
