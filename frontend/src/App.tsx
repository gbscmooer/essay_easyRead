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
 *   /explore                → ExplorePage（领域探索：问句驱动免读领域认知，见 GOAL.md）
 *   /workspace[/:id]        → 工作区列表 / 详情
 *   /workspace/:id/research → ResearchPage（深度研究）
 *   *                       → NotFoundPage（404）
 */

import React, { Suspense } from 'react'
import { createBrowserRouter, Outlet, useLocation } from 'react-router-dom'
import { Layout } from '@/components/Layout'
import { ToastContainer } from '@/components/ToastContainer'
import { PageErrorBoundary } from '@/components/PageErrorBoundary'
import { LoadingSpinner } from '@/ds-entry'

const HomePage = React.lazy(() => import('@/pages/HomePage'))
const ReaderPage = React.lazy(() => import('@/pages/ReaderPage'))
const CodePage = React.lazy(() => import('@/pages/CodePage'))
const LibraryPage = React.lazy(() => import('@/pages/LibraryPage'))
const SettingsPage = React.lazy(() => import('@/pages/SettingsPage'))
const ExplorePage = React.lazy(() => import('@/pages/ExplorePage'))
const WorkspacePage = React.lazy(() => import('@/pages/WorkspacePage'))
const WorkspaceDetailPage = React.lazy(() => import('@/pages/WorkspaceDetailPage'))
const ResearchPage = React.lazy(() => import('@/pages/ResearchPage'))
const NotFoundPage = React.lazy(() => import('@/pages/NotFoundPage'))

function LoadingFallback() {
  return (
    <div className="er-route-loading" role="status" aria-label="页面加载中">
      <LoadingSpinner size="lg" />
      <span>REGISTERING ROUTE</span>
    </div>
  )
}

/** 路由切换时按路径 key 重新挂载并淡入（纯透明度，不改盒模型，h-full 透传保证满高页面正常）。 */
function RouteTransition() {
  const location = useLocation()
  return (
    <div key={location.pathname} className="h-full">
      <Outlet />
    </div>
  )
}

function RootLayout() {
  return (
    <Layout>
      <Suspense fallback={<LoadingFallback />}>
        <RouteTransition />
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
        { path: '/explore', element: <PageErrorBoundary pageName="领域探索"><ExplorePage /></PageErrorBoundary> },
        { path: '/workspace', element: <PageErrorBoundary pageName="工作区"><WorkspacePage /></PageErrorBoundary> },
        { path: '/workspace/:id', element: <PageErrorBoundary pageName="工作区详情"><WorkspaceDetailPage /></PageErrorBoundary> },
        { path: '/workspace/:id/research', element: <PageErrorBoundary pageName="深度研究"><ResearchPage /></PageErrorBoundary> },
        { path: '*', element: <NotFoundPage /> },
      ],
    },
  ],
  { future: { v7_relativeSplatPath: true } },
)
