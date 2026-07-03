import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import type { WorkspacePaper, WorkspaceComparisonResponse, WorkspaceComparisonSection } from '@/api/workspaces'
import { compareWorkspacePapers } from '@/api/workspaces'
import { toast } from '@/hooks/useToast'

interface WorkspaceComparisonProps {
  workspaceId: number
  papers: WorkspacePaper[]
}

const DETAIL_LABEL: Record<string, string> = {
  full: '精读',
  digest: '速读',
  metadata: '元信息',
}

const SECTION_COLORS: Record<string, string> = {
  purpose: '#3b82f6',
  method: '#8b5cf6',
  experiments: '#10b981',
  implementation: '#f59e0b',
  metadata: '#6b7280',
}

function PillList({ items, tone = 'var(--bg-elevated)', color = 'var(--text-secondary)' }: { items: string[]; tone?: string; color?: string }) {
  if (!items.length) {
    return <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>暂无</span>
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map(item => (
        <span
          key={item}
          className="text-[11px] px-2 py-0.5 rounded-full"
          style={{ backgroundColor: tone, color }}
        >
          {item}
        </span>
      ))}
    </div>
  )
}

function SectionRow({ section }: { section: WorkspaceComparisonSection }) {
  const color = SECTION_COLORS[section.key] ?? 'var(--accent)'
  return (
    <div className="py-3.5 border-t first:border-t-0" style={{ borderColor: 'var(--border)' }}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: color }} />
          <span className="text-[13px] font-medium truncate" style={{ color: 'var(--text-primary)' }}>
            {section.label}
          </span>
        </div>
        <span className="text-[12px] tabular-nums shrink-0" style={{ color: 'var(--text-secondary)' }}>
          {(section.score * 100).toFixed(0)}%
        </span>
      </div>
      <div className="mt-2 h-1.5 rounded-full overflow-hidden" style={{ backgroundColor: 'var(--bg-elevated)' }}>
        <div className="h-full rounded-full" style={{ width: `${Math.max(0, section.score * 100)}%`, backgroundColor: color }} />
      </div>
      <p className="mt-2 text-[12px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
        {section.summary}
      </p>
      <div className="mt-2 grid gap-2 md:grid-cols-3">
        <div>
          <div className="text-[11px] mb-1" style={{ color: 'var(--text-muted)' }}>共同点</div>
          <PillList items={section.shared} tone="var(--accent-subtle)" color="var(--accent)" />
        </div>
        <div>
          <div className="text-[11px] mb-1" style={{ color: 'var(--text-muted)' }}>左侧论文</div>
          <PillList items={section.unique_a} />
        </div>
        <div>
          <div className="text-[11px] mb-1" style={{ color: 'var(--text-muted)' }}>右侧论文</div>
          <PillList items={section.unique_b} />
        </div>
      </div>
    </div>
  )
}

export function WorkspaceComparison({ workspaceId, papers }: WorkspaceComparisonProps) {
  const [paperAId, setPaperAId] = useState<number | null>(null)
  const [paperBId, setPaperBId] = useState<number | null>(null)
  const [comparison, setComparison] = useState<WorkspaceComparisonResponse | null>(null)
  const [loading, setLoading] = useState(false)

  const paperMap = useMemo(() => new Map(papers.map(p => [p.id, p])), [papers])
  const firstPaperId = papers[0]?.id ?? null
  const secondPaperId = papers.find(p => p.id !== firstPaperId)?.id ?? null
  const canCompare = paperAId !== null && paperBId !== null && paperAId !== paperBId

  useEffect(() => {
    if (papers.length < 2) {
      setPaperAId(null)
      setPaperBId(null)
      setComparison(null)
      return
    }

    setPaperAId(prev => (prev !== null && paperMap.has(prev) ? prev : firstPaperId))
    setPaperBId(prev => {
      if (prev !== null && paperMap.has(prev) && prev !== firstPaperId) {
        return prev
      }
      return secondPaperId
    })
  }, [papers, firstPaperId, secondPaperId, paperMap])

  useEffect(() => {
    if (!comparison) return
    if (comparison.paper_a.paper_id !== paperAId || comparison.paper_b.paper_id !== paperBId) {
      setComparison(null)
    }
  }, [paperAId, paperBId, comparison])

  useEffect(() => {
    if (paperAId === null || paperBId === null) return
    if (paperAId === paperBId && papers.length > 1) {
      const fallback = papers.find(p => p.id !== paperAId)?.id ?? null
      setPaperBId(fallback)
    }
  }, [paperAId, paperBId, papers])

  const handleCompare = async () => {
    if (!canCompare) return
    setLoading(true)
    try {
      const result = await compareWorkspacePapers(workspaceId, paperAId!, paperBId!)
      setComparison(result)
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : '对比失败'
      toast({ type: 'error', message })
    } finally {
      setLoading(false)
    }
  }

  if (papers.length < 2) return null

  const selectedA = paperAId ? paperMap.get(paperAId) : null
  const selectedB = paperBId ? paperMap.get(paperBId) : null

  return (
    <section className="mb-8 py-4 border-y" style={{ borderColor: 'var(--border)' }}>
      <div className="flex items-center justify-between gap-3 mb-3">
        <div>
          <div className="text-[12px] uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>对比</div>
          <div className="text-[15px] font-medium mt-0.5" style={{ color: 'var(--text-primary)' }}>论文对比</div>
        </div>
        <span className="text-[11px] px-2 py-0.5 rounded-full" style={{ backgroundColor: 'var(--bg-elevated)', color: 'var(--text-muted)' }}>
          工作区内任意两篇
        </span>
      </div>

      <div className="grid gap-2 md:grid-cols-[1fr_1fr_auto]">
        <label className="block">
          <span className="sr-only">选择左侧论文</span>
          <select
            value={paperAId ?? ''}
            onChange={e => setPaperAId(Number(e.target.value))}
            className="w-full px-3 py-2 text-[12px] rounded-lg border focus:outline-none"
            style={{ backgroundColor: 'var(--bg-surface)', borderColor: 'var(--border)', color: 'var(--text-primary)' }}
          >
            {papers.map(paper => (
              <option key={paper.id} value={paper.id}>
                {paper.title || paper.filename}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="sr-only">选择右侧论文</span>
          <select
            value={paperBId ?? ''}
            onChange={e => setPaperBId(Number(e.target.value))}
            className="w-full px-3 py-2 text-[12px] rounded-lg border focus:outline-none"
            style={{ backgroundColor: 'var(--bg-surface)', borderColor: 'var(--border)', color: 'var(--text-primary)' }}
          >
            {papers.map(paper => (
              <option key={paper.id} value={paper.id}>
                {paper.title || paper.filename}
              </option>
            ))}
          </select>
        </label>
        <button
          onClick={handleCompare}
          disabled={!canCompare || loading}
          className="px-4 py-2 text-[12px] rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          style={{ backgroundColor: 'var(--accent)', color: '#fff' }}
        >
          {loading ? '对比中…' : '生成对比'}
        </button>
      </div>

      {paperAId === paperBId && (
        <p className="mt-2 text-[11px]" style={{ color: 'var(--warning)' }}>请选择两篇不同的论文</p>
      )}

      {comparison && (
        <div className="mt-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[12px] px-2.5 py-1 rounded-full" style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }}>
              {comparison.relation}
            </span>
            <span className="text-[12px] tabular-nums" style={{ color: 'var(--text-secondary)' }}>
              综合相似度 {(comparison.overall_score * 100).toFixed(0)}%
            </span>
            {comparison.same_cluster && comparison.cluster_label && (
              <span className="text-[11px] px-2 py-0.5 rounded-full" style={{ backgroundColor: 'var(--success-subtle)', color: 'var(--success)' }}>
                同子方向 · {comparison.cluster_label}
              </span>
            )}
            {comparison.direct_edge_kinds.map(kind => (
              <span key={kind} className="text-[11px] px-2 py-0.5 rounded-full" style={{ backgroundColor: 'var(--bg-elevated)', color: 'var(--text-muted)' }}>
                {kind}
              </span>
            ))}
          </div>

          <div className="mt-3 grid gap-3 md:grid-cols-2">
            {[comparison.paper_a, comparison.paper_b].map((paper, idx) => (
              <div key={paper.paper_id} className="rounded-lg p-3" style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border)' }}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-[11px] mb-1" style={{ color: 'var(--text-muted)' }}>
                      {idx === 0 ? '左侧论文' : '右侧论文'}
                    </div>
                    <Link to={`/reader/${paper.paper_id}`} className="text-[13px] font-medium leading-snug hover:opacity-80" style={{ color: 'var(--text-primary)' }}>
                      {paper.title}
                    </Link>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px]" style={{ color: 'var(--text-muted)' }}>
                      {paper.year && <span>{paper.year}</span>}
                      {paper.venue && <span>{paper.venue}</span>}
                      <span>{DETAIL_LABEL[paper.detail_level] ?? paper.detail_level}</span>
                    </div>
                  </div>
                  <span className="text-[11px] px-2 py-0.5 rounded-full shrink-0" style={{ backgroundColor: 'var(--bg-surface)', color: 'var(--text-muted)' }}>
                    {paper.read_level === 'full' ? '精读' : paper.read_level === 'digest' ? '速读' : '未解读'}
                  </span>
                </div>
              </div>
            ))}
          </div>

          <div className="mt-3 text-[12px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
            {comparison.relation_reason}
          </div>

          <div className="mt-3">
            <div className="text-[11px] mb-1.5" style={{ color: 'var(--text-muted)' }}>共享主题</div>
            <PillList items={comparison.shared_topics} tone="var(--accent-subtle)" color="var(--accent)" />
          </div>

          <div className="mt-4 rounded-lg" style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)' }}>
            {comparison.sections.map(section => (
              <SectionRow key={section.key} section={section} />
            ))}
          </div>

          <div className="mt-3 text-[12px]" style={{ color: 'var(--text-muted)' }}>
            {comparison.reading_order.reason}
          </div>
          <div className="mt-2 grid gap-2 md:grid-cols-2">
            <Link
              to={`/reader/${comparison.reading_order.first_paper_id}`}
              className="text-[12px] px-3 py-2 rounded-lg transition-colors"
              style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)', backgroundColor: 'var(--bg-surface)' }}
            >
              先读：{comparison.reading_order.first_title}
            </Link>
            <Link
              to={`/reader/${comparison.reading_order.second_paper_id}`}
              className="text-[12px] px-3 py-2 rounded-lg transition-colors"
              style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)', backgroundColor: 'var(--bg-surface)' }}
            >
              再读：{comparison.reading_order.second_title}
            </Link>
          </div>
        </div>
      )}

      {!comparison && !loading && selectedA && selectedB && selectedA.id !== selectedB.id && (
        <p className="mt-3 text-[12px]" style={{ color: 'var(--text-muted)' }}>
          选择两篇论文后点击「生成对比」即可查看结构化差异。
        </p>
      )}
    </section>
  )
}
