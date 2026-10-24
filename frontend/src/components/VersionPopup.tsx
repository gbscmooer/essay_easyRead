import { useEffect, useRef } from 'react'
import '@/styles/overlays.css'

const APP_VERSION = '9.2.0'
const GITHUB_URL = ''

const CURRENT_RELEASE = {
  date: '2026-10-24',
  changes: [
    '新建研究默认使用八阶段流程，问题驱动研究改为实验选项',
    '研究暂停时可剔除文献、追加检索式后继续；完成后可修正问题生成新版本并查看版本链',
    '报告与页面显示检索与分析完整性：数据源失败、资料不足、提取失败与改判原因',
    '逐篇证据提取更稳定，检索未命中与成熟度判定增加程序复核',
    '设置页可填写 OpenAlex API Key；每日免费额度用完时立即提示并跳过该数据源，不再反复重试',
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
            alt="论文精读辅助分析软件几何标记"
          />
          <div className="er-version-identity">
            <span>版本记录</span>
            <h2 id="version-title">论文精读辅助分析软件</h2>
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

        {GITHUB_URL && (
          <a className="er-version-source" href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
            <span>SOURCE / GITHUB</span>
            <strong>查看项目仓库</strong>
            <i aria-hidden="true">↗</i>
          </a>
        )}
      </div>
    </div>
  )
}

export { APP_VERSION }
