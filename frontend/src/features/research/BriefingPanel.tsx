/**
 * 领域全景透视（Domain Briefing）面板
 *
 * 渲染 stage_synthesize 产出的 ExecutiveBriefing + 技术流派卡片：
 * 学者进入工作区第一眼读这份报告，3 分钟通晓领域全局，不必读任何单篇论文。
 * 数据来自 ResearchGraph.synthesis（GET /workspaces/{id}/graph 或 SSE synthesis_ready）。
 */
import { useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { CSSProperties } from 'react'
import type { GraphSynthesis, ParadigmInfo, ResearchGraph } from '@/types'
import { clusterInk } from '@/theme/graphPalette'

interface BriefingPanelProps {
  open: boolean
  onClose: () => void
  graph: ResearchGraph | null
  synthesis: GraphSynthesis | null
  onSelectNode?: (nodeId: string) => void
}

const PROSE_STYLE: React.CSSProperties = {
  '--tw-prose-body': 'var(--text-secondary)',
  '--tw-prose-headings': 'var(--text-primary)',
  '--tw-prose-bold': 'var(--text-primary)',
  '--tw-prose-code': 'var(--text-primary)',
  '--tw-prose-links': 'var(--accent)',
  '--tw-prose-quotes': 'var(--text-secondary)',
  '--tw-prose-bullets': 'var(--text-muted)',
  '--tw-prose-hr': 'var(--border)',
} as React.CSSProperties

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h4 className="text-label mb-2" style={{ color: 'var(--text-muted)' }}>{children}</h4>
}

function Bullets({ items, ink }: { items: string[]; ink?: string }) {
  if (!items.length) return null
  return (
    <ul className="space-y-1.5">
      {items.map((s, i) => (
        <li key={i} className="flex gap-2 text-[13px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          <span className="mt-[7px] w-1.5 h-1.5 shrink-0" style={{ backgroundColor: ink ?? 'var(--accent)' }} />
          <span>{s}</span>
        </li>
      ))}
    </ul>
  )
}

function ParadigmCard({ p, labelById, onSelectNode }: {
  p: ParadigmInfo
  labelById: Map<string, string>
  onSelectNode?: (nodeId: string) => void
}) {
  const ink = clusterInk(p.cluster_id)
  return (
    <div className="p-4 space-y-3" style={{ backgroundColor: 'var(--bg-elevated)', borderLeft: `3px solid ${ink}` }}>
      <div className="space-y-1">
        <div className="flex items-baseline justify-between gap-2">
          <h5 className="text-[14px] font-semibold" style={{ color: 'var(--text-primary)' }}>{p.name}</h5>
          <span className="text-[10px] shrink-0" style={{ color: 'var(--text-muted)' }}>子方向 #{p.cluster_id}</span>
        </div>
        {p.core_philosophy && (
          <p className="text-[12px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{p.core_philosophy}</p>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3">
        {p.pros.length > 0 && (
          <div>
            <span className="text-label" style={{ color: 'var(--success)' }}>优势</span>
            <Bullets items={p.pros} ink="var(--success)" />
          </div>
        )}
        {p.cons.length > 0 && (
          <div>
            <span className="text-label" style={{ color: 'var(--warning)' }}>软肋</span>
            <Bullets items={p.cons} ink="var(--warning)" />
          </div>
        )}
      </div>
      {p.representative_node_ids.length > 0 && (
        <div className="flex flex-wrap gap-1.5 pt-1" style={{ borderTop: '1px dashed var(--border)' }}>
          <span className="text-[10px] leading-5" style={{ color: 'var(--text-muted)' }}>代表作</span>
          {p.representative_node_ids.map(nid => (
            <button
              key={nid}
              type="button"
              className="er-tag cursor-pointer transition-opacity hover:opacity-75"
              style={{ '--tag-accent': ink } as CSSProperties}
              onClick={() => onSelectNode?.(nid)}
              title="在图谱中查看该论文"
            >
              {labelById.get(nid) ?? nid}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function BriefingPanel({ open, onClose, graph, synthesis, onSelectNode }: BriefingPanelProps) {
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    closeRef.current?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const briefing = synthesis?.briefing ?? graph?.synthesis?.briefing ?? null
  const paradigms = synthesis?.paradigms ?? graph?.synthesis?.paradigms ?? []
  const hasAnything = briefing || paradigms.length > 0
  const labelById = new Map((graph?.nodes ?? []).map(n => [n.id, n.label]))

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0" style={{ backgroundColor: 'rgba(0,0,0,0.4)', backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)' }} />
      <div
        className="relative w-[34rem] max-w-full h-full overflow-y-auto p-5 space-y-6 animate-slide-in-x"
        style={{ backgroundColor: 'var(--bg-surface)', borderLeft: '1px solid var(--border)' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-heading text-[15px]" style={{ color: 'var(--text-primary)' }}>📜 领域全景透视</h3>
            <p className="text-[11px] mt-0.5" style={{ color: 'var(--text-muted)' }}>3 分钟读懂领域全局 · 不必先读单篇论文</p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="关闭领域全景"
            className="er-btn er-btn--ghost er-btn--sm"
          >
            关闭
          </button>
        </div>

        {!hasAnything && (
          <div className="text-center py-10">
            <p className="text-[13px]" style={{ color: 'var(--text-secondary)' }}>暂无领域认知综合结果</p>
            <p className="text-[12px] mt-1.5 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
              重新运行一次深度研究（需在设置中配置解读 API Key），系统会在构建图谱后自动生成
              节点角色研判、引用因果与技术流派简报。
            </p>
          </div>
        )}

        {briefing && (
          <>
            {briefing.domain_problem && (
              <section>
                <SectionTitle>领域本质与核心矛盾</SectionTitle>
                <p className="text-[13px] leading-relaxed" style={{ color: 'var(--text-primary)' }}>{briefing.domain_problem}</p>
              </section>
            )}

            {briefing.milestone_timeline.length > 0 && (
              <section>
                <SectionTitle>技术演进时间轴</SectionTitle>
                <div className="space-y-1.5">
                  {briefing.milestone_timeline.map((m, i) => (
                    <div key={i} className="flex gap-3 items-baseline">
                      <span className="text-[12px] font-semibold tabular-nums shrink-0 w-14 text-right" style={{ color: 'var(--accent)' }}>{m.year}</span>
                      <span className="text-[12px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{m.event}</span>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {paradigms.length > 0 && (
              <section className="space-y-3">
                <SectionTitle>主流技术路线</SectionTitle>
                {paradigms.map(p => (
                  <ParadigmCard key={p.cluster_id} p={p} labelById={labelById} onSelectNode={onSelectNode} />
                ))}
              </section>
            )}

            {briefing.benchmark_standards.length > 0 && (
              <section>
                <SectionTitle>标准评测体系</SectionTitle>
                <Bullets items={briefing.benchmark_standards} />
              </section>
            )}

            {briefing.recent_hotspots.length > 0 && (
              <section>
                <SectionTitle>近期爆发热点</SectionTitle>
                <Bullets items={briefing.recent_hotspots} ink="#d94a38" />
              </section>
            )}

            {briefing.open_challenges.length > 0 && (
              <section>
                <SectionTitle>未解决的公开挑战</SectionTitle>
                <Bullets items={briefing.open_challenges} ink="#7d4a6d" />
              </section>
            )}

            {briefing.markdown_report && (
              <section>
                <SectionTitle>完整综述</SectionTitle>
                <div
                  className="prose prose-sm max-w-none text-[13px] leading-relaxed"
                  style={PROSE_STYLE}
                >
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{briefing.markdown_report}</ReactMarkdown>
                </div>
              </section>
            )}
          </>
        )}

        {!briefing && paradigms.length > 0 && (
          <section className="space-y-3">
            <SectionTitle>主流技术路线</SectionTitle>
            {paradigms.map(p => (
              <ParadigmCard key={p.cluster_id} p={p} labelById={labelById} onSelectNode={onSelectNode} />
            ))}
          </section>
        )}
      </div>
    </div>
  )
}
