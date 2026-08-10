import type { AnalysisResult } from '@/types'

const SPRING = 'var(--ease-press)'

interface OutlineTabProps {
  result: AnalysisResult
  onJumpToPage?: (page: number, section?: string) => void
}

export function OutlineTab({ result, onJumpToPage }: OutlineTabProps) {
  if (!result.outline || result.outline.length === 0) {
    return (
      <div className="text-[13px] text-center py-10" style={{ color: 'var(--text-muted)' }}>
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
    <div className="space-y-5">
      <header>
        <h2 className="text-heading text-[15px] mb-1" style={{ color: 'var(--text-primary)' }}>论文大纲</h2>
        <p className="text-[12px]" style={{ color: 'var(--text-muted)' }}>点击章节跳转到论文对应位置</p>
      </header>

      <div className="relative">
        <div className="absolute left-[7px] top-3 bottom-3 w-px" style={{ backgroundColor: 'var(--border)' }} />

        <div className="space-y-2.5">
          {result.outline.map((item, idx) => {
            const pageNum = parsePage(item.page)
            const clickable = !!pageNum && !!onJumpToPage

            return (
              <div key={idx} className="group relative pl-9">
                <div
                  className={`absolute left-[3px] top-[18px] w-2 h-2 z-10 transition-all duration-200 ${clickable ? 'group-hover:scale-[1.6]' : ''}`}
                  style={{
                    backgroundColor: clickable ? 'var(--accent)' : 'var(--bg-surface)',
                    border: clickable ? '1.5px solid var(--accent)' : '1.5px solid var(--border-light)',
                    boxShadow: clickable ? '0 0 0 0 var(--accent-subtle)' : 'none',
                    transitionTimingFunction: SPRING,
                  }}
                />

                <button
                  onClick={() => pageNum && onJumpToPage?.(pageNum, item.section)}
                  disabled={!clickable}
                  className={`w-full text-left list-row transition-all duration-200 ${clickable ? 'card-hover focus-glow press' : ''}`}
                  style={{ cursor: clickable ? 'pointer' : 'default', opacity: clickable ? 1 : 0.62, transitionTimingFunction: SPRING }}
                >
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <h3 className="text-[13px] font-medium" style={{ color: 'var(--text-primary)' }}>
                      {item.section}
                    </h3>
                    {item.page && (
                      <span
                        className="text-[11px] px-2 py-0.5 shrink-0 tabular-nums"
                        style={clickable
                          ? { color: 'var(--accent)', backgroundColor: 'var(--accent-subtle)' }
                          : { color: 'var(--text-muted)', backgroundColor: 'var(--bg-elevated)' }}
                      >
                        p.{item.page}
                      </span>
                    )}
                  </div>
                  <p className="text-[12px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
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
