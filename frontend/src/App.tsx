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
 */

import { Routes, Route } from 'react-router-dom'
import { Layout } from '@/components/Layout'
import { HomePage } from '@/pages/HomePage'
import { ReaderPage } from '@/pages/ReaderPage'
import { LibraryPage } from '@/pages/LibraryPage'
import { SettingsPage } from '@/pages/SettingsPage'
import { CodePage } from '@/pages/CodePage'
import { WorkspacePage } from '@/pages/WorkspacePage'
import { WorkspaceDetailPage } from '@/pages/WorkspaceDetailPage'


export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/reader/:id" element={<ReaderPage />} />
        <Route path="/code/:id" element={<CodePage />} />
        <Route path="/library" element={<LibraryPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/workspace" element={<WorkspacePage />} />
        <Route path="/workspace/:id" element={<WorkspaceDetailPage />} />
      </Routes>
    </Layout>
  )
}
