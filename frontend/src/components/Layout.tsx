/**
 * Application frame — route-aware shell（v10「朱批」：左侧目录栏 + 版面区）.
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
  useRef,
  type ReactNode,
} from 'react'
import { Link, useBlocker, useLocation, useNavigate } from 'react-router-dom'
import { TerminalPanel } from './TerminalPanel'
import { ThemeProvider } from './ThemeProvider'
import { APP_VERSION, VersionPopup } from './VersionPopup'
import { ShortcutHelp } from './ShortcutHelp'
import { ConfirmDialog } from '@/ds-entry'
import { useConfigDirty, useUIConfig, saveTheme } from '@/hooks/useConfig'
import { setHelpCallback, useGlobalShortcuts } from '@/hooks/useKeyboardShortcuts'
import { useThemeMode } from '@/theme/useTheme'
import { toast } from '@/hooks/useToast'
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
      className="mg-toc__sub-item"
      data-active={active || undefined}
      aria-current={active ? 'page' : undefined}
      to={to}
    >
      {children}
    </Link>
  )
}

/** 目录栏底部的日期注记（刊物的出版日期行）。 */
function folioDate(): string {
  const now = new Date()
  const week = ['日', '一', '二', '三', '四', '五', '六'][now.getDay()]
  return `${now.getFullYear()}.${String(now.getMonth() + 1).padStart(2, '0')}.${String(now.getDate()).padStart(2, '0')} 周${week}`
}

export function Layout({ children }: LayoutProps) {
  const location = useLocation()
  const navigate = useNavigate()
  const visual = useMemo(() => routeVisual(location.pathname), [location.pathname])
  const paperId = useCurrentPaperId()
  const workspaceId = useCurrentWorkspaceId()
  const configDirty = useConfigDirty()
  const uiConfig = useUIConfig()
  const isDark = useThemeMode() === 'dark'
  const preferredThemes = useRef({ light: 'light', dark: 'dark' })
  if (uiConfig.theme) preferredThemes.current[isDark ? 'dark' : 'light'] = uiConfig.theme

  const toggleTheme = useCallback(() => {
    const next = preferredThemes.current[isDark ? 'light' : 'dark']
    void saveTheme(next).catch(() => toast({ type: 'error', message: '主题保存失败，请重试' }))
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
  let contextSection: string | null = null   // 子页面挂在哪个目录项下面
  if (paperId) {
    contextSection = '/library'
    const fromPath = (location.state as { from?: string } | null)?.from
    const workspaceMatch = fromPath?.match(/^\/workspace\/(\d+)/)
    const backTo = workspaceMatch ? `/workspace/${workspaceMatch[1]}` : '/library'
    contextItems.push(
      <ContextLink key="reader" to={`/reader/${paperId}`} active={location.pathname.startsWith('/reader')}>论文分析</ContextLink>,
      <ContextLink key="code" to={`/code/${paperId}`} active={location.pathname.startsWith('/code')}>源码分析</ContextLink>,
      <ContextLink key="back" to={backTo} active={false}>{workspaceMatch ? '返回工作区' : '返回论文库'}</ContextLink>,
    )
  } else if (workspaceId) {
    contextSection = '/workspace'
    contextItems.push(
      <ContextLink key="detail" to={`/workspace/${workspaceId}`} active={location.pathname === `/workspace/${workspaceId}`}>论文集合</ContextLink>,
      <ContextLink key="research" to={`/explore?workspace_id=${workspaceId}`} active={false}>发起研究</ContextLink>,
      <ContextLink key="graph" to={`/workspace/${workspaceId}/graph`} active={location.pathname.includes('/graph')}>工作区图谱</ContextLink>,
    )
  }

  return (
    <ThemeProvider>
      <div
        className="er-experience mg-shell"
        data-scene={visual.scene}
        data-route={visual.route}
        data-motion={motion.enabled ? 'full' : 'reduced'}
      >
        <a className="mg-skip" href="#er-main-content">跳到主要内容</a>

        {/* 目录栏：像书的目录一样编号；打开论文或工作区时，子页面缩进挂在所属目录项下面 */}
        <aside className="mg-rail" aria-label="主要导航">
          <Link to="/" className="mg-brand" aria-label="论文精读首页">
            <span className="mg-seal" aria-hidden="true">论</span>
            <span className="mg-brand__text">
              <span className="mg-brand__name">论文精读</span>
              <span className="mg-brand__sub">研究者的阅读台</span>
            </span>
          </Link>

          <nav className="mg-toc" aria-label="主要导航">
            <span className="mg-toc__head">目录</span>
            {primaryNav.map((item, i) => {
              const active = isNavActive(item.path, location.pathname)
              const sub = contextSection === item.path && contextItems.length > 0
              return (
                <div className="mg-toc__group" key={item.path} data-active={active || undefined}>
                  <Link
                    to={item.path}
                    className="mg-toc__item"
                    data-active={active || undefined}
                    aria-current={active && !sub ? 'page' : undefined}
                  >
                    <span className="mg-toc__no">{String(i + 1).padStart(2, '0')}</span>
                    <span className="mg-toc__label">{item.label}</span>
                  </Link>
                  {sub && (
                    <div className="mg-toc__sub" aria-label="当前内容导航">
                      {contextItems}
                    </div>
                  )}
                </div>
              )
            })}
          </nav>

          <div className="mg-rail__foot">
            <div className="mg-rail__tools">
              <button
                type="button"
                onClick={toggleTheme}
                className="mg-tool"
                title={isDark ? '切换至宣纸（明亮）' : '切换至墨（深色）'}
                aria-label="切换主题颜色"
              >
                <span className="mg-tool__glyph" aria-hidden="true">{isDark ? '纸' : '墨'}</span>
                <span className="mg-tool__label">{isDark ? '宣纸' : '墨色'}</span>
              </button>
              <button
                type="button"
                onClick={toggleShortcuts}
                className="mg-tool"
                title="键盘快捷键 (?)"
                aria-label="快捷键帮助"
              >
                <span className="mg-tool__glyph" aria-hidden="true">?</span>
                <span className="mg-tool__label">快捷键</span>
              </button>
            </div>
            <div className="mg-folio">
              <span>{folioDate()}</span>
              <button
                type="button"
                onClick={() => setShowVersion(true)}
                className="mg-folio__version"
                title="查看版本与更新日志"
                aria-label="查看论文精读辅助分析软件版本信息"
              >
                v{APP_VERSION}
              </button>
            </div>
          </div>
        </aside>

        {/* 主内容区域 */}
        <div className="mg-stage">
          <main id="er-main-content" className="mg-main" tabIndex={-1}>
            <div className="mg-scroll" key={location.pathname}>
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
