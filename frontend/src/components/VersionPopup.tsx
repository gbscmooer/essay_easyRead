import { useEffect, useRef } from 'react'
import '@/styles/overlays.css'

const APP_VERSION = '5.1.0'
const GITHUB_URL = 'https://github.com/gbscmooer/essay_easyRead'

const CURRENT_RELEASE = {
  date: '2026-08-19',
  changes: [
    '阅读页记住工作状态：分析面板宽度与开合、PDF 缩放跨论文保留，每篇论文再次打开时回到上次读到的页码',
    'PDF 支持键盘翻页与缩放：左右方向键 / PgUp / PgDn 翻页，Home 与 End 直达首末页，+ − 缩放、0 复位',
    '新增「适宽 / 适页」两种自适应缩放，拖动分割条或折叠面板后会自动重算比例',
    'PDF 加载失败不再是一片空白：给出失败原因、重新加载与在新标签页打开两个入口',
    '新增标注索引面板：按页码列出全部标注，点击回到原处，可直接删除整条标注',
    '修复长论文向下翻会翻到空白页：页面可见性观察器现在会纳入虚拟化后续渲染的页',
    '修复输入页码或从大纲跳转到远处章节时无反应：目标页会先渲染再滚动',
    '实时进度连接中断不再等同于分析失败：提供「重新连接」，后台分析继续，不必重跑模型',
    '分割条可用键盘调节（方向键 / PageUp / PageDown / Home 复位）并向读屏播报当前宽度',
    '快捷键帮助补齐阅读页按键，并修正 Tab 切换的数字范围',
  ],
}

export function VersionPopup({ onClose }: { onClose: () => void }) {
  const popupRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleClick = (event: MouseEvent) => {
      if (popupRef.current && !popupRef.current.contains(event.target as Node)) onClose()
    }
    const handleEsc = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('mousedown', handleClick)
    document.addEventListener('keydown', handleEsc)
    return () => {
      document.removeEventListener('mousedown', handleClick)
      document.removeEventListener('keydown', handleEsc)
    }
  }, [onClose])

  useEffect(() => {
    const element = popupRef.current
    if (!element) return

    const handleTab = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return
      const focusable = element.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      )
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    element.querySelector<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    )?.focus()
    document.addEventListener('keydown', handleTab)
    return () => document.removeEventListener('keydown', handleTab)
  }, [])

  return (
    <div
      className="er-version-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="version-title"
      aria-describedby="version-release-label"
    >
      <div
        ref={popupRef}
        className="er-version-plane"
      >
        <header className="er-version-header">
          <img
            className="er-version-mark"
            src="/essay-register.svg"
            alt="Easy Essay 几何标记"
          />
          <div className="er-version-identity">
            <span>ER / RELEASE REGISTER</span>
            <h2 id="version-title">Easy Essay</h2>
          </div>
          <span className="er-version-number">V{APP_VERSION}</span>
          <button className="er-overlay-close" type="button" onClick={onClose} aria-label="关闭版本信息">
            <span aria-hidden="true">×</span>
          </button>
        </header>

        <div className="er-version-axis" aria-hidden="true">
          <span />
          <i />
        </div>

        <div className="er-version-body">
          <div className="er-version-release-heading">
            <div>
              <span id="version-release-label">CURRENT / RELEASE</span>
              <strong>版本 {APP_VERSION}</strong>
            </div>
            <time dateTime={CURRENT_RELEASE.date}>{CURRENT_RELEASE.date}</time>
          </div>

          <ol className="er-version-ledger">
            {CURRENT_RELEASE.changes.map((change, index) => (
              <li key={change}>
                <span aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
                <p>{change}</p>
              </li>
            ))}
          </ol>
        </div>

        <a className="er-version-source" href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
          <span>SOURCE / GITHUB</span>
          <strong>查看项目仓库</strong>
          <i aria-hidden="true">↗</i>
        </a>
      </div>
    </div>
  )
}

export { APP_VERSION }
