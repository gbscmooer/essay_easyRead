import type { GraphEdgeKind } from '@/types'

const EDGE_OPTIONS: { kind: GraphEdgeKind; label: string; color: string }[] = [
  { kind: 'cites', label: '引用', color: '#ef4444' },
  { kind: 'similar', label: '相似', color: '#3b82f6' },
  { kind: 'shared-method', label: '共享方法', color: '#10b981' },
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
      <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>边类型</span>
      {EDGE_OPTIONS.map(opt => (
        <button
          key={opt.kind}
          onClick={() => toggle(opt.kind)}
          className={`flex items-center gap-1 px-2 py-0.5 text-[10px] rounded-full border transition-colors ${
            selected.has(opt.kind) ? 'font-medium' : 'opacity-50'
          }`}
          style={
            selected.has(opt.kind)
              ? { borderColor: opt.color, color: opt.color, backgroundColor: `${opt.color}15` }
              : { borderColor: 'var(--border)', color: 'var(--text-muted)' }
          }
        >
          <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: opt.color }} />
          {opt.label}
        </button>
      ))}
    </div>
  )
}
