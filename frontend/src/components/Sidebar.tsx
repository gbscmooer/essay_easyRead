/**
 * Sidebar 组件
 *
 * 论文历史列表侧边栏，显示在 ReaderPage 左侧。
 *
 * Props:
 *   papers: Paper[] - 论文列表
 *   activeId: number | null - 当前选中的论文 ID
 *   onSelect: (id: number) => void - 选中论文的回调
 *   onDelete: (id: number) => void - 删除论文的回调
 *
 * 功能：
 *   - 显示论文列表，每项显示标题/文件名、会议、年份
 *   - 高亮当前选中的论文
 *   - 状态标识：done(绿点), analyzing(黄点), failed(红点)
 *   - 点击切换论文
 *   - 右键/按钮删除论文
 *   - 顶部搜索框（本地过滤）
 *   - 底部上传按钮
 *
 * 样式：
 *   - 宽度固定 w-64
 *   - bg-gray-900, border-r, border-gray-800
 *   - 列表项 hover:bg-gray-800, active:bg-gray-700
 */

import { useState, useMemo } from 'react'
import type { Paper } from '@/types'


interface SidebarProps {
  papers: Paper[]
  activeId: number | null
  onSelect: (id: number) => void
  onDelete: (id: number) => void
}

function statusDotColor(status: Paper['status']): string {
  switch (status) {
    case 'done': return 'bg-green-500'
    case 'extracting':
    case 'analyzing': return 'bg-yellow-500'
    case 'failed': return 'bg-red-500'
    default: return 'bg-gray-500'
  }
}

export function Sidebar({ papers, activeId, onSelect, onDelete }: SidebarProps) {
  const [search, setSearch] = useState('')

  const filtered = useMemo(() => {
    if (!search.trim()) return papers
    const q = search.toLowerCase()
    return papers.filter(p =>
      (p.title || p.filename).toLowerCase().includes(q)
    )
  }, [papers, search])

  return (
    <div className="flex flex-col w-64 h-full bg-gray-900 border-r border-gray-800 shrink-0">
      {/* 搜索框 */}
      <div className="p-3 border-b border-gray-800">
        <input
          type="text"
          placeholder="搜索论文..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="w-full px-3 py-1.5 text-sm bg-gray-800 border border-gray-700 rounded text-gray-200 placeholder-gray-500 focus:outline-none focus:border-blue-500"
        />
      </div>

      {/* 论文列表 */}
      <div className="flex-1 overflow-auto">
        {filtered.length === 0 ? (
          <p className="text-gray-500 text-sm text-center mt-8">暂无论文</p>
        ) : (
          filtered.map(paper => (
            <div
              key={paper.id}
              onClick={() => onSelect(paper.id)}
              onContextMenu={e => {
                e.preventDefault()
                onDelete(paper.id)
              }}
              className={`flex items-start gap-2 px-3 py-2.5 cursor-pointer transition-colors border-l-2 ${
                activeId === paper.id
                  ? 'bg-gray-800 border-blue-500'
                  : 'border-transparent hover:bg-gray-800/50'
              }`}
            >
              {/* 状态点 */}
              <span className={`w-2 h-2 mt-1.5 rounded-full shrink-0 ${statusDotColor(paper.status)}`} />

              {/* 论文信息 */}
              <div className="flex-1 min-w-0">
                <p className="text-sm text-gray-200 truncate">
                  {paper.title || paper.filename}
                </p>
                <div className="flex items-center gap-2 mt-0.5">
                  {paper.venue && (
                    <span className="text-xs text-gray-500">{paper.venue}</span>
                  )}
                  {paper.year && (
                    <span className="text-xs text-gray-500">{paper.year}</span>
                  )}
                </div>
              </div>

              {/* 删除按钮 */}
              <button
                onClick={e => {
                  e.stopPropagation()
                  onDelete(paper.id)
                }}
                className="text-gray-600 hover:text-red-400 text-xs shrink-0 mt-0.5 opacity-0 group-hover:opacity-100 transition-opacity"
                title="删除"
              >
                ×
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
