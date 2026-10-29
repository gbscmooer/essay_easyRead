import { useEffect, useRef } from 'react'
import '@/styles/overlays.css'

const APP_VERSION = '9.17.0'
const GITHUB_URL = ''

const CURRENT_RELEASE = {
  date: '2026-10-29',
  changes: [
    '开放问答的“原文阅读”（定稿前按问题本身从已收集原文中再取 16 句）恢复为默认关闭：在 50 道跨学科题目上复核，开与不开答得一样全，开了答案更长、用量更多。整套流程在这 50 题上比直接问模型答得更全（细则覆盖 0.78 对 0.71）',
    '召回修复：校园网下 Scopus 检索结果不再因一次限流就整批不带摘要（原来约四分之一的论文没有摘要，进不了证据、答案引用不了）；召回后仍缺摘要的论文先按 DOI 回 Scopus 批量补，再查 Europe PMC；修复取论文参考文献列表的接口（原来一直失败），并新增可选的参考文献回溯（从召回到的综述的参考文献里补对题文献）',
    '开放问答默认同时独立起草两份答案再合并成一份（保留各份不重复的要点和对题意的不同理解），之后照常查证定稿：在 50 道跨学科题目上答得更全（细则覆盖 0.772 → 0.797），计算机方向综合评分持平；一份起草出错时用另一份，不再整份失败。每题多用约 2 分钟',
    '在校园网下，Scopus 检索结果带回摘要；修复问题原句带括号时检索失败、放宽检索式按被引排序排出与题目无关的高被引论文、原式命中不足时不补满等问题，非 CS 学科的召回不再被不对题的计算机论文占满',
    '开放问答新增几项可选增强（默认关）：补全文可用 Europe PMC 开放获取与校园网下的 ScienceDirect、在线 Scopus 与本地论文库一起找论文、可单独指定起草模型、“保留草稿”定稿方式',
    '新增 ResearchQA 评测（75 个领域、按综述提炼的细则打分），按学科大类分层抽题，先跑子集再跑全部题目',
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
