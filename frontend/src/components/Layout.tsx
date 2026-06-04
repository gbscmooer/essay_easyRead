/**
 * Layout 组件
 *
 * 应用整体布局框架。
 *
 * Props:
 *   children: React.ReactNode - 页面内容
 *
 * 布局结构：
 *   - 顶部导航栏（固定）
 *     - 左侧：Logo "论文精读助手"
 *     - 中间：导航链接（首页 / 论文库）
 *     - 右侧：（预留，如设置按钮）
 *   - 内容区（占满剩余高度）
 *
 * 样式：
 *   - 导航栏：bg-gray-900, border-b, border-gray-800
 *   - 内容区：flex-1, overflow-auto
 *   - 深色主题配色
 */

import React from 'react'
import { Link, useLocation } from 'react-router-dom'


interface LayoutProps {
  children: React.ReactNode
}

export function Layout({ children }: LayoutProps) {
  const location = useLocation()

  const navItems = [
    { path: '/', label: '首页' },
    { path: '/library', label: '论文库' },
    { path: '/settings', label: '配置' },
  ]

  return (
    <div className="flex flex-col h-screen bg-gray-950">
      {/* 顶部导航栏 */}
      <nav className="flex items-center justify-between px-6 h-14 bg-gray-900 border-b border-gray-800 shrink-0">
        {/* 左侧 Logo */}
        <Link to="/" className="text-lg font-bold text-gray-100 hover:text-blue-400 transition-colors">
          论文精读助手
        </Link>

        {/* 中间导航链接 */}
        <div className="flex items-center gap-6">
          {navItems.map(item => (
            <Link
              key={item.path}
              to={item.path}
              className={`text-sm transition-colors ${
                location.pathname === item.path
                  ? 'text-blue-400 font-medium'
                  : 'text-gray-400 hover:text-gray-200'
              }`}
            >
              {item.label}
            </Link>
          ))}
        </div>

        {/* 右侧预留 */}
        <div className="w-20" />
      </nav>

      {/* 内容区 */}
      <main className="flex-1 overflow-auto">
        {children}
      </main>
    </div>
  )
}
