import { useEffect, useRef } from 'react'
import '@/styles/overlays.css'

const APP_VERSION = '9.15.7'
const GITHUB_URL = ''

const CURRENT_RELEASE = {
  date: '2026-10-28',
  changes: [
    '在校园网下，Scopus 检索结果会带回摘要，非 arXiv 的期刊论文不再只有题名；校外照旧，不受影响',
    '修复 Scopus 检索的两个问题：问题原句带括号时整条检索失败；放宽后的检索式按被引排序会排出与题目无关的全库高被引论文（经典论文层因此失效）',
    'Scopus 检索命中不足名额时用放宽的检索式补满：长检索式每条原来只命中 4~6 篇，而本地论文库每条给 45 篇，非 CS 学科的候选几乎被不对题的 CS 论文占满',
    '开放问答补全文时可以选用更多来源：arXiv 之外，还能用 Europe PMC 开放获取全文，以及校园网下的 ScienceDirect 订阅全文；补全文的名额可交给重排模型按与问题的相关度分配（均默认关）',
    '新增 ResearchQA 评测（75 个领域、按综述提炼的细则打分），可按学科大类分层抽题，先跑小子集，再跑全部题目',
    '开放问答新增几项可选增强（默认关）：在线 Scopus 与本地论文库一起找论文、草稿点名的期刊论文可到 Scopus 按题名查找；选材阶段可先走官方全文接口；起草前可先读召回论文中最相关的若干篇',
    '开放问答新增“保留草稿”定稿方式（默认关）：草稿原文由程序保留，模型只为每句挑证据、改正与证据矛盾的句子，不再整篇改写；输出用量大幅减少',
    '开放问答可单独指定起草用的模型（其余步骤仍用当前模型）：在模型不熟悉的学科，更强的起草模型配合原文阅读能写得更全',
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
