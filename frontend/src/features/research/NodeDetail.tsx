import type { GraphNode } from '@/types'
import { Link } from 'react-router-dom'

interface NodeDetailProps {
  node: GraphNode | null
  onClose: () => void
}

export function NodeDetail({ node, onClose }: NodeDetailProps) {
  if (!node) return null

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div
        className="relative w-80 h-full overflow-y-auto p-5 space-y-4"
        style={{ backgroundColor: 'var(--bg-surface)', borderLeft: '1px solid var(--border)' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>节点详情</h3>
          <button
            onClick={onClose}
            className="text-xs px-2 py-1 rounded transition-colors"
            style={{ color: 'var(--text-muted)' }}
          >
            关闭
          </button>
        </div>

        <div className="space-y-3">
          <div>
            <span className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>类型</span>
            <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>{node.kind}</p>
          </div>

          <div>
            <span className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>标签</span>
            <p className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>{node.label}</p>
          </div>

          {node.meta.paper_id && (
            <div>
              <span className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>论文 ID</span>
              <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>#{node.meta.paper_id}</p>
            </div>
          )}

          {node.meta.year && (
            <div>
              <span className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>年份</span>
              <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>{node.meta.year}</p>
            </div>
          )}

          {node.meta.venue && (
            <div>
              <span className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>会议/期刊</span>
              <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>{node.meta.venue}</p>
            </div>
          )}

          {node.meta.source && (
            <div>
              <span className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>来源</span>
              <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
                {node.meta.source === 'uploaded' ? '用户上传' : '联网扩展'}
              </p>
            </div>
          )}

          {node.meta.paper_id && (
            <Link
              to={`/reader/${node.meta.paper_id}`}
              className="block w-full text-center py-2 text-xs font-medium rounded-lg transition-colors mt-4"
              style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }}
            >
              查看论文分析 &rarr;
            </Link>
          )}
        </div>
      </div>
    </div>
  )
}
