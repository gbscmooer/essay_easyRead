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
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from 'react'
import { Link, useBlocker, useLocation, useNavigate } from 'react-router-dom'
import { TerminalPanel } from './TerminalPanel'
import { ThemeProvider } from './ThemeProvider'
import { VersionPopup } from './VersionPopup'
import { ShortcutHelp } from './ShortcutHelp'
import { ConfirmDialog } from '@/ds-entry'
import { useConfigDirty } from '@/hooks/useConfig'
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
  { path: '/', index: '00', label: '入口', register: 'ORIGIN' },
  { path: '/library', index: '01', label: '论文库', register: 'ARCHIVE' },
  { path: '/workspace', index: '02', label: '工作区', register: 'FIELD' },
  { path: '/settings', index: '03', label: '设置', register: 'SYSTEM' },
] as const

function routeVisual(pathname: string): RouteVisual {
  if (/^\/reader\//.test(pathname)) {
    return { index: '04', route: 'reader', scene: 'field', eyebrow: 'PAPER / EVIDENCE SURFACE', title: '论文精读', register: 'READ—PLANE' }
  }
  if (/^\/code\//.test(pathname)) {
    return { index: '05', route: 'code', scene: 'process', eyebrow: 'SOURCE / IMPLEMENTATION MAP', title: '源码分析', register: 'CODE—TRACE' }
  }
  if (/^\/workspace\/\d+\/research/.test(pathname)) {
    return { index: '06', route: 'research', scene: 'field', eyebrow: 'RELATION / RESEARCH MODEL', title: '深度研究', register: 'GRAPH—FIELD' }
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
  if (pathname === '/settings') {
    return { index: '03', route: 'settings', scene: 'process', eyebrow: 'PROTOCOL / SYSTEM CONTROL', title: '系统设置', register: 'CONTROL—AXIS' }
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

/**
 * 指针位置 → 顶部寄存条上的游标刻度。
 *
 * 上一版把它做成一个跟着光标走的蓝色色块，浮在 z-index 2 的全屏层上 ——
 * 于是它会压在列表行、输入框和标签上面。装饰不能落在内容平面上：这条规则
 * 一旦破例，界面就再没有「哪一层是可读的」这回事。
 *
 * 现在同一个信号被移到顶部寄存条里，变成一格量到列位的游标。它落在框架上，
 * 永远不与内容重叠，而且刚好说明了这套栅格有几栏 —— 从纯装饰变成了读数。
 */
function useGridPointer(rootRef: RefObject<HTMLDivElement>, enabled: boolean) {
  useEffect(() => {
    const root = rootRef.current
    if (!root || !enabled || !window.matchMedia('(pointer: fine)').matches) return

    let frame = 0
    let nextX = window.innerWidth * 0.5

    const commit = () => {
      frame = 0
      const width = window.innerWidth
      const columns = width <= 700 ? 6 : width <= 1000 ? 8 : 12
      const edge = width <= 700 ? 18 : Math.min(64, Math.max(22, width * 0.04))
      const usable = Math.max(1, width - edge * 2)
      const step = usable / columns
      const column = Math.min(columns - 1, Math.max(0, Math.floor((nextX - edge) / step)))

      root.style.setProperty('--er-cursor-x', `${edge + column * step}px`)
      root.style.setProperty('--er-cursor-w', `${step}px`)
      root.style.setProperty('--er-cursor-col', `"${String(column + 1).padStart(2, '0')}"`)
    }

    const onPointerMove = (event: PointerEvent) => {
      nextX = event.clientX
      if (!frame) frame = requestAnimationFrame(commit)
    }

    const onResize = () => {
      if (!frame) frame = requestAnimationFrame(commit)
    }

    commit()
    window.addEventListener('pointermove', onPointerMove, { passive: true })
    window.addEventListener('resize', onResize, { passive: true })
    return () => {
      if (frame) cancelAnimationFrame(frame)
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('resize', onResize)
    }
  }, [enabled, rootRef])
}

interface ContextLinkProps {
  to: string
  active: boolean
  children: ReactNode
}

function ContextLink({ to, active, children }: ContextLinkProps) {
  return (
    <Link className="er-context-link" data-active={active || undefined} aria-current={active ? 'page' : undefined} to={to}>
      {children}
    </Link>
  )
}

export function Layout({ children }: LayoutProps) {
  const location = useLocation()
  const navigate = useNavigate()
  const rootRef = useRef<HTMLDivElement>(null)
  const visual = useMemo(() => routeVisual(location.pathname), [location.pathname])
  const paperId = useCurrentPaperId()
  const workspaceId = useCurrentWorkspaceId()
  const configDirty = useConfigDirty()
  const blocker = useBlocker(({ currentLocation, nextLocation }) => (
    currentLocation.pathname.startsWith('/settings')
    && nextLocation.pathname !== currentLocation.pathname
    && configDirty
  ))
  const motion = useMotionPreference()
  const [showVersion, setShowVersion] = useState(false)
  const [showShortcuts, setShowShortcuts] = useState(false)
  const [showUnsavedConfirm, setShowUnsavedConfirm] = useState(false)

  useGridPointer(rootRef, motion.enabled)
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
      <span className="er-context-divider" key="divider" aria-hidden="true" />,
      <ContextLink key="back" to={backTo} active={false}>{workspaceMatch ? '返回工作区' : '返回论文库'} ↑</ContextLink>,
    )
  } else if (workspaceId) {
    contextItems.push(
      <ContextLink key="detail" to={`/workspace/${workspaceId}`} active={location.pathname === `/workspace/${workspaceId}`}>论文集合</ContextLink>,
      <ContextLink key="research" to={`/workspace/${workspaceId}/research`} active={location.pathname.includes('/research')}>深度研究</ContextLink>,
    )
  }

  return (
    <ThemeProvider>
      <div
        ref={rootRef}
        className="er-experience"
        data-scene={visual.scene}
        data-route={visual.route}
        data-motion={motion.enabled ? 'full' : 'reduced'}
      >
        <a className="er-skip-link" href="#er-main-content">跳到主要内容</a>

        <div className="er-route-wipe" aria-hidden="true"><span /></div>

        <header className="er-masthead">
          <button className="er-brand" type="button" onClick={() => setShowVersion(true)} aria-label="查看 Easy Essay 版本信息">
            <span className="er-brand__mark" aria-hidden="true">ER</span>
            <span><strong>EASY ESSAY</strong><small>RESEARCH INTELLIGENCE</small></span>
          </button>
          <div className="er-route-meta">
            <span>{visual.eyebrow}</span>
            <strong>{visual.title}</strong>
          </div>
          <button
            className="er-motion-control"
            type="button"
            aria-pressed={motion.enabled}
            aria-label={motion.enabled ? '关闭界面动效' : '开启界面动效'}
            onClick={motion.toggle}
          >
            <i aria-hidden="true" />
            <span aria-hidden="true">MOTION</span>
            <strong aria-hidden="true">{motion.enabled ? 'ACTIVE' : 'PAUSED'}</strong>
          </button>
        </header>

        <aside className="er-route-rail" aria-hidden="true">
          <span>{visual.index} / {visual.route.toUpperCase()}</span>
        </aside>

        <div className="er-content">
          <div className="er-registration-strip">
            <i className="er-registration-strip__cursor" aria-hidden="true" />
            <span className="er-registration-strip__index">{visual.index}—{visual.register}</span>
            {contextItems.length > 0 && <nav className="er-context-nav" aria-label="当前内容导航">{contextItems}</nav>}
            <span className="er-registration-strip__column" aria-hidden="true">COL</span>
          </div>
          <main id="er-main-content" className="er-main" tabIndex={-1}>
            <div className="er-page-stage" key={location.pathname}>{children}</div>
          </main>
          <TerminalPanel />
        </div>

        <nav className="er-primary-nav" aria-label="主要导航">
          {primaryNav.map(item => {
            const active = isNavActive(item.path, location.pathname)
            return (
              <Link
                key={item.path}
                className="er-nav-item"
                data-active={active || undefined}
                aria-current={active ? 'page' : undefined}
                to={item.path}
              >
                <span>{item.index}</span>
                <strong>{item.label}</strong>
                <small>{item.register}</small>
              </Link>
            )
          })}
        </nav>

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
