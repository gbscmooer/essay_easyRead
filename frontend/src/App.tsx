/**
 * 应用根组件
 *
 * 路由配置：
 *   /           → HomePage（首页，上传入口 + 论文库概览）
 *   /reader/:id → ReaderPage（论文精读页，PDF预览 + 解析结果）
 *   /library    → LibraryPage（论文库，筛选和搜索）
 *
 * 布局：
 *   - 深色主题，bg-gray-950
 *   - 顶部导航栏（Logo + 导航链接）
 *   - 页面内容区
 *
 * 所有页面路由均使用 React.lazy() 实现代码分割，
 * 首屏只加载当前路由对应的 chunk。
 */

import React, { Suspense } from 'react'
import { Routes, Route } from 'react-router-dom'
import { Layout } from '@/components/Layout'

const HomePage = React.lazy(() => import('@/pages/HomePage'))
const ReaderPage = React.lazy(() => import('@/pages/ReaderPage'))
const CodePage = React.lazy(() => import('@/pages/CodePage'))
const LibraryPage = React.lazy(() => import('@/pages/LibraryPage'))
const SettingsPage = React.lazy(() => import('@/pages/SettingsPage'))
const WorkspacePage = React.lazy(() => import('@/pages/WorkspacePage'))
const WorkspaceDetailPage = React.lazy(() => import('@/pages/WorkspaceDetailPage'))
const ResearchPage = React.lazy(() => import('@/pages/ResearchPage'))

function LoadingFallback() {
  return (
    <div className="flex items-center justify-center h-64">
      <div
        className="w-8 h-8 border-2 rounded-full animate-spin"
        style={{ borderColor: 'var(--border)', borderTopColor: 'var(--accent)' }}
      />
    </div>
  )
}

export default function App() {
  return (
    <Layout>
      <Suspense fallback={<LoadingFallback />}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/reader/:id" element={<ReaderPage />} />
          <Route path="/code/:id" element={<CodePage />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/workspace" element={<WorkspacePage />} />
          <Route path="/workspace/:id" element={<WorkspaceDetailPage />} />
          <Route path="/workspace/:id/research" element={<ResearchPage />} />
        </Routes>
      </Suspense>
    </Layout>
  )
}
