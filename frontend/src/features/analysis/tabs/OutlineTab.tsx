import type { AnalysisResult } from '@/types'


interface OutlineTabProps {
  result: AnalysisResult
  onJumpToPage?: (page: number, section?: string) => void
}

export function OutlineTab({ result, onJumpToPage }: OutlineTabProps) {
  if (!result.outline || result.outline.length === 0) {
    return (
      <div className="text-sm text-center py-8" style={{ color: 'var(--text-muted)' }}>
        暂无大纲信息
      </div>
    )
  }

  const parsePage = (page: string): number | null => {
    if (!page) return null
    const match = String(page).match(/\d+/)
    return match ? parseInt(match[0], 10) : null
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>论文大纲</h2>
      <p className="text-xs" style={{ color: 'var(--text-muted)' }}>点击章节跳转到论文对应位置</p>

      <div className="relative">
        <div className="absolute left-3 top-2 bottom-2 w-px" style={{ backgroundColor: 'var(--border-light)' }} />

        <div className="space-y-2">
          {result.outline.map((item, idx) => {
            const pageNum = parsePage(item.page)
            const clickable = !!pageNum && !!onJumpToPage

            return (
              <div key={idx} className="relative pl-10">
                <div className="absolute left-1.5 top-2.5 w-3 h-3 rounded-full z-10" style={{ backgroundColor: 'var(--accent)', border: '2px solid var(--bg-base)' }} />

                <button
                  onClick={() => pageNum && onJumpToPage?.(pageNum, item.section)}
                  disabled={!clickable}
                  className="w-full text-left rounded-lg p-3 border transition-colors"
                  style={clickable ? { backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border-light)' } : { backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border-light)', cursor: 'default' }}
                  onMouseEnter={e => { if (clickable) { (e.currentTarget as HTMLElement).style.borderColor = 'var(--accent)' } }}
                  onMouseLeave={e => { if (clickable) { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border-light)' } }}
                >
                  <div className="flex items-center justify-between mb-1">
                    <h3 className="text-sm font-medium"
                    style={{ color: clickable ? 'var(--accent)' : 'var(--text-primary)' }}>
                      {item.section}
                    </h3>
                    {item.page && (
                      <span className="text-xs px-2 py-0.5 rounded"
                      style={clickable ? { color: 'var(--accent)', backgroundColor: 'var(--accent-subtle)' } : { color: 'var(--text-muted)', backgroundColor: 'var(--bg-elevated)' }}>
                        p.{item.page}
                      </span>
                    )}
                  </div>
                  <p className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                    {item.summary}
                  </p>
                </button>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
