import type { GraphEdgeKind } from '@/types'
import { Tooltip } from '@/ds-entry'

const EDGE_OPTIONS: { kind: GraphEdgeKind; label: string; color: string; hint: string }[] = [
  { kind: 'cites', label: '引用', color: '#ef4444', hint: '基于"父论文 → 引用论文"的关系（联网扩展阶段建立）。点亮则在图中显示。' },
  { kind: 'similar', label: '相似', color: '#3b82f6', hint: '基于两篇论文向量余弦相似度高于阈值。可在"高级设置"中调阈值。' },
  { kind: 'shared-method', label: '共享方法', color: '#10b981', hint: '两篇论文使用了相同的方法/技术（需启用 LLM 提取，当前为占位）。' },
]

interface EdgeFilterProps {
  selected: Set<GraphEdgeKind>
  onChange: (selected: Set<GraphEdgeKind>) => void
}

export function EdgeFilter({ selected, onChange }: EdgeFilterProps) {
  const toggle = (kind: GraphEdgeKind) => {
    const next = new Set(selected)
    if (next.has(kind)) {
      next.delete(kind)
    } else {
      next.add(kind)
    }
    onChange(next)
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
        边类型{selected.size > 0 ? ` · ${selected.size}` : ''}
      </span>
      {EDGE_OPTIONS.map(opt => {
        const on = selected.has(opt.kind)
        return (
          <Tooltip key={opt.kind} content={opt.hint}>
            <button
              type="button"
              aria-pressed={on}
              onClick={() => toggle(opt.kind)}
              className={`focus-glow press flex items-center gap-1 px-2 py-0.5 text-[10px] rounded-full border transition-all duration-200 ${
                on ? 'font-medium' : 'opacity-60 hover:opacity-100'
              }`}
              style={
                on
                  ? { borderColor: opt.color, color: opt.color, backgroundColor: `${opt.color}15` }
                  : { borderColor: 'var(--border)', color: 'var(--text-muted)' }
              }
            >
              <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: on ? opt.color : 'var(--text-muted)' }} />
              {opt.label}
            </button>
          </Tooltip>
        )
      })}
    </div>
  )
}
