import { useState, useCallback, useRef } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { TerminalPanel } from './TerminalPanel'
import { ThemeProvider } from './ThemeProvider'
import { VersionPopup } from './VersionPopup'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { hasUnsavedConfig } from '@/hooks/useConfig'


interface LayoutProps {
  children: React.ReactNode
}


const primaryNav = [
  { path: '/', label: '首页' },
  { path: '/library', label: '论文库' },
  { path: '/workspace', label: '工作区' },
  { path: '/settings', label: '设置' },
]


function useCurrentPaperId(): number | null {
  const location = useLocation()
  const match = location.pathname.match(/^\/(?:reader|code)\/(\d+)/)
  return match ? Number(match[1]) : null
}

function useCurrentWorkspaceId(): number | null {
  const m = useLocation().pathname.match(/^\/workspace\/(\d+)(?:\/|$)/)
  return m ? Number(m[1]) : null
}


export function Layout({ children }: LayoutProps) {
  const location = useLocation()
  const navigate = useNavigate()
  const paperId = useCurrentPaperId()
  const wsId = useCurrentWorkspaceId()
  const [navCollapsed, setNavCollapsed] = useState(false)
  const [showVersion, setShowVersion] = useState(false)
  const [showUnsavedConfirm, setShowUnsavedConfirm] = useState(false)
  const pendingNavRef = useRef<string | null>(null)
  const pendingNavStateRef = useRef<unknown>(null)

  const toggleVersion = useCallback(() => setShowVersion(v => !v), [])
  const closeVersion = useCallback(() => setShowVersion(false), [])

  // Unified handler: if on settings with unsaved config, show confirm dialog; otherwise navigate directly
  const handleNavGuard = useCallback((target: string, e: React.MouseEvent, state?: unknown) => {
    if (location.pathname.startsWith('/settings') && hasUnsavedConfig()) {
      e.preventDefault()
      pendingNavRef.current = target
      pendingNavStateRef.current = state ?? null
      setShowUnsavedConfirm(true)
    }
  }, [location.pathname])

  return (
    <ThemeProvider>
    <div className="flex flex-col h-screen" style={{ backgroundColor: 'var(--bg-base)' }}>
      {/* 一级导航栏 */}
      <nav className={`flex items-center justify-between px-6 shrink-0 transition-all duration-200 ${
        navCollapsed ? 'h-10' : 'h-14'
      }`} style={{
        backgroundColor: 'var(--bg-surface)',
        borderBottom: '1px solid var(--border)',
      }}>
        <button
          onClick={toggleVersion}
          className={`font-semibold transition-colors cursor-pointer ${navCollapsed ? 'text-sm' : 'text-base'}`}
          style={{ color: 'var(--text-primary)', background: 'none', border: 'none' }}
        >
          <span style={{ color: 'var(--accent)' }}>&#9670;</span> Easy Essay
        </button>

        {!navCollapsed && (
          <div className="flex items-center gap-1">
            {primaryNav.map(item => (
              <Link
                key={item.path}
                to={item.path}
                onClick={(e) => handleNavGuard(item.path, e)}
                className={`px-3 py-1.5 rounded-md text-sm transition-colors ${
                  location.pathname === item.path
                    ? 'font-medium'
                    : 'hover:opacity-80'
                }`}
                style={
                  location.pathname === item.path
                    ? { color: 'var(--accent)', backgroundColor: 'var(--accent-subtle)' }
                    : { color: 'var(--text-secondary)' }
                }
              >
                {item.label}
              </Link>
            ))}
          </div>
        )}

        <button
          onClick={() => setNavCollapsed(v => !v)}
          className="p-1.5 rounded-md transition-colors"
          style={{ color: 'var(--text-muted)' }}
          title={navCollapsed ? '展开导航栏' : '收起导航栏'}
        >
          <svg className={`w-4 h-4 transition-transform ${navCollapsed ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M5 15l7-7 7 7" />
          </svg>
        </button>
      </nav>

      {/* 二级导航栏（仅在查看论文时显示，且未收起时）*/}
      {paperId && !navCollapsed && (
        <div className="flex items-center justify-between px-6 h-11 shrink-0" style={{
          backgroundColor: 'var(--bg-surface)',
          borderBottom: '1px solid var(--border)',
          opacity: 0.97,
        }}>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-1">
              <Link
                to={`/reader/${paperId}`}
                state={location.state}
                onClick={(e) => handleNavGuard(`/reader/${paperId}`, e, location.state)}
                className={`px-3 py-1 text-xs rounded-md transition-colors ${
                  location.pathname.startsWith('/reader')
                    ? 'font-medium'
                    : ''
                }`}
                style={
                  location.pathname.startsWith('/reader')
                    ? { backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }
                    : { color: 'var(--text-muted)' }
                }
              >
                论文分析
              </Link>
              <Link
                to={`/code/${paperId}`}
                state={location.state}
                onClick={(e) => handleNavGuard(`/code/${paperId}`, e, location.state)}
                className={`px-3 py-1 text-xs rounded-md transition-colors ${
                  location.pathname.startsWith('/code')
                    ? 'font-medium'
                    : ''
                }`}
                style={
                  location.pathname.startsWith('/code')
                    ? { backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }
                    : { color: 'var(--text-muted)' }
                }
              >
                源码分析
              </Link>
            </div>
          </div>
          {(() => {
            const fromPath = (location.state as { from?: string } | null)?.from
            const wsMatch = fromPath?.match(/^\/workspace\/(\d+)/)
            const backTo = wsMatch ? `/workspace/${wsMatch[1]}` : '/library'
            const backLabel = wsMatch ? '返回工作区' : '返回论文库'
            return (
              <Link
                to={backTo}
                onClick={(e) => handleNavGuard(backTo, e)}
                className="text-xs transition-colors"
                style={{ color: 'var(--text-muted)' }}
              >
                {backLabel} &rarr;
              </Link>
            )
          })()}
        </div>
      )}

      {/* 工作区级二级导航（在工作区页面显示，且不在论文页面时）*/}
      {wsId && !paperId && !navCollapsed && (
        <div className="flex items-center gap-4 px-6 h-11 shrink-0" style={{
          backgroundColor: 'var(--bg-surface)',
          borderBottom: '1px solid var(--border)',
          opacity: 0.97,
        }}>
          <Link
            to={`/workspace/${wsId}`}
            className={`px-3 py-1 text-xs rounded-md transition-colors ${
              location.pathname === `/workspace/${wsId}`
                ? 'font-medium'
                : ''
            }`}
            style={
              location.pathname === `/workspace/${wsId}`
                ? { backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }
                : { color: 'var(--text-muted)' }
            }
          >
            论文列表
          </Link>
          <Link
            to={`/workspace/${wsId}/research`}
            className={`px-3 py-1 text-xs rounded-md transition-colors ${
              location.pathname.includes('/research')
                ? 'font-medium'
                : ''
            }`}
            style={
              location.pathname.includes('/research')
                ? { backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }
                : { color: 'var(--text-muted)' }
            }
          >
            深度研究 ⚡
          </Link>
        </div>
      )}

      {/* 内容区 */}
      <main className="flex-1 overflow-auto">
        {children}
      </main>

      {/* 底部终端面板 */}
      <TerminalPanel />

      {/* 版本信息弹窗 */}
      {showVersion && <VersionPopup onClose={closeVersion} />}

      {/* 未保存配置确认 */}
      <ConfirmDialog
        open={showUnsavedConfirm}
        title="未保存的更改"
        message="有未保存的配置更改，确定离开吗？"
        variant="warning"
        confirmLabel="离开"
        cancelLabel="留下"
        onConfirm={() => {
          setShowUnsavedConfirm(false)
          const target = pendingNavRef.current
          const state = pendingNavStateRef.current
          pendingNavRef.current = null
          pendingNavStateRef.current = null
          if (target) navigate(target, state ? { state } : undefined)
        }}
        onCancel={() => {
          setShowUnsavedConfirm(false)
          pendingNavRef.current = null
          pendingNavStateRef.current = null
        }}
      />
    </div>
    </ThemeProvider>
  )
}
