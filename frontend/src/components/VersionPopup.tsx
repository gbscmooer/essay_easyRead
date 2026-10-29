import { useEffect, useRef } from 'react'
import '@/styles/overlays.css'

const APP_VERSION = '10.0.1'
const GITHUB_URL = ''

const CURRENT_RELEASE = {
  date: '2026-10-29',
  changes: [
    '修复：精读、源码、图谱页上鼠标移到左侧目录栏时没有展开到正常宽度，目录文字被挤成一字一行',
    '界面整体改版「朱批」：宣纸底、墨色字，唯一的强调色是朱砂（取自学者在书页上做批注的朱笔）；页名与标题用思源宋体 / Newsreader，界面文字用 Instrument Sans，编号与日期用等宽字。去掉了圆角卡片、投影、胶囊标签和彩色图标块，改用细线分隔的账页式列表与方点状态注记',
    '导航改为左侧“目录”栏：各页带编号；打开论文或工作区时，论文分析 / 源码分析 / 图谱等子页面缩进挂在所属目录项下面。精读、源码、图谱这类工作界面上目录栏自动收成编号细栏，鼠标移上去展开；窄屏时收成顶栏',
    '首页改成刊物头版：日期行与双线刊头、投稿框（上传 PDF）、馆藏数字、最近解析的编号账页与去处索引；404 页改为“勘误”页并给出可去的地方',
    '深色主题“墨”与明亮主题“宣纸”同一套配色逻辑；图表分类色、代码区、对话框与提示同步换成纸面配色，对话框去掉毛玻璃；精读页的分析标签改为单行、当前页底边一道朱笔',
    '功能与操作方式不变：所有页面、按钮、快捷键、主题与字体设置照常可用',
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
            alt="论文精读辅助分析软件印章标记"
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
