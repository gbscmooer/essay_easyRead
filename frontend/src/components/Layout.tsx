/**
 * Application frame — route-aware constructivist experience shell.
 *
 * Business routes stay unchanged. The shell translates each route into a
 * small visual state (`data-scene` / `data-route` / `data-motion`) consumed by
 * the presentation layer, keeping navigation logic and visual rendering
 * decoupled.
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { Link, useBlocker, useLocation, useNavigate } from 'react-router-dom'
import { TerminalPanel } from './TerminalPanel'
import { ThemeProvider } from './ThemeProvider'
import { APP_VERSION, VersionPopup } from './VersionPopup'
import { ShortcutHelp } from './ShortcutHelp'
import { ConfirmDialog } from '@/ds-entry'
import { useConfigDirty, useUIConfig, setUIPreview } from '@/hooks/useConfig'
import { setHelpCallback, useGlobalShortcuts } from '@/hooks/useKeyboardShortcuts'
import { useMotionPreference } from '@/hooks/useMotionPreference'

interface LayoutProps {
  children: ReactNode
}

type SceneKey = 'origin' | 'archive' | 'field' | 'process'

interface RouteVisual {
  index: string
  route: string
  scene: SceneKey
  eyebrow: string
  title: string
  register: string
}

const primaryNav = [
  { path: '/', label: '首页' },
  { path: '/library', label: '论文库' },
  { path: '/workspace', label: '工作区' },
  { path: '/explore', label: '文献研究' },
  { path: '/settings', label: '设置' },
] as const

function routeVisual(pathname: string): RouteVisual {
  if (/^\/reader\//.test(pathname)) {
    return { index: '05', route: 'reader', scene: 'field', eyebrow: 'PAPER / EVIDENCE SURFACE', title: '论文精读', register: 'READ—PLANE' }
  }
  if (/^\/code\//.test(pathname)) {
    return { index: '06', route: 'code', scene: 'process', eyebrow: 'SOURCE / IMPLEMENTATION MAP', title: '源码分析', register: 'CODE—TRACE' }
  }
  if (/^\/workspace\/\d+\/(research|graph)/.test(pathname)) {
    return { index: '07', route: 'research', scene: 'field', eyebrow: 'RELATION / RESEARCH MODEL', title: '工作区图谱', register: 'GRAPH—FIELD' }
  }
  if (/^\/workspace\/\d+/.test(pathname)) {
    return { index: '02', route: 'workspace-detail', scene: 'field', eyebrow: 'COLLECTION / READING FIELD', title: '工作区详情', register: 'SET—INDEX' }
  }
  if (pathname === '/library') {
    return { index: '01', route: 'library', scene: 'archive', eyebrow: 'RECORD / PAPER ARCHIVE', title: '论文档案', register: 'ARCHIVE—PLANE' }
  }
  if (pathname === '/workspace') {
    return { index: '02', route: 'workspace', scene: 'field', eyebrow: 'COLLECTION / RELATION FIELD', title: '研究工作区', register: 'FIELD—SET' }
  }
  if (pathname === '/explore' || pathname.startsWith('/explorer/')) {
    return { index: '03', route: 'explore', scene: 'field', eyebrow: 'QUESTION / DOMAIN SURVEY', title: '文献研究', register: 'SCOUT—FIELD' }
  }
  if (pathname === '/settings') {
    return { index: '04', route: 'settings', scene: 'process', eyebrow: 'PROTOCOL / SYSTEM CONTROL', title: '系统设置', register: 'CONTROL—AXIS' }
  }
  if (pathname === '/') {
    return { index: '00', route: 'home', scene: 'origin', eyebrow: 'INPUT / RESEARCH ORIGIN', title: '论文研究入口', register: 'SOURCE—PLANE' }
  }
  return { index: '99', route: 'not-found', scene: 'archive', eyebrow: 'ROUTE / NOT REGISTERED', title: '未找到页面', register: 'NULL—POINT' }
}

function isNavActive(path: string, pathname: string): boolean {
  if (path === '/') return pathname === '/'
  if (path === '/library') return pathname === '/library' || /^\/(reader|code)\//.test(pathname)
  if (path === '/workspace') return pathname.startsWith('/workspace')
  return pathname.startsWith(path)
}

function useCurrentPaperId(): number | null {
  const match = useLocation().pathname.match(/^\/(?:reader|code)\/(\d+)/)
  return match ? Number(match[1]) : null
}

function useCurrentWorkspaceId(): number | null {
  const match = useLocation().pathname.match(/^\/workspace\/(\d+)(?:\/|$)/)
  return match ? Number(match[1]) : null
}

interface ContextLinkProps {
  to: string
  active: boolean
  children: ReactNode
}

function ContextLink({ to, active, children }: ContextLinkProps) {
  return (
    <Link
      className={`px-3 py-1 rounded-md text-xs font-medium transition-all ${
        active
          ? 'bg-[var(--bg-surface)] text-[var(--accent)] font-semibold shadow-xs'
          : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-elevated)]'
      }`}
      data-active={active || undefined}
      aria-current={active ? 'page' : undefined}
      to={to}
    >
      {children}
    </Link>
  )
}

export function Layout({ children }: LayoutProps) {
  const location = useLocation()
  const navigate = useNavigate()
  const visual = useMemo(() => routeVisual(location.pathname), [location.pathname])
  const paperId = useCurrentPaperId()
  const workspaceId = useCurrentWorkspaceId()
  const configDirty = useConfigDirty()
  const uiConfig = useUIConfig()
  const isDark = uiConfig.theme === 'dark'

  const toggleTheme = useCallback(() => {
    const next = isDark ? 'light' : 'dark'
    setUIPreview({ theme: next })
    try {
      localStorage.setItem('essay_theme', next)
    } catch {}
  }, [isDark])

  const blocker = useBlocker(({ currentLocation, nextLocation }) => (
    currentLocation.pathname.startsWith('/settings')
    && nextLocation.pathname !== currentLocation.pathname
    && configDirty
  ))
  const motion = useMotionPreference()
  const [showVersion, setShowVersion] = useState(false)
  const [showShortcuts, setShowShortcuts] = useState(false)
  const [showUnsavedConfirm, setShowUnsavedConfirm] = useState(false)

  useGlobalShortcuts(navigate)

  const toggleShortcuts = useCallback(() => setShowShortcuts(value => !value), [])
  useEffect(() => {
    setHelpCallback(toggleShortcuts)
    return () => setHelpCallback(null)
  }, [toggleShortcuts])

  useEffect(() => {
    if (blocker.state !== 'blocked') return
    setShowUnsavedConfirm(true)
  }, [blocker.state])

  const contextItems: ReactNode[] = []
  if (paperId) {
    const fromPath = (location.state as { from?: string } | null)?.from
    const workspaceMatch = fromPath?.match(/^\/workspace\/(\d+)/)
    const backTo = workspaceMatch ? `/workspace/${workspaceMatch[1]}` : '/library'
    contextItems.push(
      <ContextLink key="reader" to={`/reader/${paperId}`} active={location.pathname.startsWith('/reader')}>论文分析</ContextLink>,
      <ContextLink key="code" to={`/code/${paperId}`} active={location.pathname.startsWith('/code')}>源码分析</ContextLink>,
      <ContextLink key="back" to={backTo} active={false}>{workspaceMatch ? '返回工作区' : '返回论文库'} ↑</ContextLink>,
    )
  } else if (workspaceId) {
    contextItems.push(
      <ContextLink key="detail" to={`/workspace/${workspaceId}`} active={location.pathname === `/workspace/${workspaceId}`}>论文集合</ContextLink>,
      <ContextLink key="research" to={`/explore?workspace_id=${workspaceId}`} active={false}>发起研究</ContextLink>,
      <ContextLink key="graph" to={`/workspace/${workspaceId}/graph`} active={location.pathname.includes('/graph')}>工作区图谱</ContextLink>,
    )
  }

  return (
    <ThemeProvider>
      <div
        className="er-experience flex flex-col h-screen w-screen overflow-hidden bg-[var(--bg-base)] text-[var(--text-primary)]"
        data-scene={visual.scene}
        data-route={visual.route}
        data-motion={motion.enabled ? 'full' : 'reduced'}
      >
        <a className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:px-3 focus:py-1.5 focus:bg-blue-600 focus:text-white focus:rounded-md" href="#er-main-content">跳到主要内容</a>

        {/* 现代化顶部导航栏 */}
        <header className="h-14 shrink-0 px-4 sm:px-6 bg-[var(--bg-surface)] border-b border-[var(--border)] flex items-center justify-between z-30 select-none">
          {/* 左侧：Logo 与主导航 */}
          <div className="flex items-center gap-6">
            <div className="flex items-center gap-2">
              <Link to="/" className="flex items-center gap-2.5 group">
                <span className="w-8 h-8 rounded-lg bg-blue-600 text-white flex items-center justify-center font-bold text-sm shadow-xs group-hover:scale-105 transition-transform">
                  论
                </span>
                <span className="font-bold text-[15px] tracking-tight text-[var(--text-primary)]">
                  论文精读
                </span>
              </Link>
              <button
                type="button"
                onClick={() => setShowVersion(true)}
                className="text-[11px] font-mono px-1.5 py-0.5 rounded bg-[var(--bg-elevated)] text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors border border-[var(--border)] cursor-pointer"
                title="查看版本与更新日志"
                aria-label="查看论文精读辅助分析软件版本信息"
              >
                v{APP_VERSION}
              </button>
            </div>

            {/* 主要导航 */}
            <nav className="flex items-center gap-1" aria-label="主要导航">
              {primaryNav.map(item => {
                const active = isNavActive(item.path, location.pathname)
                return (
                  <Link
                    key={item.path}
                    to={item.path}
                    className={`px-3 py-1.5 rounded-lg text-[13px] font-medium transition-all ${
                      active
                        ? 'bg-blue-50 text-blue-600 dark:bg-blue-950/60 dark:text-blue-400 font-semibold shadow-2xs'
                        : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-elevated)]'
                    }`}
                    data-active={active || undefined}
                    aria-current={active ? 'page' : undefined}
                  >
                    {item.label}
                  </Link>
                )
              })}
            </nav>
          </div>

          {/* 右侧：上下文导航与操作按钮 */}
          <div className="flex items-center gap-3">
            {contextItems.length > 0 && (
              <nav className="flex items-center gap-1 p-1 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border)]" aria-label="当前内容导航">
                {contextItems}
              </nav>
            )}

            <div className="flex items-center gap-1 border-l border-[var(--border)] pl-2 sm:pl-3">
              {/* 主题切换 */}
              <button
                type="button"
                onClick={toggleTheme}
                className="p-2 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-elevated)] transition-colors cursor-pointer"
                title={isDark ? '切换至明亮模式' : '切换至深色模式'}
                aria-label="切换主题颜色"
              >
                {isDark ? (
                  <svg className="w-4 h-4 text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
                  </svg>
                ) : (
                  <svg className="w-4 h-4 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
                  </svg>
                )}
              </button>

              {/* 快捷键帮助 */}
              <button
                type="button"
                onClick={toggleShortcuts}
                className="p-2 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-elevated)] transition-colors cursor-pointer"
                title="键盘快捷键 (?)"
                aria-label="快捷键帮助"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </button>
            </div>
          </div>
        </header>

        {/* 主内容区域 */}
        <div className="flex-1 min-h-0 w-full overflow-hidden flex flex-col relative">
          <main id="er-main-content" className="flex-1 min-h-0 w-full overflow-hidden flex flex-col relative" tabIndex={-1}>
            <div className="h-full w-full overflow-auto flex flex-col" key={location.pathname}>
              {children}
            </div>
          </main>
          <TerminalPanel />
        </div>

        {showVersion && <VersionPopup onClose={() => setShowVersion(false)} />}
        <ShortcutHelp open={showShortcuts} onClose={toggleShortcuts} />
        <ConfirmDialog
          open={showUnsavedConfirm}
          title="未保存的更改"
          message="有未保存的配置更改，确定离开吗？"
          variant="warning"
          confirmLabel="离开"
          cancelLabel="留下"
          onConfirm={() => {
            setShowUnsavedConfirm(false)
            if (blocker.state === 'blocked') {
              blocker.proceed()
            }
          }}
          onCancel={() => {
            setShowUnsavedConfirm(false)
            if (blocker.state === 'blocked') blocker.reset()
          }}
        />
      </div>
    </ThemeProvider>
  )
}
