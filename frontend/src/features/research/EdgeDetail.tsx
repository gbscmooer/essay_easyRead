import type { SelectedEdge } from './GraphCanvas'

// Categorical/semantic colors for relation channels + citation intents (data viz —
// intentional fixed hues, not UI chrome). 与 graphkit/relatedness.py 的八个通道一一对应。
const FACET_COLORS: Record<string, string> = {
  topic: '#0ea5e9',
  purpose: '#3b82f6',
  method: '#8b5cf6',
  term: '#ec4899',
  dataset: '#10b981',
  baseline: '#f59e0b',
  bibcoupling: '#eab308',
  cocitation: '#a855f7',
}

const KIND_LABEL: Record<string, string> = {
  related: '关联',
  similar: '关联',       // v2 及以前持久化图谱的旧边名
  cites: '引用',
  bibcoupling: '文献耦合',
  cocitation: '共引',
  'shared-method': '共享方法（已弃用）',
  'uses-dataset': '使用数据集',
}

// 引用意图：后端启发式分类的稳定 code → 中文标签 + 配色
const INTENT_META: Record<string, { label: string; color: string; bg: string }> = {
  baseline:    { label: '对比基线', color: '#ef4444', bg: 'rgba(239,68,68,0.12)' },
  reuse:       { label: '方法复用', color: '#3b82f6', bg: 'rgba(59,130,246,0.12)' },
  inspiration: { label: '启发借鉴', color: '#a855f7', bg: 'rgba(168,85,247,0.12)' },
  background:  { label: '背景引用', color: '#6b7280', bg: 'rgba(107,114,128,0.14)' },
}

export function EdgeDetail({ edge, onClose }: { edge: SelectedEdge | null; onClose: () => void }) {
  if (!edge) return null
  const detail = edge.detail
  const totalContribution = detail
    ? detail.components.reduce((s, c) => s + Math.max(0, c.contribution), 0)
    : 0
  const isRelation = edge.kind === 'related' || edge.kind === 'similar'
  const contexts = edge.kind === 'cites' ? (edge.contexts ?? []) : []
  // 按意图汇总，用于上下文区块顶部的占比概览
  const intentCounts = contexts.reduce<Record<string, number>>((acc, c) => {
    const k = c.intent && INTENT_META[c.intent] ? c.intent : 'background'
    acc[k] = (acc[k] ?? 0) + 1
    return acc
  }, {})

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0" style={{ backgroundColor: 'rgba(0,0,0,0.4)', backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)' }} />
      <div
        className="relative w-96 h-full overflow-y-auto p-5 space-y-5 animate-slide-in-x"
        style={{ backgroundColor: 'var(--bg-surface)', borderLeft: '1px solid var(--border)' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-heading text-[15px]" style={{ color: 'var(--text-primary)' }}>
            {edge.kind === 'cites' ? '引用详情' : '关联度详情'}
          </h3>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭引用详情"
            className="focus-glow press text-[12px] px-3 py-1.5 rounded-full transition-colors hover:opacity-80"
            style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}
          >
            关闭
          </button>
        </div>

        {/* 两端论文 */}
        <div className="space-y-1.5">
          <p className="text-[13px] font-medium leading-snug" style={{ color: 'var(--text-primary)' }}>{edge.sourceLabel}</p>
          <div className="text-center text-[11px]" style={{ color: 'var(--text-muted)' }}>⟶ {KIND_LABEL[edge.kind] ?? edge.kind}</div>
          <p className="text-[13px] font-medium leading-snug" style={{ color: 'var(--text-primary)' }}>{edge.targetLabel}</p>
        </div>

        {/* 引用上下文 */}
        {contexts.length > 0 && (
          <div className="space-y-2.5">
            <div className="flex items-baseline justify-between">
              <span className="text-label" style={{ color: 'var(--text-muted)' }}>引用上下文</span>
              <span className="text-[12px] tabular-nums" style={{ color: 'var(--text-secondary)' }}>{contexts.length} 处引用</span>
            </div>
            {/* 意图占比概览 */}
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(intentCounts).map(([intent, n]) => {
                const meta = INTENT_META[intent] ?? INTENT_META.background
                return (
                  <span key={intent} className="text-[11px] font-medium px-2 py-0.5 rounded-full"
                    style={{ backgroundColor: meta.bg, color: meta.color }}>
                    {meta.label} · {n}
                  </span>
                )
              })}
            </div>
            <div className="space-y-2">
              {contexts.map((ctx, i) => {
                const meta = ctx.intent ? INTENT_META[ctx.intent] : undefined
                return (
                  <div key={i} className="rounded-xl p-3 space-y-1.5" style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border)' }}>
                    <div className="flex flex-wrap items-center gap-1.5">
                      {meta && (
                        <span className="inline-block text-[11px] font-medium px-2 py-0.5 rounded-full" style={{ backgroundColor: meta.bg, color: meta.color }}>
                          {meta.label}
                        </span>
                      )}
                      {ctx.section && (
                        <span className="inline-block text-[11px] font-medium px-2 py-0.5 rounded-full" style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--text-secondary)' }}>
                          {ctx.section}
                        </span>
                      )}
                    </div>
                    <p className="text-[12px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{ctx.context}</p>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* 综合关联度（仅关联边） */}
        {isRelation && (
          <>
            <div>
              <div className="flex items-baseline justify-between mb-1.5">
                <span className="text-label" style={{ color: 'var(--text-muted)' }}>综合关联度</span>
                <span className="text-[19px] font-semibold tabular-nums" style={{ color: 'var(--accent)' }}>
                  {(edge.weight * 100).toFixed(0)}%
                </span>
              </div>
              {detail && totalContribution > 0 && (
                <div className="flex h-2.5 rounded-full overflow-hidden" style={{ backgroundColor: 'var(--bg-elevated)' }}>
                  {detail.components.filter(c => c.contribution > 0).map(c => (
                    <div
                      key={c.facet}
                      title={`${c.label} 贡献 ${(c.contribution * 100).toFixed(1)}%`}
                      style={{
                        width: `${(c.contribution / totalContribution) * 100}%`,
                        backgroundColor: FACET_COLORS[c.facet] ?? '#6b7280',
                      }}
                    />
                  ))}
                </div>
              )}
            </div>

            {detail ? (
              <div className="space-y-3">
                <span className="text-label" style={{ color: 'var(--text-muted)' }}>通道分解</span>
                {detail.components.map(c => (
                  <div key={c.facet} className="space-y-1.5">
                    <div className="flex items-center justify-between text-[12px]">
                      <span className="flex items-center gap-1.5 font-medium" style={{ color: 'var(--text-primary)' }}>
                        <span className="w-2 h-2 rounded-full inline-block" style={{ backgroundColor: FACET_COLORS[c.facet] ?? '#6b7280' }} />
                        {c.label}
                      </span>
                      <span className="tabular-nums" style={{ color: 'var(--text-secondary)' }}>
                        {(c.sim * 100).toFixed(0)}% × {(c.weight * 100).toFixed(0)}% →{' '}
                        <span className="font-medium" style={{ color: 'var(--text-primary)' }}>{(c.contribution * 100).toFixed(1)}%</span>
                      </span>
                    </div>
                    <div className="h-1 rounded-full overflow-hidden" style={{ backgroundColor: 'var(--bg-elevated)' }}>
                      <div className="h-full rounded-full" style={{ width: `${c.sim * 100}%`, backgroundColor: FACET_COLORS[c.facet] ?? '#6b7280' }} />
                    </div>
                    <p className="text-[11px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>{c.reason}</p>
                    {c.shared.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {c.shared.map((s, i) => (
                          <span key={i} className="text-[11px] px-2 py-0.5 rounded-full"
                            style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }}>{s}</span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-[12px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                这条边来自旧版本图谱，没有通道分解。重新运行一次深度研究即可看到「主题 / 研究目的 / 方法 / 术语 / 数据集 / baseline / 文献耦合 / 共引」八个通道各自的贡献与理由。
              </p>
            )}
          </>
        )}

        {/* cites 边无上下文时的提示 */}
        {edge.kind === 'cites' && contexts.length === 0 && (
          <p className="text-[12px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
            该引用边无上下文信息。启用 GROBID 解析后，引用上下文将显示在此处。
          </p>
        )}

        {/* 其他边类型 */}
        {!isRelation && edge.kind !== 'cites' && (
          <p className="text-[12px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
            该边为结构关系图层（其分值已计入关联度边），无独立分解。
          </p>
        )}
      </div>
    </div>
  )
}
