import { Link } from 'react-router-dom'
import type { ReactNode } from 'react'
import { LoadingSpinner } from '@/components/LoadingSpinner'
import type {
  WorkspaceReadingPlanPaper,
  WorkspaceReadingPlanResponse,
  WorkspaceSurveyResponse,
} from '@/api/workspaces'

interface WorkspaceInnovationPanelProps {
  plan: WorkspaceReadingPlanResponse | null
  survey: WorkspaceSurveyResponse | null
  loading: boolean
}

const READ_LABEL: Record<string, string> = {
  full: '精读',
  digest: '速读',
  none: '未解读',
}

function Chip({ children, tone = 'var(--bg-elevated)', color = 'var(--text-secondary)' }: { children: ReactNode; tone?: string; color?: string }) {
  return (
    <span
      className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px]"
      style={{ backgroundColor: tone, color }}
    >
      {children}
    </span>
  )
}

function PlanPaper({ paper }: { paper: WorkspaceReadingPlanPaper }) {
  return (
    <Link
      to={`/reader/${paper.paper_id}`}
      className="block py-2.5 border-t first:border-t-0 transition-colors hover:opacity-80"
      style={{ borderColor: 'var(--border)' }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[13px] font-medium truncate" style={{ color: 'var(--text-primary)' }}>
            {paper.title}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5" style={{ color: 'var(--text-muted)' }}>
            {paper.year && <span className="text-[11px]">{paper.year}</span>}
            {paper.venue && <span className="text-[11px]">{paper.venue}</span>}
            <Chip>{READ_LABEL[paper.read_level] ?? paper.read_level}</Chip>
            {paper.cluster_label && <Chip tone="var(--accent-subtle)" color="var(--accent)">{paper.cluster_label}</Chip>}
          </div>
          {paper.reason && (
            <div className="mt-1 text-[11px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
              {paper.reason}
            </div>
          )}
        </div>
        <span className="text-[11px] tabular-nums shrink-0" style={{ color: 'var(--text-muted)' }}>
          {(paper.score * 100).toFixed(0)}%
        </span>
      </div>
    </Link>
  )
}

function EmptyLine({ label }: { label: string }) {
  return (
    <div className="py-3 text-[12px]" style={{ color: 'var(--text-muted)' }}>
      {label}
    </div>
  )
}

export function WorkspaceInnovationPanel({ plan, survey, loading }: WorkspaceInnovationPanelProps) {
  if (loading) {
    return (
      <section className="mb-8 py-4 border-y" style={{ borderColor: 'var(--border)' }}>
        <div className="flex items-center gap-3">
          <LoadingSpinner size="sm" />
          <span className="text-[12px]" style={{ color: 'var(--text-muted)' }}>正在生成路径与矩阵</span>
        </div>
      </section>
    )
  }

  if (!plan && !survey) return null

  const coverage = plan?.coverage
  const topTopics = survey?.topics.slice(0, 8) ?? []
  const matrix = survey?.matrix.slice(0, 8) ?? []

  return (
    <section className="mb-8 py-4 border-y" style={{ borderColor: 'var(--border)' }}>
      <div className="flex items-center justify-between gap-3 mb-3">
        <div>
          <div className="text-[12px] uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>智能规划</div>
          <div className="text-[15px] font-medium mt-0.5" style={{ color: 'var(--text-primary)' }}>阅读路径与综述矩阵</div>
        </div>
        {coverage && (
          <span className="text-[11px] px-2 py-0.5 rounded-full" style={{ backgroundColor: 'var(--bg-elevated)', color: 'var(--text-muted)' }}>
            {coverage.paper_count} / {coverage.total_workspace_papers}
          </span>
        )}
      </div>

      {coverage && (
        <div className="grid grid-cols-4 gap-2 mb-4">
          <div className="rounded-lg px-3 py-2" style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
            <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>精读</div>
            <div className="mt-1 text-[16px] font-semibold tabular-nums" style={{ color: 'var(--accent)' }}>{coverage.full}</div>
          </div>
          <div className="rounded-lg px-3 py-2" style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
            <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>速读</div>
            <div className="mt-1 text-[16px] font-semibold tabular-nums" style={{ color: 'var(--success)' }}>{coverage.digest}</div>
          </div>
          <div className="rounded-lg px-3 py-2" style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
            <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>未读</div>
            <div className="mt-1 text-[16px] font-semibold tabular-nums" style={{ color: 'var(--warning)' }}>{coverage.none}</div>
          </div>
          <div className="rounded-lg px-3 py-2" style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
            <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>依赖</div>
            <div className="mt-1 text-[16px] font-semibold tabular-nums" style={{ color: 'var(--text-primary)' }}>{coverage.citation_edges}</div>
          </div>
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-[0.95fr_1.05fr]">
        <div>
          <div className="text-[12px] font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>阅读路径</div>
          {plan && plan.stages.length > 0 ? (
            <div className="space-y-3">
              {plan.stages.map(stage => (
                <div key={stage.stage}>
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <div className="text-[12px] font-medium" style={{ color: 'var(--text-primary)' }}>
                      {stage.stage}. {stage.label}
                    </div>
                    <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
                      {stage.papers.length} 篇
                    </div>
                  </div>
                  <div className="rounded-lg px-3" style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
                    {stage.papers.map(paper => <PlanPaper key={paper.paper_id} paper={paper} />)}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyLine label="暂无路径" />
          )}
        </div>

        <div>
          <div className="text-[12px] font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>综述矩阵</div>

          {topTopics.length > 0 && (
            <div className="mb-3 flex flex-wrap gap-1.5">
              {topTopics.map(topic => <Chip key={topic} tone="var(--accent-subtle)" color="var(--accent)">{topic}</Chip>)}
            </div>
          )}

          {survey && survey.draft_sections.length > 0 && (
            <div className="mb-3 space-y-2">
              {survey.draft_sections.slice(0, 2).map(section => (
                <div key={section.key} className="text-[12px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                  <span className="font-medium" style={{ color: 'var(--text-primary)' }}>{section.title}：</span>
                  {section.content}
                </div>
              ))}
            </div>
          )}

          {matrix.length > 0 ? (
            <div className="overflow-x-auto rounded-lg" style={{ border: '1px solid var(--border)' }}>
              <table className="w-full min-w-[680px] text-left text-[12px]" style={{ backgroundColor: 'var(--bg-surface)' }}>
                <thead style={{ color: 'var(--text-muted)', backgroundColor: 'var(--bg-elevated)' }}>
                  <tr>
                    <th className="px-3 py-2 font-medium">论文</th>
                    <th className="px-3 py-2 font-medium">任务</th>
                    <th className="px-3 py-2 font-medium">方法</th>
                    <th className="px-3 py-2 font-medium">数据集</th>
                  </tr>
                </thead>
                <tbody>
                  {matrix.map(row => (
                    <tr key={row.paper_id} className="border-t" style={{ borderColor: 'var(--border)' }}>
                      <td className="px-3 py-2 align-top w-[28%]">
                        <Link to={`/reader/${row.paper_id}`} className="font-medium hover:opacity-80" style={{ color: 'var(--text-primary)' }}>
                          {row.title}
                        </Link>
                        <div className="mt-1 text-[11px]" style={{ color: 'var(--text-muted)' }}>
                          {row.year || '年份未知'} · {row.detail_level}
                        </div>
                      </td>
                      <td className="px-3 py-2 align-top" style={{ color: 'var(--text-secondary)' }}>{row.task || '暂无'}</td>
                      <td className="px-3 py-2 align-top" style={{ color: 'var(--text-secondary)' }}>{row.method || '暂无'}</td>
                      <td className="px-3 py-2 align-top" style={{ color: 'var(--text-secondary)' }}>
                        {row.datasets.length > 0 ? row.datasets.join('、') : '暂无'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyLine label="暂无矩阵" />
          )}
        </div>
      </div>
    </section>
  )
}
