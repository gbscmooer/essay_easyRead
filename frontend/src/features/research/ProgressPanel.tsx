import type { ResearchStage } from '@/types'

const STAGE_LABELS: Record<ResearchStage, string> = {
  collect: '收集论文',
  expand: '联网扩展',
  chunk: '文本切片',
  embed: '向量嵌入',
  graph: '构建图谱',
  persist: '持久化',
}

const STAGE_ORDER: ResearchStage[] = ['collect', 'expand', 'chunk', 'embed', 'graph', 'persist']

interface ProgressPanelProps {
  stage: ResearchStage | null
  progressMessage: string
  embed: { done: number; total: number }
  papersAdded: { paper_id: number; title: string; total_expanded: number }[]
  error: string | null
}

export function ProgressPanel({ stage, progressMessage, embed, papersAdded, error }: ProgressPanelProps) {
  const currentIdx = stage ? STAGE_ORDER.indexOf(stage) : -1

  return (
    <div className="px-6 py-3.5" style={{ borderBottom: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)' }}>
      {/* 阶段指示器 */}
      <div className="flex items-center gap-1.5 mb-2.5">
        {STAGE_ORDER.map((s, i) => (
          <div key={s} className="flex items-center gap-1.5">
            <div
              className={`w-2 h-2 rounded-full transition-colors ${i === currentIdx ? 'animate-pulse' : ''}`}
              style={
                i < currentIdx
                  ? { backgroundColor: 'var(--accent)' }
                  : i === currentIdx
                  ? { backgroundColor: 'var(--accent)', opacity: 0.7 }
                  : { backgroundColor: 'var(--border-light)' }
              }
            />
            <span
              className="text-[11px]"
              style={
                i <= currentIdx
                  ? { color: 'var(--text-secondary)' }
                  : { color: 'var(--text-muted)', opacity: 0.5 }
              }
            >
              {STAGE_LABELS[s]}
            </span>
            {i < STAGE_ORDER.length - 1 && (
              <div className="w-3 h-px" style={{ backgroundColor: 'var(--border)' }} />
            )}
          </div>
        ))}
      </div>

      {/* 进度信息 */}
      <div className="flex items-center gap-4">
        {stage === 'embed' && embed.total > 0 && (
          <div className="flex items-center gap-2 flex-1">
            <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ backgroundColor: 'var(--bg-elevated)' }}>
              <div
                className="h-full rounded-full transition-all duration-300"
                style={{
                  width: `${(embed.done / embed.total) * 100}%`,
                  backgroundColor: 'var(--accent)',
                }}
              />
            </div>
            <span className="text-[12px] font-mono tabular-nums" style={{ color: 'var(--text-muted)' }}>
              {embed.done}/{embed.total}
            </span>
          </div>
        )}

        {stage === 'expand' && papersAdded.length > 0 && (
          <span className="text-[12px]" style={{ color: 'var(--text-muted)' }}>
            已扩展 {papersAdded.length} 篇论文
          </span>
        )}

        {progressMessage && (
          <span className="text-[12px]" style={{ color: 'var(--text-muted)' }}>
            {progressMessage}
          </span>
        )}

        {error && (
          <span className="text-[12px]" style={{ color: 'var(--error)' }}>{error}</span>
        )}
      </div>
    </div>
  )
}
