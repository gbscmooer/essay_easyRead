import { useEffect, useRef } from 'react'

const APP_VERSION = '1.2.2'
const GITHUB_URL = 'https://github.com/gbscmooer/essay_easyRead'

interface ReleaseNote {
  version: string
  date: string
  changes: string[]
}

const RELEASE_NOTES: ReleaseNote[] = [
  {
    version: '1.2.2',
    date: '2026-06-06',
    changes: [
      '新增深度研究模块（知识图谱 + RAG + Semantic Scholar 扩展）',
      '新增 Embedding 模型配置，支持本地 BGE-M3 / 硅基流动 / OpenAI',
      '设置页新增模型连通性测试按钮',
      '首页新增"最近使用工作区"板块',
      '全局主题适配优化，修复深色模式下白色背景问题',
      '摩卡主题文字亮度调整',
    ],
  },
  {
    version: '1.2.1',
    date: '2026-06-04',
    changes: [
      '新增工作区管理功能',
      '新增论文库列表页面',
      '源码分析面板支持代码仓库克隆与浏览',
      '底部终端面板（后端日志 / 前端日志 / 控制台）',
    ],
  },
  {
    version: '1.2.0',
    date: '2026-06-01',
    changes: [
      '新增 6 套主题（朋克 / 纯黑 / 学术 / 纯白 / 陶瓷 / 摩卡）',
      '论文 AI 流式解析（大纲、方法、实验、问答等）',
      'PDF 阅读器支持大纲跳转高亮',
      '画词翻译 & AI 总结功能',
    ],
  },
]


export function VersionPopup({ onClose }: { onClose: () => void }) {
  const popupRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (popupRef.current && !popupRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('mousedown', handleClick)
    document.addEventListener('keydown', handleEsc)
    return () => {
      document.removeEventListener('mousedown', handleClick)
      document.removeEventListener('keydown', handleEsc)
    }
  }, [onClose])

  return (
    <div
      ref={popupRef}
      className="absolute top-full left-0 mt-2 z-[100] w-80 rounded-xl border overflow-hidden"
      style={{
        backgroundColor: 'var(--bg-elevated)',
        borderColor: 'var(--border)',
        boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
      }}
      onClick={(e) => e.stopPropagation()}
    >
      {/* 头部 */}
      <div className="p-4 pb-3" style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-2">
            <span style={{ color: 'var(--accent)' }}>&#9670;</span>
            <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
              Easy Essay
            </span>
          </div>
          <span
            className="text-[10px] px-1.5 py-0.5 rounded-full"
            style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }}
          >
            v{APP_VERSION}
          </span>
        </div>
        <a
          href={GITHUB_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 text-xs mt-1 transition-opacity hover:opacity-80"
          style={{ color: 'var(--text-muted)' }}
        >
          <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
            <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
          </svg>
          GitHub
          <svg className="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
          </svg>
        </a>
      </div>

      {/* 更新日志 */}
      <div className="p-4 pt-3 max-h-72 overflow-auto">
        <p className="text-[10px] font-medium mb-2" style={{ color: 'var(--text-muted)' }}>更新日志</p>
        {RELEASE_NOTES.map((release) => (
          <div key={release.version} className="mb-3 last:mb-0">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
                v{release.version}
              </span>
              <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                {release.date}
              </span>
            </div>
            <ul className="space-y-0.5">
              {release.changes.map((change, i) => (
                <li key={i} className="text-[11px] leading-relaxed pl-2" style={{ color: 'var(--text-secondary)' }}>
                  <span style={{ color: 'var(--text-muted)' }}>· </span>{change}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  )
}

export { APP_VERSION }
