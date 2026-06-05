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
import { BrowserRouter } from 'react-router-dom'
import { ErrorBoundary } from './components/ErrorBoundary'
import App from './App'
import './styles/globals.css'


ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <BrowserRouter future={{ v7_relativeSplatPath: true }}>
        <App />
      </BrowserRouter>
    </ErrorBoundary>
  </React.StrictMode>,
)
