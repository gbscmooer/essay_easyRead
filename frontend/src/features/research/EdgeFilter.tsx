import type { GraphEdgeKind } from '@/types'
import { Tooltip } from '@/ds-entry'

const EDGE_OPTIONS: { kind: GraphEdgeKind; label: string; color: string; hint: string }[] = [
  { kind: 'cites', label: '引用', color: '#ef4444', hint: '父论文 → 被引论文（联网扩展阶段建立）。' },
  { kind: 'related', label: '关联', color: '#3b82f6', hint: '论文关联度边：主题/目的/方法/术语/数据集/baseline/文献耦合/共引 八通道融合，再经多尺度骨架提取保留显著关系。点击边可看分解。' },
  { kind: 'bibcoupling', label: '文献耦合', color: '#f59e0b', hint: '两篇论文引用了大量相同的外部文献。该信号已计入关联度，此图层仅供单独查看。' },
  { kind: 'cocitation', label: '共引', color: '#a855f7', hint: '被同一篇论文共同引用。该信号已计入关联度，此图层仅供单独查看。' },
  { kind: 'shared-method', label: '共享方法', color: '#10b981', hint: '已弃用占位：当前图谱不产出此边类型。' },
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
    <div className="flex items-center gap-2 flex-wrap">
      <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
        边类型{selected.size > 0 ? ` · ${selected.size}` : ''}
      </span>
      {EDGE_OPTIONS.filter(opt => opt.kind !== 'shared-method').map(opt => {
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
