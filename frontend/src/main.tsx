/**
 * 应用入口
 *
 * 挂载 React 应用到 DOM。
 * 引入全局样式。
 * 配置 React Router。
 */

// 实现要点：
// - ReactDOM.createRoot(document.getElementById('root'))
// - BrowserRouter 包裹 App 组件
// - 引入 globals.css

import React from 'react'
import ReactDOM from 'react-dom/client'
import { RouterProvider } from 'react-router-dom'
import { ErrorBoundary } from './components/ErrorBoundary'
import { router } from './App'
// 样式分层，顺序即优先级：
// globals(token 与基础层) → typography(字族与字号阶梯) → experience(路由外壳)
//   → surfaces(页面构件) → pages(各页面覆写) → controls(控件系统)
// typography 必须在 globals 之后：它要改写 Tailwind preflight 落在 html/body
// 上的字族。controls 必须最后，.er-btn 才能压过 pages/experience 里的按钮兜底规则。
import './styles/globals.css'
import './styles/typography.css'
import './styles/experience.css'
import './styles/surfaces.css'
import './styles/pages.css'
import './styles/controls.css'
// v10「朱批」版面层：外壳目录栏、共用版面构件、首页（必须最后，压过旧层外壳兜底）
import './styles/marginalia.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <RouterProvider router={router} />
    </ErrorBoundary>
  </React.StrictMode>,
)
