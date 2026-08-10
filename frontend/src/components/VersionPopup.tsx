import { useEffect, useRef } from 'react'
import '@/styles/overlays.css'

const APP_VERSION = '5.0.0'
const GITHUB_URL = 'https://github.com/gbscmooer/essay_easyRead'

const CURRENT_RELEASE = {
  date: '2026-08-10',
  changes: [
    '换上三套真字体：标题用 Archivo 变量字体（真 900 字重 + 真宽度轴），界面用 Inter，坐标与编号用 JetBrains Mono。此前标题写的是 Arial 900，而 Arial 根本没有 900 字重，浏览器一直在合成假粗体',
    '所有坐标、场景码、页码这类注记改用等宽字，读起来是仪器打上去的标注，不再和标题抢注意力',
    '中文与拉丁文的行盒改写成同一套度量，中英混排不再一行松一行紧',
    '动效默认开启，再点一次才关闭，选择会被记住。此前只要系统偏好里勾了「减弱动效」，界面第一眼就是暂停状态',
    '删除两层浮在内容之上的装饰：跟着光标走的蓝色色块、以及压在列表和表单上的色块与轴线。设置页 API Key 输入框中间那条粉色竖线、论文库右半屏发灰的标签，都是它们造成的',
    '光标位置改为顶部寄存条上的一格刻度，落在框架上而不是内容上',
    '论文库、工作区、设置三页重写为对齐的账本式版面：编号 / 标题 / 状态 / 操作四栏在整列上对齐',
    '修复：论文库列表被父级撑破，末尾一行长期被裁掉一半',
    '修复：工作区详情的综述矩阵横向溢出容器，右半边被裁在视口外',
    '修复：深色模式下右上角动效开关与当前导航项是深底深字，几乎看不见',
    '修复：后端不可达时会把用户选的深色主题当场翻回浅色，现在保留本地缓存的设置',
    '图谱配色换成一套统一的印刷油墨分类色，替换原先直接取自工具库默认色板的高饱和紫 / 翠绿 / 亮蓝',
    '首页重排：标题两行不再互相压字，投放区占满整条底边，删掉左下角那块没有内容的空白',
    '页面样式不再靠「第几个 div」认结构：删除全部位置选择器与内联样式嗅探，改为语义类名',
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
