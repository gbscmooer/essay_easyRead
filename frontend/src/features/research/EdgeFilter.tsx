import type { CSSProperties } from 'react'
import type { GraphEdgeKind } from '@/types'
import { Tooltip } from '@/ds-entry'
import { EDGE_INK } from '@/theme/graphPalette'

const EDGE_OPTIONS: { kind: GraphEdgeKind; label: string; color: string; hint: string }[] = [
  { kind: 'cites', label: '引用', color: EDGE_INK.cites, hint: '父论文 → 被引论文（联网扩展阶段建立）。' },
  { kind: 'related', label: '关联', color: EDGE_INK.related, hint: '论文关联度边：主题/目的/方法/术语/数据集/baseline/文献耦合/共引 八通道融合，再经多尺度骨架提取保留显著关系。点击边可看分解。' },
  { kind: 'bibcoupling', label: '文献耦合', color: EDGE_INK.bibcoupling, hint: '两篇论文引用了大量相同的外部文献。该信号已计入关联度，此图层仅供单独查看。' },
  { kind: 'cocitation', label: '共引', color: EDGE_INK.cocitation, hint: '被同一篇论文共同引用。该信号已计入关联度，此图层仅供单独查看。' },
  { kind: 'shared-method', label: '共享方法', color: EDGE_INK['shared-method'], hint: '已弃用占位：当前图谱不产出此边类型。' },
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
      <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
        边类型{selected.size > 0 ? ` · ${selected.size}` : ''}
      </span>
      {/* 每个开关带自己的强调色（--btn-accent），必须和图谱里对应边的颜色
          一致 —— 这是图例，不只是按钮。选中/未选的区别由控件系统统一给出，
          不再靠内联 backgroundColor + opacity 手写两套。 */}
      {EDGE_OPTIONS.filter(opt => opt.kind !== 'shared-method').map(opt => {
        const on = selected.has(opt.kind)
        return (
          <Tooltip key={opt.kind} content={opt.hint}>
            <button
              type="button"
              aria-pressed={on}
              onClick={() => toggle(opt.kind)}
              className="er-btn er-btn--secondary er-btn--sm"
              style={{ '--btn-accent': opt.color } as CSSProperties}
            >
              {opt.label}
            </button>
          </Tooltip>
        )
      })}
    </div>
  )
}
