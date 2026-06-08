import type { SelectedEdge } from './GraphCanvas'

const FACET_COLORS: Record<string, string> = {
  purpose: '#3b82f6',
  method: '#8b5cf6',
  baseline: '#f59e0b',
  dataset: '#10b981',
}

const KIND_LABEL: Record<string, string> = {
  similar: '相似',
  cites: '引用',
  'shared-method': '共享方法',
  'uses-dataset': '使用数据集',
}

export function EdgeDetail({ edge, onClose }: { edge: SelectedEdge | null; onClose: () => void }) {
  if (!edge) return null
  const detail = edge.detail
  const totalContribution = detail
    ? detail.components.reduce((s, c) => s + Math.max(0, c.contribution), 0)
    : 0

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div
        className="relative w-96 h-full overflow-y-auto p-5 space-y-4"
        style={{ backgroundColor: 'var(--bg-surface)', borderLeft: '1px solid var(--border)' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>相似度详情</h3>
          <button onClick={onClose} className="text-xs px-2 py-1 rounded" style={{ color: 'var(--text-muted)' }}>关闭</button>
        </div>

        {/* 两端论文 */}
        <div className="space-y-1.5">
          <p className="text-sm font-medium leading-snug" style={{ color: 'var(--text-primary)' }}>{edge.sourceLabel}</p>
          <div className="text-center text-[10px]" style={{ color: 'var(--text-muted)' }}>⟷ {KIND_LABEL[edge.kind] ?? edge.kind}</div>
          <p className="text-sm font-medium leading-snug" style={{ color: 'var(--text-primary)' }}>{edge.targetLabel}</p>
        </div>

        {/* 综合相似度 */}
        <div>
          <div className="flex items-baseline justify-between mb-1">
            <span className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>综合相似度</span>
            <span className="text-lg font-semibold tabular-nums" style={{ color: 'var(--accent)' }}>
              {(edge.weight * 100).toFixed(0)}%
            </span>
          </div>
          {/* 贡献堆叠条 */}
          {detail && totalContribution > 0 && (
            <div className="flex h-2.5 rounded-full overflow-hidden" style={{ backgroundColor: 'var(--border)' }}>
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
          /* 各维度分解 */
          <div className="space-y-3">
            <span className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>维度分解</span>
            {detail.components.map(c => (
              <div key={c.facet} className="space-y-1">
                <div className="flex items-center justify-between text-xs">
                  <span className="flex items-center gap-1.5 font-medium" style={{ color: 'var(--text-primary)' }}>
                    <span className="w-2 h-2 rounded-full inline-block" style={{ backgroundColor: FACET_COLORS[c.facet] ?? '#6b7280' }} />
                    {c.label}
                  </span>
                  <span className="tabular-nums" style={{ color: 'var(--text-secondary)' }}>
                    {(c.sim * 100).toFixed(0)}% × {(c.weight * 100).toFixed(0)}% →{' '}
                    <span className="font-medium" style={{ color: 'var(--text-primary)' }}>{(c.contribution * 100).toFixed(1)}%</span>
                  </span>
                </div>
                {/* 单维度相似度条 */}
                <div className="h-1 rounded-full overflow-hidden" style={{ backgroundColor: 'var(--border)' }}>
                  <div className="h-full rounded-full" style={{ width: `${c.sim * 100}%`, backgroundColor: FACET_COLORS[c.facet] ?? '#6b7280' }} />
                </div>
                <p className="text-[11px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>{c.reason}</p>
                {c.shared.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {c.shared.map((s, i) => (
                      <span key={i} className="text-[10px] px-1.5 py-0.5 rounded"
                        style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }}>{s}</span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>
            {edge.kind === 'similar'
              ? '该相似边由当前 Provider 计算，未提供维度分解。切换到 facet_weighted 相似度后，点击相似边即可看到「目的 / 方法 / baseline / 数据集」各维度的占比与理由。'
              : '该边为结构关系，无相似度分解。'}
          </p>
        )}
      </div>
    </div>
  )
}
