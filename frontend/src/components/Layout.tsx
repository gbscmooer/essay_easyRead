import { useState, useEffect } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { TerminalPanel } from './TerminalPanel'
import { ThemeProvider } from './ThemeProvider'
import { getPaper } from '@/api/papers'


interface LayoutProps {
  children: React.ReactNode
}


const primaryNav = [
  { path: '/', label: '首页' },
  { path: '/library', label: '论文库' },
  { path: '/workspace', label: '工作区' },
  { path: '/settings', label: '配置' },
]


function useCurrentPaperId(): number | null {
  const location = useLocation()
  const match = location.pathname.match(/^\/(?:reader|code)\/(\d+)/)
  return match ? Number(match[1]) : null
}


export function Layout({ children }: LayoutProps) {
  const location = useLocation()
  const paperId = useCurrentPaperId()
  const [paperTitle, setPaperTitle] = useState<string | null>(null)
  const [navCollapsed, setNavCollapsed] = useState(false)

  useEffect(() => {
    if (paperId) {
      getPaper(paperId)
        .then(p => setPaperTitle(p.title || p.filename))
        .catch(() => setPaperTitle(null))
    } else {
      setPaperTitle(null)
    }
  }, [paperId])

  return (
    <ThemeProvider>
    <div className="flex flex-col h-screen" style={{ backgroundColor: 'var(--bg-base)' }}>
      {/* 一级导航栏 */}
      <nav className={`flex items-center justify-between px-6 bg-gray-900 border-b border-gray-800 shrink-0 transition-all duration-200 ${navCollapsed ? 'h-8' : 'h-12'}`}>
        <Link to="/" className={`font-bold text-gray-100 hover:text-blue-400 transition-colors ${navCollapsed ? 'text-sm' : 'text-base'}`}>
          论文精读助手
        </Link>

        {!navCollapsed && (
          <div className="flex items-center gap-6">
            {primaryNav.map(item => (
              <Link
                key={item.path}
                to={item.path}
                className={`text-sm transition-colors ${
                  location.pathname === item.path
                    ? 'text-blue-400 font-medium'
                    : 'text-gray-400 hover:text-gray-200'
                }`}
              >
                {item.label}
              </Link>
            ))}
          </div>
        )}

        <button
          onClick={() => setNavCollapsed(v => !v)}
          className="p-1 text-gray-500 hover:text-gray-300 transition-colors"
          title={navCollapsed ? '展开导航栏' : '收起导航栏'}
        >
          <svg className={`w-4 h-4 transition-transform ${navCollapsed ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
          </svg>
        </button>
      </nav>

      {/* 二级导航栏（仅在查看论文时显示，且未收起时）*/}
      {paperId && !navCollapsed && (
        <div className="flex items-center justify-between px-6 h-10 bg-gray-900/80 border-b border-gray-800 shrink-0">
          <div className="flex items-center gap-4">
            <span className="text-xs text-gray-500 truncate max-w-xs">
              {paperTitle || '加载中...'}
            </span>
            <div className="flex items-center gap-1">
              <Link
                to={`/reader/${paperId}`}
                className={`px-3 py-1 text-xs rounded transition-colors ${
                  location.pathname.startsWith('/reader')
                    ? 'bg-blue-500/10 text-blue-400 font-medium'
                    : 'text-gray-500 hover:text-gray-300 hover:bg-gray-800'
                }`}
              >
                论文分析
              </Link>
              <Link
                to={`/code/${paperId}`}
                className={`px-3 py-1 text-xs rounded transition-colors ${
                  location.pathname.startsWith('/code')
                    ? 'bg-blue-500/10 text-blue-400 font-medium'
                    : 'text-gray-500 hover:text-gray-300 hover:bg-gray-800'
                }`}
              >
                源码分析
              </Link>
            </div>
          </div>
          <Link
            to="/library"
            className="text-xs text-gray-600 hover:text-gray-400 transition-colors"
          >
            返回论文库
          </Link>
        </div>
      )}

      {/* 内容区 */}
      <main className="flex-1 overflow-auto">
        {children}
      </main>

      {/* 底部终端面板 */}
      <TerminalPanel />
    </div>
    </ThemeProvider>
  )
}
