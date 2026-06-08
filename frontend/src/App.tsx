/**
 * 应用路由（数据路由 createBrowserRouter）
 *
 * 采用数据路由以启用 useBlocker（设置页「未保存离开」拦截）。
 * 所有页面挂在 RootLayout 这一布局路由下，经 <Outlet/> 渲染。
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

import { createBrowserRouter, Outlet } from 'react-router-dom'
import { Layout } from '@/components/Layout'
import { ToastContainer } from '@/components/ToastContainer'
import { HomePage } from '@/pages/HomePage'
import { ReaderPage } from '@/pages/ReaderPage'
import { LibraryPage } from '@/pages/LibraryPage'
import { SettingsPage } from '@/pages/SettingsPage'
import { CodePage } from '@/pages/CodePage'
import { WorkspacePage } from '@/pages/WorkspacePage'
import { WorkspaceDetailPage } from '@/pages/WorkspaceDetailPage'
import { ResearchPage } from '@/pages/ResearchPage'
import { NotFoundPage } from '@/pages/NotFoundPage'


function RootLayout() {
  return (
    <Layout>
      <Outlet />
      <ToastContainer />
    </Layout>
  )
}

export const router = createBrowserRouter(
  [
    {
      element: <RootLayout />,
      children: [
        { path: '/', element: <HomePage /> },
        { path: '/reader/:id', element: <ReaderPage /> },
        { path: '/code/:id', element: <CodePage /> },
        { path: '/library', element: <LibraryPage /> },
        { path: '/settings', element: <SettingsPage /> },
        { path: '/workspace', element: <WorkspacePage /> },
        { path: '/workspace/:id', element: <WorkspaceDetailPage /> },
        { path: '/workspace/:id/research', element: <ResearchPage /> },
        { path: '*', element: <NotFoundPage /> },
      ],
    },
  ],
  { future: { v7_relativeSplatPath: true } },
)
