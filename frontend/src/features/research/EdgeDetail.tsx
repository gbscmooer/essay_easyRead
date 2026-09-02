import type { CSSProperties } from 'react'
import type { SelectedEdge } from './GraphCanvas'
import { CHANNEL_INK, INTENT_INK, RELATION_INK, inkWash } from '@/theme/graphPalette'

// 八个通道与 graphkit/relatedness.py 一一对应；配色取自 theme/graphPalette 的
// 油墨分类色，不再在这里另写一套十六进制。
const FACET_COLORS: Record<string, string> = CHANNEL_INK

const KIND_LABEL: Record<string, string> = {
  related: '关联',
  similar: '关联',       // v2 及以前持久化图谱的旧边名
  cites: '引用',
  bibcoupling: '文献耦合',
  cocitation: '共引',
  'shared-method': '共享方法（已弃用）',
  'uses-dataset': '使用数据集',
}

// 引用因果（stage_synthesize 连边研判）：code → 中文标签 + 配色
const RELATION_META: Record<string, { label: string; color: string; bg: string }> = {
  improves:    { label: '➡️ 改进继承', color: RELATION_INK.improves, bg: inkWash(RELATION_INK.improves) },
  replaces:    { label: '⚔️ 范式替代', color: RELATION_INK.replaces, bg: inkWash(RELATION_INK.replaces) },
  evaluates_on:{ label: '📏 基准评测', color: RELATION_INK.evaluates_on, bg: inkWash(RELATION_INK.evaluates_on) },
  inspires:    { label: '🔗 跨界借鉴', color: RELATION_INK.inspires, bg: inkWash(RELATION_INK.inspires) },
  cites:       { label: '普通引用', color: 'var(--text-muted)', bg: 'var(--bg-elevated)' },
}

// 引用意图：后端启发式分类的稳定 code → 中文标签 + 配色
const INTENT_META: Record<string, { label: string; color: string; bg: string }> = {
  baseline:    { label: '对比基线', color: INTENT_INK.baseline, bg: inkWash(INTENT_INK.baseline) },
  reuse:       { label: '方法复用', color: INTENT_INK.reuse, bg: inkWash(INTENT_INK.reuse) },
  inspiration: { label: '启发借鉴', color: INTENT_INK.inspiration, bg: inkWash(INTENT_INK.inspiration) },
  background:  { label: '背景引用', color: 'var(--text-muted)', bg: 'var(--bg-elevated)' },
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
            className="er-btn er-btn--ghost er-btn--sm"
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

        {/* 引用因果研判（仅 cites 边；related 无向边不猜因果） */}
        {edge.kind === 'cites' && edge.semantic && edge.semantic.relation_type !== 'cites' && (
          <div className="p-3 space-y-1.5" style={{ backgroundColor: RELATION_META[edge.semantic.relation_type]?.bg ?? 'var(--bg-elevated)' }}>
            <span className="er-tag" style={{ '--tag-accent': RELATION_META[edge.semantic.relation_type]?.color } as CSSProperties}>
              {RELATION_META[edge.semantic.relation_type]?.label ?? edge.semantic.relation_type}
            </span>
            {edge.semantic.relation_summary && (
              <p className="text-[13px] leading-relaxed" style={{ color: 'var(--text-primary)' }}>
                {edge.semantic.relation_summary}
              </p>
            )}
            <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>AI 因果研判 · 点击图中连线可复核引用原文</p>
          </div>
        )}

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
                  <span key={intent} className="er-tag" style={{ '--tag-accent': meta.color } as CSSProperties}>
                    {meta.label} · {n}
                  </span>
                )
              })}
            </div>
            <div className="space-y-2">
              {contexts.map((ctx, i) => {
                const meta = ctx.intent ? INTENT_META[ctx.intent] : undefined
                return (
                  <div key={i} className="p-3 space-y-1.5" style={{ backgroundColor: 'var(--bg-elevated)', borderLeft: 'var(--rule-mark) solid var(--border-light)' }}>
                    <div className="flex flex-wrap items-center gap-1.5">
                      {meta && (
                        <span className="er-tag" style={{ '--tag-accent': meta.color } as CSSProperties}>
                          {meta.label}
                        </span>
                      )}
                      {ctx.section && (
                        <span className="er-tag er-tag--mute">{ctx.section}</span>
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
                <div className="flex h-2.5 overflow-hidden" style={{ backgroundColor: 'var(--bg-elevated)' }}>
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
                        <span className="w-2 h-2 inline-block" style={{ backgroundColor: FACET_COLORS[c.facet] ?? '#6b7280' }} />
                        {c.label}
                      </span>
                      <span className="tabular-nums" style={{ color: 'var(--text-secondary)' }}>
                        {(c.sim * 100).toFixed(0)}% × {(c.weight * 100).toFixed(0)}% →{' '}
                        <span className="font-medium" style={{ color: 'var(--text-primary)' }}>{(c.contribution * 100).toFixed(1)}%</span>
                      </span>
                    </div>
                    <div className="h-1 overflow-hidden" style={{ backgroundColor: 'var(--bg-elevated)' }}>
                      <div className="h-full" style={{ width: `${c.sim * 100}%`, backgroundColor: FACET_COLORS[c.facet] ?? '#6b7280' }} />
                    </div>
                    <p className="text-[11px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>{c.reason}</p>
                    {c.shared.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {c.shared.map((s, i) => (
                          <span key={i} className="er-tag">{s}</span>
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
