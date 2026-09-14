import { useEffect, useRef } from 'react'
import '@/styles/overlays.css'

const APP_VERSION = '7.4.0'
const GITHUB_URL = ''

const CURRENT_RELEASE = {
  date: '2026-09-14',
  changes: [
    '领域探索支持暂停与断点续跑：阶段边界自动落检查点，失败/暂停后可从断点继续，跳过已完成阶段与已提取篇目；进程重启遗留的僵尸运行自愈后同样可续',
    '报告证据闭环：稳定证据 ID + 页码/章节定位索引，agent 可按子问题分页读取、定向补读已有论文，并对终稿逐断言核验',
    'LLM 配置档案支持密钥显示/复制（仅限本机访问）与网关代理传输；相关论文刷新完整透传协议配置',
    '修复中期审查 9 项 P1：报告子问题与召回覆盖维度随研究问题动态生成、重负载操作移出事件循环、并发写库随机锁死、Vite 配置被构建产物遮蔽、探索记录删除误触等',
    '安全加固：出站请求公网地址校验（防 SSRF）、重定向跨主机不携带授权头、本地信任仅限回环地址',
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
