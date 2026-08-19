import { useEffect, useRef } from 'react'
import '@/styles/overlays.css'

const APP_VERSION = '5.4.0'
const GITHUB_URL = 'https://github.com/gbscmooer/essay_easyRead'

const CURRENT_RELEASE = {
  date: '2026-08-19',
  changes: [
    '笔记草稿不再因切换 Tab 丢失：按论文分别保存，回到笔记页继续写；⌘/Ctrl + Enter 可直接提交',
    '删除笔记改为需要确认，与删除论文、标注保持一致',
    '阅读页侧边栏的搜索改为查全库，不再只在已加载的最近 30 篇里过滤，并说明当前列出的范围',
    '工作区「从论文库添加」新增搜索框并显示候选数量，论文超过 100 篇后也能找到并加入',
    '跨论文问答保留最近 10 轮问答，新问题不再冲掉上一轮的答案与引用；支持复制答案、中止检索',
    '论文按标题搜不到时可用原始文件名匹配（尚未索引的论文也能找到）',
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
