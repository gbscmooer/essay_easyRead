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
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
  type RefObject,
} from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { TerminalPanel } from './TerminalPanel'
import { ThemeProvider } from './ThemeProvider'
import { VersionPopup } from './VersionPopup'
import { ShortcutHelp } from './ShortcutHelp'
import { ConfirmDialog } from '@/ds-entry'
import { hasUnsavedConfig } from '@/hooks/useConfig'
import { setHelpCallback, useGlobalShortcuts } from '@/hooks/useKeyboardShortcuts'

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

function useMotionPreference() {
  const query = '(prefers-reduced-motion: reduce)'
  const [systemReduced, setSystemReduced] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia(query).matches,
  )
  const [override, setOverride] = useState<boolean | null>(null)

  useEffect(() => {
    const media = window.matchMedia(query)
    const update = () => setSystemReduced(media.matches)
    update()
    media.addEventListener?.('change', update)
    return () => media.removeEventListener?.('change', update)
  }, [])

  useEffect(() => {
    const root = document.documentElement
    if (override === null) {
      root.removeAttribute('data-er-motion-override')
    } else {
      root.setAttribute('data-er-motion-override', override ? 'full' : 'reduced')
    }

    return () => root.removeAttribute('data-er-motion-override')
  }, [override])

  const enabled = override ?? !systemReduced
  const toggle = useCallback(() => setOverride(current => !(current ?? !systemReduced)), [systemReduced])
  return { enabled, source: override === null ? 'system' : 'user', toggle }
}

/** Writes a decorative pointer aperture directly to CSS variables on one RAF. */
function useGridPointer(rootRef: RefObject<HTMLDivElement>, enabled: boolean) {
  useEffect(() => {
    const root = rootRef.current
    const finePointer = window.matchMedia('(pointer: fine)')
    if (!root || !enabled || !finePointer.matches) return

    let frame = 0
    let nextX = window.innerWidth * 0.72
    let nextY = window.innerHeight * 0.32

    const commit = () => {
      frame = 0
      const width = window.innerWidth
      const height = window.innerHeight
      const columns = width <= 700 ? 6 : width <= 1000 ? 8 : 12
      const edge = width <= 700 ? 18 : Math.min(64, Math.max(22, width * 0.04))
      const usableWidth = Math.max(1, width - edge * 2)
      const xStep = usableWidth / columns
      const yEdge = Math.min(36, Math.max(20, height * 0.03))
      const usableHeight = Math.max(1, height - yEdge * 2)
      const yStep = usableHeight / 8
      const x = edge + Math.round((nextX - edge) / xStep) * xStep
      const y = yEdge + Math.round((nextY - yEdge) / yStep) * yStep

      root.style.setProperty('--er-pointer-x', `${Math.min(width - edge, Math.max(edge, x))}px`)
      root.style.setProperty('--er-pointer-y', `${Math.min(height - yEdge, Math.max(yEdge, y))}px`)
    }

    const onPointerMove = (event: PointerEvent) => {
      nextX = event.clientX
      nextY = event.clientY
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
  onClick?: (event: ReactMouseEvent) => void
  children: ReactNode
}

function ContextLink({ to, active, onClick, children }: ContextLinkProps) {
  return (
    <Link className="er-context-link" data-active={active || undefined} aria-current={active ? 'page' : undefined} to={to} onClick={onClick}>
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
  const motion = useMotionPreference()
  const [showVersion, setShowVersion] = useState(false)
  const [showShortcuts, setShowShortcuts] = useState(false)
  const [showUnsavedConfirm, setShowUnsavedConfirm] = useState(false)
  const pendingNavRef = useRef<string | null>(null)
  const pendingNavStateRef = useRef<unknown>(null)

  useGridPointer(rootRef, motion.enabled)
  useGlobalShortcuts(navigate)

  const toggleShortcuts = useCallback(() => setShowShortcuts(value => !value), [])
  useEffect(() => {
    setHelpCallback(toggleShortcuts)
    return () => setHelpCallback(null)
  }, [toggleShortcuts])

  const handleNavGuard = useCallback((target: string, event: ReactMouseEvent, state?: unknown) => {
    if (!location.pathname.startsWith('/settings') || !hasUnsavedConfig()) return
    event.preventDefault()
    pendingNavRef.current = target
    pendingNavStateRef.current = state ?? null
    setShowUnsavedConfirm(true)
  }, [location.pathname])

  const contextItems: ReactNode[] = []
  if (paperId) {
    const fromPath = (location.state as { from?: string } | null)?.from
    const workspaceMatch = fromPath?.match(/^\/workspace\/(\d+)/)
    const backTo = workspaceMatch ? `/workspace/${workspaceMatch[1]}` : '/library'
    contextItems.push(
      <ContextLink key="reader" to={`/reader/${paperId}`} active={location.pathname.startsWith('/reader')} onClick={event => handleNavGuard(`/reader/${paperId}`, event, location.state)}>论文分析</ContextLink>,
      <ContextLink key="code" to={`/code/${paperId}`} active={location.pathname.startsWith('/code')} onClick={event => handleNavGuard(`/code/${paperId}`, event, location.state)}>源码分析</ContextLink>,
      <span className="er-context-divider" key="divider" aria-hidden="true" />,
      <ContextLink key="back" to={backTo} active={false} onClick={event => handleNavGuard(backTo, event)}>{workspaceMatch ? '返回工作区' : '返回论文库'} ↑</ContextLink>,
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
        data-motion-source={motion.source}
      >
        <a className="er-skip-link" href="#er-main-content">跳到主要内容</a>

        <div className="er-route-wipe" aria-hidden="true"><span /></div>
        <div className="er-pointer-aperture" aria-hidden="true" />
        <div className="er-spatial" aria-hidden="true">
          <i className="er-spatial__mass" />
          <i className="er-spatial__bar" />
          <i className="er-spatial__axis" />
          <span>{visual.register}</span>
        </div>

        <header className="er-masthead">
          <button className="er-brand" type="button" onClick={() => setShowVersion(true)} aria-label="查看 Easy Essay 版本信息">
            <span className="er-brand__mark" aria-hidden="true">ER</span>
            <span><strong>EASY ESSAY</strong><small>RESEARCH INTELLIGENCE</small></span>
          </button>
          <div className="er-route-meta">
            <span>{visual.eyebrow}</span>
            <strong>{visual.title}</strong>
          </div>
          <button className="er-motion-control" type="button" aria-pressed={motion.enabled} onClick={motion.toggle}>
            <i aria-hidden="true" />
            <span>MOTION</span>
            <strong>{motion.enabled ? 'ACTIVE' : 'PAUSED'}</strong>
          </button>
        </header>

        <aside className="er-route-rail" aria-hidden="true">{visual.index} / {visual.route.toUpperCase()}</aside>

        <div className="er-content">
          <div className="er-registration-strip">
            <span>{visual.index}—{visual.register}</span>
            {contextItems.length > 0 && <nav className="er-context-nav" aria-label="当前内容导航">{contextItems}</nav>}
            <span>CN / 31.2304°N</span>
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
                onClick={event => handleNavGuard(item.path, event)}
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
