import { useState } from 'react'
import type { GraphNode } from '@/types'
import { Link } from 'react-router-dom'
import { digestPaper } from '@/api/research'
import { toast } from '@/hooks/useToast'

interface NodeDetailProps {
  node: GraphNode | null
  onClose: () => void
}

const READ_LEVEL_LABEL: Record<string, { text: string; color: string }> = {
  full:   { text: '已精读', color: 'var(--accent)' },
  digest: { text: '已速读', color: 'var(--success)' },
  none:   { text: '未解读', color: 'var(--text-muted)' },
}

export function NodeDetail({ node, onClose }: NodeDetailProps) {
  const [digesting, setDigesting] = useState(false)
  const [localSummary, setLocalSummary] = useState<string | null>(null)

  if (!node) return null

  const summary = localSummary ?? node.meta.summary ?? null
  const readLevel = localSummary ? 'digest' : (node.meta.read_level ?? 'none')
  const badge = READ_LEVEL_LABEL[readLevel] ?? READ_LEVEL_LABEL.none

  const handleDigest = async () => {
    if (!node.meta.paper_id) return
    setDigesting(true)
    try {
      const r = await digestPaper(node.meta.paper_id)
      const s = (r.digest as { summary?: string })?.summary ?? ''
      setLocalSummary(s || '（已速读，无总结文本）')
      toast({ type: 'success', message: '速读完成。重建图谱后这篇会获得可解释关系' })
    } catch (e) {
      const msg = (e as { detail?: string })?.detail || String(e)
      toast({ type: 'error', message: `速读失败：${msg}` })
    } finally {
      setDigesting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0" style={{ backgroundColor: 'rgba(0,0,0,0.4)', backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)' }} />
      <div
        className="relative w-80 h-full overflow-y-auto p-5 space-y-5 animate-slide-in-x"
        style={{ backgroundColor: 'var(--bg-surface)', borderLeft: '1px solid var(--border)' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-heading text-[15px]" style={{ color: 'var(--text-primary)' }}>节点详情</h3>
          <button
            onClick={onClose}
            className="text-[12px] px-3 py-1.5 rounded-full transition-colors hover:opacity-80"
            style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}
          >
            关闭
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <span className="text-label" style={{ color: 'var(--text-muted)' }}>标签</span>
            <p className="text-[14px] font-medium mt-1 leading-snug" style={{ color: 'var(--text-primary)' }}>{node.label}</p>
          </div>

          {node.meta.paper_id && (
            <div className="flex items-center gap-2">
              <span
                className="text-[11px] font-medium px-2 py-0.5 rounded-full"
                style={{ color: badge.color, border: `1px solid ${badge.color}` }}
              >
                {badge.text}
              </span>
              {node.meta.source && (
                <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
                  {node.meta.source === 'uploaded' ? '用户上传' : '联网扩展'}
                </span>
              )}
            </div>
          )}

          {summary && (
            <div>
              <span className="text-label" style={{ color: 'var(--text-muted)' }}>速读总结</span>
              <p className="text-[13px] leading-relaxed mt-1" style={{ color: 'var(--text-secondary)' }}>{summary}</p>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            {node.meta.year && (
              <div>
                <span className="text-label" style={{ color: 'var(--text-muted)' }}>年份</span>
                <p className="text-[13px] mt-0.5 tabular-nums" style={{ color: 'var(--text-secondary)' }}>{node.meta.year}</p>
              </div>
            )}
            {node.meta.venue && (
              <div>
                <span className="text-label" style={{ color: 'var(--text-muted)' }}>会议/期刊</span>
                <p className="text-[13px] mt-0.5" style={{ color: 'var(--text-secondary)' }}>{node.meta.venue}</p>
              </div>
            )}
          </div>

          {typeof node.meta.importance === 'number' && (
            <div>
              <span className="text-label" style={{ color: 'var(--text-muted)' }}>影响力 (PageRank)</span>
              <div className="flex items-center gap-2 mt-1.5">
                <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ backgroundColor: 'var(--bg-elevated)' }}>
                  <div className="h-full rounded-full" style={{ width: `${Math.round(node.meta.importance * 100)}%`, backgroundColor: 'var(--accent)' }} />
                </div>
                <span className="text-[12px] tabular-nums" style={{ color: 'var(--text-secondary)' }}>
                  {(node.meta.importance * 100).toFixed(0)}%
                </span>
              </div>
            </div>
          )}

          {node.meta.cluster_label && (
            <div>
              <span className="text-label" style={{ color: 'var(--text-muted)' }}>研究子方向</span>
              <p className="text-[13px] mt-0.5" style={{ color: 'var(--text-secondary)' }}>{node.meta.cluster_label}</p>
            </div>
          )}

          {/* 未解读的论文：提供按需「图谱速读」 */}
          {node.meta.paper_id && readLevel === 'none' && (
            <button
              onClick={handleDigest}
              disabled={digesting}
              className="block w-full text-center py-2 text-[12px] font-medium rounded-full transition-colors mt-1 disabled:opacity-50"
              style={{ border: '1px solid var(--success)', color: 'var(--success)' }}
            >
              {digesting ? '速读中…' : '速读这篇（轻量，省 token）'}
            </button>
          )}

          {node.meta.paper_id && (
            <Link
              to={`/reader/${node.meta.paper_id}`}
              className="block w-full text-center py-2 text-[12px] font-medium rounded-full transition-opacity hover:opacity-90 mt-1"
              style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }}
            >
              查看论文精读 &rarr;
            </Link>
          )}
        </div>
      </div>
    </div>
  )
}
