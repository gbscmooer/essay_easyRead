/**
 * 布局组件 — 可折叠左轨导航 + 浮动药丸二级导航
 *
 * macOS Finder 风格侧边栏：64px 折叠 / 240px 展开。
 * 二级导航（论文/工作区标签）以 frosted glass 浮动工具栏呈现。
 */
import { useState, useCallback, useRef, useEffect } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { TerminalPanel } from './TerminalPanel'
import { ThemeProvider } from './ThemeProvider'
import { VersionPopup } from './VersionPopup'
import { ShortcutHelp } from './ShortcutHelp'
import { ConfirmDialog } from '@/ds-entry'
import { hasUnsavedConfig } from '@/hooks/useConfig'
import { useGlobalShortcuts, setHelpCallback } from '@/hooks/useKeyboardShortcuts'

interface LayoutProps {
  children: React.ReactNode
}

/* ============================================================
 * 导航数据 & 图标
 * ============================================================ */

const RAIL_W_COLLAPSED = 64
const RAIL_W_EXPANDED = 240

const navIcons: Record<string, (active: boolean) => React.ReactNode> = {
  '/': (active) => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={active ? 2 : 1.5}
      strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
      <polyline points="9 22 9 12 15 12 15 22" />
    </svg>
  ),
  '/library': (active) => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={active ? 2 : 1.5}
      strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
      <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
    </svg>
  ),
  '/workspace': (active) => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={active ? 2 : 1.5}
      strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="7" width="20" height="14" rx="2" />
      <path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2" />
      <line x1="2" y1="13" x2="22" y2="13" />
    </svg>
  ),
  '/settings': (active) => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={active ? 2 : 1.5}
      strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  ),
}

const primaryNav = [
  { path: '/', label: '首页' },
  { path: '/library', label: '论文库' },
  { path: '/workspace', label: '工作区' },
  { path: '/settings', label: '设置' },
]

const SEED_RADIUS = 'var(--seed-radius, 12px)'

/* ============================================================
 * 左轨导航项 — 含 hover 反馈 + active 强调条 + 焦点光晕
 * ============================================================ */

interface NavRailItemProps {
  path: string
  label: string
  active: boolean
  collapsed: boolean
  onClick: (e: React.MouseEvent) => void
}

function NavRailItem({ path, label, active, collapsed, onClick }: NavRailItemProps) {
  const [hover, setHover] = useState(false)

  const bg = active
    ? 'var(--accent-subtle)'
    : hover
      ? 'var(--bg-elevated)'
      : 'transparent'

  return (
    <Link
      to={path}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      className="focus-glow flex items-center gap-3 py-2.5 transition-all duration-200 press"
      style={{
        paddingLeft: collapsed ? 0 : 12,
        paddingRight: collapsed ? 0 : 12,
        justifyContent: collapsed ? 'center' : 'flex-start',
        color: active ? 'var(--accent)' : hover ? 'var(--text-primary)' : 'var(--text-secondary)',
        backgroundColor: bg,
        borderRadius: SEED_RADIUS,
        transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
        position: 'relative',
      }}
      title={collapsed ? label : undefined}
      aria-current={active ? 'page' : undefined}
    >
      {/* Active 指示条 */}
      {active && (
        <span
          className="absolute left-0 top-1/2 -translate-y-1/2 rounded-r-full transition-all duration-200"
          style={{
            width: 3,
            height: 20,
            backgroundColor: 'var(--accent)',
          }}
        />
      )}
      <span className="shrink-0">
        {navIcons[path]?.(active)}
      </span>
      <span
        className="text-[13px] whitespace-nowrap transition-all duration-200"
        style={{
          fontWeight: active ? 500 : 400,
          opacity: collapsed ? 0 : 1,
          width: collapsed ? 0 : 'auto',
          overflow: 'hidden',
        }}
      >
        {label}
      </span>
    </Link>
  )
}

/* ============================================================
 * 二级导航药丸 — hover/active/focus 三态清晰区分
 * ============================================================ */

interface PillLinkProps {
  to: string
  active: boolean
  state?: unknown
  onClick?: (e: React.MouseEvent) => void
  children: React.ReactNode
}

function PillLink({ to, active, state, onClick, children }: PillLinkProps) {
  const [hover, setHover] = useState(false)
  return (
    <Link
      to={to}
      state={state}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      className="focus-glow px-3 py-1.5 text-[13px] transition-all press"
      style={
        active
          ? { backgroundColor: 'var(--accent)', color: '#fff', fontWeight: 500, borderRadius: SEED_RADIUS }
          : {
              color: hover ? 'var(--text-primary)' : 'var(--text-muted)',
              backgroundColor: hover ? 'var(--accent-subtle)' : 'transparent',
              borderRadius: SEED_RADIUS,
            }
      }
      aria-current={active ? 'page' : undefined}
    >
      {children}
    </Link>
  )
}

interface BackLinkProps {
  to: string
  label: string
  onClick: (e: React.MouseEvent) => void
}

function BackLink({ to, label, onClick }: BackLinkProps) {
  const [hover, setHover] = useState(false)
  return (
    <Link
      to={to}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      className="focus-glow text-[12px] px-2 py-1 transition-all press"
      style={{
        color: hover ? 'var(--text-primary)' : 'var(--text-muted)',
        backgroundColor: hover ? 'var(--accent-subtle)' : 'transparent',
        borderRadius: SEED_RADIUS,
      }}
    >
      {label} &rarr;
    </Link>
  )
}

/* ============================================================
 * 路由辅助
 * ============================================================ */

function useCurrentPaperId(): number | null {
  const match = useLocation().pathname.match(/^\/(?:reader|code)\/(\d+)/)
  return match ? Number(match[1]) : null
}

function useCurrentWorkspaceId(): number | null {
  const m = useLocation().pathname.match(/^\/workspace\/(\d+)(?:\/|$)/)
  return m ? Number(m[1]) : null
}

function isNavActive(path: string, pathname: string): boolean {
  if (path === '/') return pathname === '/'
  return pathname.startsWith(path)
}

/* ============================================================
 * Layout
 * ============================================================ */

export function Layout({ children }: LayoutProps) {
  const location = useLocation()
  const navigate = useNavigate()
  const paperId = useCurrentPaperId()
  const wsId = useCurrentWorkspaceId()
  const [navCollapsed, setNavCollapsed] = useState(false)
  const [collapseHover, setCollapseHover] = useState(false)
  const [showVersion, setShowVersion] = useState(false)
  const [showShortcuts, setShowShortcuts] = useState(false)
  const [showUnsavedConfirm, setShowUnsavedConfirm] = useState(false)
  const pendingNavRef = useRef<string | null>(null)
  const pendingNavStateRef = useRef<unknown>(null)

  const railW = navCollapsed ? RAIL_W_COLLAPSED : RAIL_W_EXPANDED

  useGlobalShortcuts(navigate)

  // Register ? shortcut
  const toggleShortcuts = useCallback(() => setShowShortcuts(v => !v), [])
  useEffect(() => {
    setHelpCallback(toggleShortcuts)
    return () => { setHelpCallback(null) }
  }, [toggleShortcuts])

  const toggleVersion = useCallback(() => setShowVersion(v => !v), [])
  const closeVersion = useCallback(() => setShowVersion(false), [])

  // Navigation guard for unsaved settings
  const handleNavGuard = useCallback((target: string, e: React.MouseEvent, state?: unknown) => {
    if (location.pathname.startsWith('/settings') && hasUnsavedConfig()) {
      e.preventDefault()
      pendingNavRef.current = target
      pendingNavStateRef.current = state ?? null
      setShowUnsavedConfirm(true)
    }
  }, [location.pathname])

  // Secondary nav items
  const secondaryItems: React.ReactNode[] = []

  if (paperId) {
    const fromPath = (location.state as { from?: string } | null)?.from
    const wsMatch = fromPath?.match(/^\/workspace\/(\d+)/)
    const backTo = wsMatch ? `/workspace/${wsMatch[1]}` : '/library'
    const backLabel = wsMatch ? '返回工作区' : '返回论文库'

    secondaryItems.push(
      <div key="paper-tabs" className="flex items-center gap-0.5">
        <PillLink
          to={`/reader/${paperId}`}
          state={location.state}
          active={location.pathname.startsWith('/reader')}
          onClick={(e) => handleNavGuard(`/reader/${paperId}`, e, location.state)}
        >
          论文分析
        </PillLink>
        <PillLink
          to={`/code/${paperId}`}
          state={location.state}
          active={location.pathname.startsWith('/code')}
          onClick={(e) => handleNavGuard(`/code/${paperId}`, e, location.state)}
        >
          源码分析
        </PillLink>
      </div>,
      <span key="sep" className="w-px h-4 mx-1" style={{ backgroundColor: 'var(--border)' }} />,
      <BackLink
        key="back"
        to={backTo}
        onClick={(e) => handleNavGuard(backTo, e)}
        label={backLabel}
      />,
    )
  }

  if (wsId && !paperId) {
    secondaryItems.push(
      <PillLink
        key="ws-detail"
        to={`/workspace/${wsId}`}
        active={location.pathname === `/workspace/${wsId}`}
      >
        论文列表
      </PillLink>,
      <PillLink
        key="ws-research"
        to={`/workspace/${wsId}/research`}
        active={location.pathname.includes('/research')}
      >
        深度研究
      </PillLink>,
    )
  }

  return (
    <ThemeProvider>
      <div className="flex h-screen relative overflow-hidden" style={{ backgroundColor: 'var(--bg-base)' }}>

        {/* 极光环境背景 */}
        <div className="app-aurora" aria-hidden="true" />

        {/* ── 左轨导航 ── */}
        <nav
          className="shrink-0 flex flex-col transition-[width] duration-300 relative z-10"
          style={{
            width: railW,
            backgroundColor: 'var(--bg-surface)',
            borderRight: '1px solid var(--border)',
            transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
          }}
        >
          {/* Logo */}
          <button
            onClick={toggleVersion}
            aria-label="Easy Essay"
            className="focus-glow flex items-center gap-2.5 p-4 cursor-pointer transition-opacity hover:opacity-80 press"
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--text-primary)',
              justifyContent: navCollapsed ? 'center' : 'flex-start',
              borderRadius: SEED_RADIUS,
            }}
          >
            <img
              src="/logo.jpg"
              alt="Easy Essay"
              className="w-8 h-8 rounded-lg object-cover shrink-0"
              style={{
                boxShadow: '0 1px 3px rgba(0,0,0,0.08)',
              }}
            />
            <span
              className="text-[15px] font-semibold whitespace-nowrap transition-all duration-200"
              style={{
                opacity: navCollapsed ? 0 : 1,
                width: navCollapsed ? 0 : 'auto',
                overflow: 'hidden',
                letterSpacing: '-0.01em',
              }}
            >
              Easy Essay
            </span>
          </button>

          {/* 分割线 */}
          <div className="mx-4 my-1" style={{ height: 1, backgroundColor: 'var(--border)' }} />

          {/* 导航项 */}
          <div className="flex-1 py-2 px-2 space-y-0.5">
            {primaryNav.map(item => (
              <NavRailItem
                key={item.path}
                path={item.path}
                label={item.label}
                active={isNavActive(item.path, location.pathname)}
                collapsed={navCollapsed}
                onClick={(e) => handleNavGuard(item.path, e)}
              />
            ))}
          </div>

          {/* 收起/展开按钮 */}
          <div className="p-3 shrink-0" style={{ borderTop: '1px solid var(--border)' }}>
            <button
              onClick={() => setNavCollapsed(v => !v)}
              onMouseEnter={() => setCollapseHover(true)}
              onMouseLeave={() => setCollapseHover(false)}
              aria-label={navCollapsed ? '展开侧边栏' : '收起侧边栏'}
              aria-expanded={!navCollapsed}
              className="focus-glow w-full flex items-center justify-center p-2 transition-all press"
              style={{
                color: collapseHover ? 'var(--text-primary)' : 'var(--text-muted)',
                backgroundColor: collapseHover ? 'var(--bg-elevated)' : 'transparent',
                borderRadius: SEED_RADIUS,
              }}
              title={navCollapsed ? '展开侧边栏' : '收起侧边栏'}
            >
              <svg
                className="transition-transform duration-300"
                style={{
                  transform: navCollapsed ? 'rotate(180deg)' : 'rotate(0deg)',
                  transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
                }}
                width="16"
                height="16"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 19l-7-7 7-7" />
              </svg>
            </button>
          </div>
        </nav>

        {/* ── 内容区 ── */}
        <div className="flex-1 flex flex-col min-w-0 relative z-10">
          {/* 二级导航 — 浮动药丸工具栏 */}
          {secondaryItems.length > 0 && (
            <div className="flex justify-center pt-3 shrink-0" style={{ zIndex: 20 }}>
              <div
                className="glass-toolbar flex items-center gap-1 px-1.5 py-1"
                style={{ borderRadius: 'var(--seed-radius, 12px)' }}
              >
                {secondaryItems}
              </div>
            </div>
          )}

          {/* 页面内容 */}
          <main className="flex-1 overflow-auto">
            {children}
          </main>

          {/* 底部终端面板 */}
          <TerminalPanel />
        </div>

        {/* 版本信息弹窗 */}
        {showVersion && <VersionPopup onClose={closeVersion} />}

        {/* 快捷键帮助 */}
        <ShortcutHelp open={showShortcuts} onClose={toggleShortcuts} />

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
