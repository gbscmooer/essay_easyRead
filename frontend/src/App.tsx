/**
 * 应用路由（数据路由 createBrowserRouter）
 *
 * 采用数据路由以启用 useBlocker（设置页「未保存离开」拦截）。
 * 所有页面挂在 RootLayout 这一布局路由下，经 <Outlet/> 渲染。
 *
 * 所有页面路由均使用 React.lazy() 实现代码分割，
 * 首屏只加载当前路由对应的 chunk。
 *
 * 路由表：
 *   /                       → HomePage（首页，上传入口 + 论文库概览）
 *   /reader/:id             → ReaderPage（论文精读页）
 *   /code/:id               → CodePage（代码分析）
 *   /library                → LibraryPage（论文库）
 *   /settings               → SettingsPage（设置）
 *   /workspace[/:id]        → 工作区列表 / 详情
 *   /workspace/:id/research → ResearchPage（深度研究）
 *   *                       → NotFoundPage（404）
 */

import React, { Suspense } from 'react'
import { createBrowserRouter, Outlet } from 'react-router-dom'
import { Layout } from '@/components/Layout'
import { ToastContainer } from '@/components/ToastContainer'
import { PageErrorBoundary } from '@/components/PageErrorBoundary'

const HomePage = React.lazy(() => import('@/pages/HomePage'))
const ReaderPage = React.lazy(() => import('@/pages/ReaderPage'))
const CodePage = React.lazy(() => import('@/pages/CodePage'))
const LibraryPage = React.lazy(() => import('@/pages/LibraryPage'))
const SettingsPage = React.lazy(() => import('@/pages/SettingsPage'))
const WorkspacePage = React.lazy(() => import('@/pages/WorkspacePage'))
const WorkspaceDetailPage = React.lazy(() => import('@/pages/WorkspaceDetailPage'))
const ResearchPage = React.lazy(() => import('@/pages/ResearchPage'))
const NotFoundPage = React.lazy(() => import('@/pages/NotFoundPage'))

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

function RootLayout() {
  return (
    <Layout>
      <Suspense fallback={<LoadingFallback />}>
        <Outlet />
      </Suspense>
      <ToastContainer />
    </Layout>
  )
}

export const router = createBrowserRouter(
  [
    {
      element: <RootLayout />,
      children: [
        { path: '/', element: <PageErrorBoundary pageName="首页"><HomePage /></PageErrorBoundary> },
        { path: '/reader/:id', element: <PageErrorBoundary pageName="论文精读"><ReaderPage /></PageErrorBoundary> },
        { path: '/code/:id', element: <PageErrorBoundary pageName="代码分析"><CodePage /></PageErrorBoundary> },
        { path: '/library', element: <PageErrorBoundary pageName="论文库"><LibraryPage /></PageErrorBoundary> },
        { path: '/settings', element: <PageErrorBoundary pageName="设置"><SettingsPage /></PageErrorBoundary> },
        { path: '/workspace', element: <PageErrorBoundary pageName="工作区"><WorkspacePage /></PageErrorBoundary> },
        { path: '/workspace/:id', element: <PageErrorBoundary pageName="工作区详情"><WorkspaceDetailPage /></PageErrorBoundary> },
        { path: '/workspace/:id/research', element: <PageErrorBoundary pageName="深度研究"><ResearchPage /></PageErrorBoundary> },
        { path: '*', element: <NotFoundPage /> },
      ],
    },
  ],
  { future: { v7_relativeSplatPath: true } },
)
