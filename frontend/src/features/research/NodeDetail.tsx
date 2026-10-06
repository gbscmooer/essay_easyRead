import { useState, useEffect, useRef, type CSSProperties } from 'react'
import type { GraphNode } from '@/types'
import { Link } from 'react-router-dom'
import { digestPaper } from '@/api/research'
import { toast } from '@/hooks/useToast'
import { ROLE_META } from '@/theme/graphPalette'

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
  const identity = node?.id
  const currentIdentity = useRef(identity)
  currentIdentity.current = identity
  useEffect(() => {
    setLocalSummary(null)
    setDigesting(false)
  }, [identity])

  if (!node) return null

  const summary = localSummary ?? node.meta?.summary ?? null
  const readLevel = localSummary ? 'digest' : (node.meta?.read_level ?? 'none')
  const badge = READ_LEVEL_LABEL[readLevel] ?? READ_LEVEL_LABEL.none
  const sem = node.semantic
  const roleMeta = sem && sem.role !== 'regular' ? ROLE_META[sem.role] : null

  const handleDigest = async () => {
    if (!node.meta?.paper_id) return
    setDigesting(true)
    try {
      const r = await digestPaper(node.meta.paper_id)
      const s = (r.digest as { summary?: string })?.summary ?? ''
      if (currentIdentity.current !== identity) return
      setLocalSummary(s || '（已速读，无总结文本）')
      toast({ type: 'success', message: '速读完成。重建图谱后这篇会获得可解释关系' })
    } catch (e) {
      if (currentIdentity.current !== identity) return
      const msg = (e as { detail?: string })?.detail || String(e)
      toast({ type: 'error', message: `速读失败：${msg}` })
    } finally {
      if (currentIdentity.current === identity) setDigesting(false)
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
            type="button"
            onClick={onClose}
            aria-label="关闭节点详情"
            className="er-btn er-btn--ghost er-btn--sm"
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
            <div className="flex items-center flex-wrap gap-2">
              <span className="er-tag" style={{ '--tag-accent': badge.color } as CSSProperties}>
                {badge.text}
              </span>
              {roleMeta && (
                <span className="er-tag" style={{ '--tag-accent': roleMeta.ink } as CSSProperties}>
                  {roleMeta.icon} {roleMeta.label}
                </span>
              )}
              {node.meta.source && (
                <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
                  {node.meta.source === 'uploaded' ? '用户上传' : '联网扩展'}
                </span>
              )}
            </div>
          )}

          {sem?.short_takeaway && (
            <div>
              <span className="text-label" style={{ color: 'var(--text-muted)' }}>一句话核心创新</span>
              <p className="text-[13px] leading-relaxed mt-1 font-medium" style={{ color: 'var(--text-primary)' }}>
                {sem.short_takeaway}
              </p>
            </div>
          )}

          {sem?.key_techniques?.length ? (
            <div>
              <span className="text-label" style={{ color: 'var(--text-muted)' }}>核心方法</span>
              <div className="flex flex-wrap gap-1.5 mt-1.5">
                {sem.key_techniques.map((t, i) => (
                  <span key={i} className="er-tag">{t}</span>
                ))}
              </div>
            </div>
          ) : null}

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
            {typeof node.meta.citation_count === 'number' && node.meta.citation_count > 0 && (
              <div>
                <span className="text-label" style={{ color: 'var(--text-muted)' }}>被引数</span>
                <p className="text-[13px] mt-0.5 tabular-nums" style={{ color: 'var(--text-secondary)' }}>
                  {node.meta.citation_count.toLocaleString()}
                </p>
              </div>
            )}
          </div>

          {typeof node.meta.importance === 'number' && (
            <div>
              <span className="text-label" style={{ color: 'var(--text-muted)' }}>影响力 (PageRank)</span>
              <div className="flex items-center gap-2 mt-1.5">
                <div className="flex-1 h-1.5 overflow-hidden" style={{ backgroundColor: 'var(--bg-elevated)' }}>
                  <div className="h-full" style={{ width: `${Math.round(node.meta.importance * 100)}%`, backgroundColor: 'var(--accent)' }} />
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
              type="button"
              onClick={handleDigest}
              disabled={digesting}
              aria-busy={digesting}
              className="er-btn er-btn--secondary er-btn--block mt-1"
              style={{ '--btn-accent': 'var(--success)' } as CSSProperties}
            >
              {digesting ? '速读中…' : '速读这篇（轻量，省 token）'}
            </button>
          )}

          {node.meta.paper_id && (
            <Link
              to={`/reader/${node.meta.paper_id}`}
              className="er-btn er-btn--primary er-btn--block mt-1"
            >
              查看论文精读 &rarr;
            </Link>
          )}
        </div>
      </div>
    </div>
  )
}
