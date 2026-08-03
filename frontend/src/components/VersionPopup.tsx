import { useEffect, useRef } from 'react'
import '@/styles/overlays.css'

const APP_VERSION = '4.0.0'
const GITHUB_URL = 'https://github.com/gbscmooer/essay_easyRead'

const CURRENT_RELEASE = {
  date: '2026-08-03',
  changes: [
    '整套界面重做：纸面、墨色、钴蓝、信号红的硬边构成语言，取消圆角、阴影与毛玻璃层，首页 / 论文库 / 精读 / 代码 / 工作区 / 深度研究 / 设置七个页面按统一的栅格重排',
    '主题系统重构，配色改由 CSS 变量单点注入，深浅两种模式共用同一套语义 token',
    '字号重新分级：功能性文字（按钮、标签、状态、元信息）全部提到 11px 以上，最小可读下限写进 token，不再和装饰性文字混用同一区间',
    '装饰性文字统一降权：坐标、场景码、寄存器编号等纯装饰内容降低字重与不透明度，把注意力交还给可操作的元素',
    '交互层级明确化：可点元素有最小 34px 点击区与统一的悬停 / 按下反馈，全站补齐键盘焦点轮廓；首页上传区升为唯一的实心主行动，移除其右侧不可点却像按钮的色块',
    '统计磁贴降权，不再是页面上最重却不可点击的元素',
    '修复：深色模式下代码页的代码文字与浅色代码底同色，导致内容不可读',
    '修复：多处仅设置前景色而依赖父级背景的样式规则，在结构变化时会出现浅底浅字',
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
