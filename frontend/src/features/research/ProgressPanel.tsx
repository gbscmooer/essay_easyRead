import type { ResearchMode, ResearchStage, RecallChannelStats } from '@/types'

const STAGE_LABELS = {
  collect: '收集论文',
  expand: '联网扩展',
  expand_arxiv: 'arXiv 扩展',
  chunk: '文本切片',
  embed: '向量嵌入',
  digest: '图谱速读',
  graph: '构建图谱',
  synthesize: '领域认知综合',
  persist: '持久化',
} satisfies Record<ResearchStage, string>

/** 与 pipeline 实际执行路径对齐：local 跳过 expand*；autoDigest=false 跳过 digest */
export function getStageOrder(mode?: ResearchMode | null, autoDigest = true): ResearchStage[] {
  const stages: ResearchStage[] = ['collect']
  if (mode === 'online') {
    stages.push('expand')
  }
  stages.push('chunk', 'embed')
  if (autoDigest) {
    stages.push('digest')
  }
  stages.push('graph', 'synthesize', 'persist')
  return stages
}

function isKnownStage(stage: string): stage is ResearchStage {
  return stage in STAGE_LABELS
}

const RECALL_CHANNEL_LABELS: Record<string, string> = {
  backward: '反向引用',
  forward: '正向引用',
  recommend: '相关推荐',
  pdf_ref: 'PDF 引用',
  graph_ppr: '图谱 PPR',
}

interface ProgressPanelProps {
  stage: ResearchStage | string | null
  progressMessage: string
  embed: { done: number; total: number }
  papersAdded: { paper_id: number; title: string; total_expanded: number }[]
  recallStats?: RecallChannelStats[]
  error: string | null
  mode?: ResearchMode | null
  autoDigest?: boolean
}

export function ProgressPanel({
  stage,
  progressMessage,
  embed,
  papersAdded,
  recallStats = [],
  error,
  mode = null,
  autoDigest = true,
}: ProgressPanelProps) {
  const stageOrder = getStageOrder(mode, autoDigest)
  const knownStage = stage && isKnownStage(stage) ? stage : null
  // 若当前 stage 不在本轮路径中（例如配置变更），回退到全量顺序以免空白
  const order = knownStage && !stageOrder.includes(knownStage)
    ? getStageOrder('online', true)
    : stageOrder
  const currentIdx = knownStage ? order.indexOf(knownStage) : -1

  return (
    <div className="px-6 py-3.5" style={{ borderBottom: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)' }}>
      {/* 阶段指示器 */}
      <div className="flex items-center gap-1.5 mb-2.5">
        {order.map((s, i) => (
          <div key={s} className="flex items-center gap-1.5">
            <div
              className={`w-2 h-2 transition-colors ${i === currentIdx ? 'animate-pulse' : ''}`}
              style={
                i < currentIdx
                  ? { backgroundColor: 'var(--accent)' }
                  : i === currentIdx
                  ? { backgroundColor: 'var(--accent)', opacity: 0.7 }
                  : { backgroundColor: 'var(--border-light)' }
              }
            />
            <span
              className={`text-[11px] transition-colors ${i === currentIdx ? 'font-semibold' : ''}`}
              style={
                i === currentIdx
                  ? { color: 'var(--accent)' }
                  : i < currentIdx
                  ? { color: 'var(--text-secondary)' }
                  : { color: 'var(--text-muted)', opacity: 0.5 }
              }
            >
              {STAGE_LABELS[s]}
            </span>
            {i < order.length - 1 && (
              <div className="w-3 h-px" style={{ backgroundColor: 'var(--border)' }} />
            )}
          </div>
        ))}
      </div>

      {/* 进度信息 */}
      <div className="flex items-center gap-4">
        {stage === 'embed' && embed.total > 0 && (
          <div className="flex items-center gap-2 flex-1">
            <div className="flex-1 h-1.5 overflow-hidden" style={{ backgroundColor: 'var(--bg-elevated)' }}>
              <div
                className="h-full transition-all duration-300"
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

        {(stage === 'expand' || stage === 'expand_arxiv') && papersAdded.length > 0 && (
          <span className="text-[12px]" style={{ color: 'var(--text-muted)' }}>
            已扩展 {papersAdded.length} 篇论文
          </span>
        )}

        {(stage === 'expand' || stage === 'expand_arxiv') && recallStats.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            {recallStats.map(stat => {
              const label = RECALL_CHANNEL_LABELS[stat.channel] ?? stat.channel
              const ok = (stat.failed ?? 0) === 0
              return (
                <span
                  key={stat.channel}
                  className="text-[11px] px-2 py-0.5 font-mono tabular-nums"
                  style={{
                    border: `1px solid ${ok ? 'var(--border)' : 'var(--error)'}`,
                    color: ok ? 'var(--text-muted)' : 'var(--error)',
                    backgroundColor: 'var(--bg-elevated)',
                  }}
                  title={[
                    stat.latency_ms ? `耗时 ${Math.round(stat.latency_ms)} ms` : '',
                    stat.errors?.length ? stat.errors.join('；') : (stat.error ?? ''),
                  ].filter(Boolean).join(' · ') || undefined}
                >
                  {label} {stat.hit_count ?? 0}
                  {(stat.failed ?? 0) > 0 ? ` · ${stat.failed} 失败` : ''}
                </span>
              )
            })}
          </div>
        )}

        {stage && !knownStage && (
          <span className="text-[12px]" style={{ color: 'var(--text-muted)' }}>
            当前阶段：{stage}
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
