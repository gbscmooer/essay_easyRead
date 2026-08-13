import { useEffect, useRef } from 'react'
import '@/styles/overlays.css'

const APP_VERSION = '5.0.1'
const GITHUB_URL = 'https://github.com/gbscmooer/essay_easyRead'

const CURRENT_RELEASE = {
  date: '2026-08-13',
  changes: [
    '升级 PDF 阅读器与前端运行依赖，清除已知安全漏洞，并保持画布、文字层、标注层与分页缩放完整可用',
    '本地终端加入可信同源校验和 30 秒一次性会话票据，同时修复连接后立即断开的 WebSocket 收发问题',
    '默认 Docker 服务只监听本机地址，日志流、终端和打开编辑器等本地能力不再接受外部来源',
    '后端启用每客户端每分钟 120 次请求限流，错误响应统一为可读的 detail 与 code 字段',
    '设置页现在追踪模型、提示词、研究参数和界面配置的全部未保存修改；离开前会确认，保存失败会明确提示',
    '遵循系统的减弱动态效果偏好；用户仍可用顶部开关显式覆盖，并保留自己的选择',
    '修复中等宽度下图谱被问答面板挤压的问题，图谱与跨论文问答改为可连续滚动的上下布局',
    '统一“已索引 · 待解析”状态与实际字体名称，修复账本行内按钮的键盘误导航和图谱筛选文字对比度',
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
