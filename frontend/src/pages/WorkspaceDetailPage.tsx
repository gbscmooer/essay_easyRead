/**
 * WorkspaceDetailPage — 工作区详情
 *
 * Display 排版 + surface-lifted PaperCard + 药丸按钮 + gradient CTA。
 */

import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import {
  getWorkspace,
  getWorkspaceInsights,
  getWorkspaceReadingPlan,
  getWorkspaceSurvey,
  updateWorkspace,
  addPaperToWorkspace,
  removePaperFromWorkspace,
  promotePaper,
  type WorkspaceDetail,
  type WorkspacePaper,
  type WorkspaceInsightResponse,
  type WorkspaceReadingPlanResponse,
  type WorkspaceSurveyResponse,
} from '@/api/workspaces'
import { listPapers } from '@/api/papers'
import type { Paper } from '@/types'
import { toast } from '@/hooks/useToast'
import { ConfirmDialog, LoadingSpinner } from '@/ds-entry'
import { WorkspaceInsights } from '@/features/workspace/WorkspaceInsights'
import { WorkspaceComparison } from '@/features/workspace/WorkspaceComparison'
import { WorkspaceInnovationPanel } from '@/features/workspace/WorkspaceInnovationPanel'


export default function WorkspaceDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const workspaceId = Number(id) || 0

  const [workspace, setWorkspace] = useState<WorkspaceDetail | null>(null)
  const [insights, setInsights] = useState<WorkspaceInsightResponse | null>(null)
  const [readingPlan, setReadingPlan] = useState<WorkspaceReadingPlanResponse | null>(null)
  const [survey, setSurvey] = useState<WorkspaceSurveyResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [insightsLoading, setInsightsLoading] = useState(true)
  const [innovationLoading, setInnovationLoading] = useState(true)
  const [editing, setEditing] = useState(false)
  const [editName, setEditName] = useState('')
  const [showAddPaper, setShowAddPaper] = useState(false)
  const [libraryPapers, setLibraryPapers] = useState<Paper[]>([])
  const [loadingPapers, setLoadingPapers] = useState(false)
  const [selectedDiscovered, setSelectedDiscovered] = useState<Set<number>>(new Set())
  const [batchLoading, setBatchLoading] = useState(false)
  const [batchRemoveConfirm, setBatchRemoveConfirm] = useState(false)
  const [singleRemoveConfirm, setSingleRemoveConfirm] = useState<(() => void) | null>(null)

  const refresh = useCallback(async (showLoading = false) => {
    if (!workspaceId) return
    if (showLoading) setLoading(true)
    setInsightsLoading(true)
    setInnovationLoading(true)
    try {
      const ws = await getWorkspace(workspaceId)
      setWorkspace(ws)
      setEditName(ws.name)
    } catch {
      navigate('/workspace')
      return
    } finally {
      if (showLoading) setLoading(false)
    }

    try {
      const data = await getWorkspaceInsights(workspaceId)
      setInsights(data)
    } catch {
      setInsights(null)
    } finally {
      setInsightsLoading(false)
    }

    try {
      const [planData, surveyData] = await Promise.all([
        getWorkspaceReadingPlan(workspaceId),
        getWorkspaceSurvey(workspaceId),
      ])
      setReadingPlan(planData)
      setSurvey(surveyData)
    } catch {
      setReadingPlan(null)
      setSurvey(null)
    } finally {
      setInnovationLoading(false)
    }
  }, [workspaceId, navigate])

  useEffect(() => { refresh(true) }, [refresh])

  const handleRename = async () => {
    const name = editName.trim()
    if (!name || !workspace) return
    try {
      await updateWorkspace(workspace.id, name)
      setEditing(false)
      refresh()
    } catch {
      toast({ type: 'error', message: '重命名失败' })
    }
  }

  const handleShowAddPaper = async () => {
    setShowAddPaper(true)
    setLoadingPapers(true)
    try {
      const res = await listPapers({ per_page: 100 })
      const existingIds = new Set(workspace?.papers.map(p => p.id) || [])
      setLibraryPapers(res.papers.filter(p => !existingIds.has(p.id)))
    } catch (e: unknown) {
      toast({ type: 'error', message: e instanceof Error ? e.message : '加载论文列表失败' })
      setShowAddPaper(false)
    } finally {
      setLoadingPapers(false)
    }
  }

  const handleAddPaper = async (paperId: number) => {
    try {
      await addPaperToWorkspace(workspaceId, paperId)
      refresh()
      setLibraryPapers(prev => prev.filter(p => p.id !== paperId))
    } catch {
      toast({ type: 'error', message: '添加论文失败' })
    }
  }

  const handleRemovePaper = async (paperId: number) => {
    try {
      await removePaperFromWorkspace(workspaceId, paperId)
      refresh()
    } catch {
      toast({ type: 'error', message: '移除论文失败' })
    }
  }

  const handlePromote = async (paperId: number) => {
    try {
      await promotePaper(workspaceId, paperId)
      refresh()
    } catch {
      toast({ type: 'error', message: '升级论文失败' })
    }
  }

  const toggleDiscovered = (paperId: number) => {
    setSelectedDiscovered(prev => {
      const next = new Set(prev)
      if (next.has(paperId)) next.delete(paperId); else next.add(paperId)
      return next
    })
  }

  const selectAllDiscovered = () => {
    setSelectedDiscovered(new Set(discoveredPapers.map(p => p.id)))
  }

  const deselectAllDiscovered = () => {
    setSelectedDiscovered(new Set())
  }

  const handleBatchPromote = async () => {
    if (selectedDiscovered.size === 0) return
    setBatchLoading(true)
    try {
      const ids = Array.from(selectedDiscovered)
      const results = await Promise.allSettled(ids.map(id => promotePaper(workspaceId, id)))
      const failed = results.filter(r => r.status === 'rejected').length
      setSelectedDiscovered(new Set())
      refresh()
      if (failed === 0) toast({ type: 'success', message: `已移入 ${ids.length} 篇论文` })
      else toast({ type: 'error', message: `${ids.length - failed} 篇已移入，${failed} 篇失败` })
    } finally {
      setBatchLoading(false)
    }
  }

  const handleBatchRemove = () => {
    if (selectedDiscovered.size === 0) return
    setBatchRemoveConfirm(true)
  }

  const confirmBatchRemove = async () => {
    setBatchRemoveConfirm(false)
    setBatchLoading(true)
    try {
      const ids = Array.from(selectedDiscovered)
      const results = await Promise.allSettled(ids.map(id => removePaperFromWorkspace(workspaceId, id)))
      const failed = results.filter(r => r.status === 'rejected').length
      setSelectedDiscovered(new Set())
      refresh()
      if (failed === 0) toast({ type: 'success', message: `已移除 ${ids.length} 篇论文` })
      else toast({ type: 'error', message: `${ids.length - failed} 篇已移除，${failed} 篇失败` })
    } finally {
      setBatchLoading(false)
    }
  }

  const l1Papers = (workspace?.papers ?? []).filter(p => (p.level ?? 1) === 1)
  const discoveredPapers = (workspace?.papers ?? []).filter(p => (p.level ?? 1) >= 2)

  if (loading || !workspace) {
    return (
      <div className="flex items-center justify-center h-64">
        <LoadingSpinner />
      </div>
    )
  }

  return (
    <div className="er-page er-page--workspace-detail max-w-3xl mx-auto px-8 py-10">
      {/* 返回链接 */}
      <Link
        to="/workspace"
        className="inline-flex items-center gap-1.5 text-[13px] transition-colors duration-200 mb-6"
        style={{ color: 'var(--text-muted)' }}
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 19l-7-7 7-7" />
        </svg>
        返回工作区
      </Link>

      {/* 标题 */}
      <div className="mb-8">
        {editing ? (
          <div className="flex items-center gap-2.5">
            <input
              type="text"
              value={editName}
              onChange={e => setEditName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleRename()}
              className="flex-1 px-4 py-2 text-[17px] transition-colors"
              style={{
                borderRadius: 'var(--seed-radius, 12px)',
                backgroundColor: 'var(--bg-elevated)',
                border: '1px solid var(--border)',
                color: 'var(--text-primary)',
                outline: 'none',
              }}
              autoFocus
            />
            <button type="button" onClick={handleRename} className="er-btn er-btn--primary">保存</button>
            <button type="button" onClick={() => { setEditing(false); setEditName(workspace.name) }} className="er-btn er-btn--ghost">取消</button>
          </div>
        ) : (
          <div className="flex items-center gap-3">
            <h1
              className="text-display text-[24px] cursor-pointer transition-colors duration-200"
              style={{ color: 'var(--text-primary)' }}
              role="button"
              tabIndex={0}
              onClick={() => setEditing(true)}
              onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setEditing(true) } }}
            >
              {workspace.name}
            </h1>
            <button
              onClick={() => setEditing(true)}
              className="text-[12px] transition-colors duration-200"
              style={{ color: 'var(--text-muted)' }}
            >
              重命名
            </button>
          </div>
        )}
        <p className="text-[13px] mt-2 flex items-center gap-3" style={{ color: 'var(--text-muted)' }}>
          <span>
            {l1Papers.length} 篇本地论文
            {discoveredPapers.length > 0 && ` · ${discoveredPapers.length} 篇深度研究发现`}
          </span>
          {workspace.papers.length > 0 && (
            workspace.papers.filter(p => !p.read_level || p.read_level === 'none').length === 0 ? (
              <span className="text-[11px] px-2 py-0.5 rounded-full" style={{ backgroundColor: 'var(--success-subtle)', color: 'var(--success)' }}>
                已全部速读
              </span>
            ) : (
              <span className="text-[11px] px-2 py-0.5 rounded-full" style={{ backgroundColor: 'var(--warning-subtle)', color: 'var(--warning)' }}>
                还有 {workspace.papers.filter(p => !p.read_level || p.read_level === 'none').length} 篇未速读
              </span>
            )
          )}
        </p>
      </div>

      {/* 操作区：添加论文 + 深度研究 CTA */}
      <div className="flex items-center gap-3 mb-6">
        <button
          type="button"
          onClick={handleShowAddPaper}
          className="er-btn er-btn--secondary er-btn--lg shrink-0"
        >
          从论文库添加
        </button>

        {/* 这一格是双行 CTA，撑不进 .er-btn 的单行盒子，因此只取偏移块语汇
            （.er-relief），其余按平面处理。渐变与圆角是上一套语言的残留 ——
            渐变会被硬边语言里的通配符和边框切出脏边。 */}
        <button
          type="button"
          onClick={() => navigate(`/workspace/${workspaceId}/research`)}
          disabled={workspace.papers.length === 0}
          className="er-relief flex-1 group relative px-6 py-3.5 text-left disabled:cursor-not-allowed"
          style={{
            border: '1px solid var(--er-ink)',
            background: 'var(--bg-surface)',
            color: 'var(--text-primary)',
          }}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
                <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
              </svg>
              <div className="text-left">
                <div className="text-[14px] font-medium leading-tight">深度研究</div>
                <div className="text-[11px] mt-0.5 opacity-80">
                  跨论文构建知识图谱 · RAG 问答
                </div>
              </div>
            </div>
            <svg className="w-5 h-5 opacity-60 group-hover:translate-x-1 transition-transform duration-200" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 12h14M12 5l7 7-7 7" />
            </svg>
          </div>
        </button>
      </div>

      <WorkspaceComparison workspaceId={workspaceId} papers={workspace.papers} />
      <WorkspaceInnovationPanel plan={readingPlan} survey={survey} loading={innovationLoading} />
      <WorkspaceInsights insights={insights} loading={insightsLoading} />

      {/* 添加论文面板 */}
      {showAddPaper && (
        <div
          className="mb-6 p-5 transition-all duration-200"
          style={{
            borderRadius: 'var(--seed-radius, 12px)',
            backgroundColor: 'var(--bg-elevated)',
            border: '1px solid var(--border)',
          }}
        >
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-[13px] font-medium" style={{ color: 'var(--text-secondary)' }}>选择论文添加到工作区</h3>
            <button onClick={() => setShowAddPaper(false)} className="text-[12px] rounded-full px-2.5 py-1 transition-colors duration-200" style={{ color: 'var(--text-muted)' }}>关闭</button>
          </div>
          {loadingPapers ? (
            <div className="flex justify-center py-4"><LoadingSpinner size="sm" /></div>
          ) : libraryPapers.length === 0 ? (
            <p className="text-[12px] text-center py-4" style={{ color: 'var(--text-muted)' }}>没有可添加的论文</p>
          ) : (
            <div className="max-h-60 overflow-auto space-y-1.5">
              {libraryPapers.map(paper => (
                <div
                  key={paper.id}
                  className="flex items-center justify-between px-4 py-2.5 transition-colors duration-200"
                  style={{ borderRadius: 8, backgroundColor: 'var(--bg-surface)' }}
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-[13px] truncate" style={{ color: 'var(--text-secondary)' }}>{paper.title || paper.filename}</p>
                    {paper.venue && <p className="text-[12px]" style={{ color: 'var(--text-muted)' }}>{paper.venue} {paper.year || ''}</p>}
                  </div>
                  <button
                    onClick={() => handleAddPaper(paper.id)}
                    className="ml-3 px-3 py-1 text-[12px] rounded-full transition-all duration-200 shrink-0"
                    style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }}
                  >
                    添加
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 本地论文 (L1) */}
      {l1Papers.length === 0 && discoveredPapers.length === 0 ? (
        <div className="text-center py-16">
          <p className="text-[13px]" style={{ color: 'var(--text-muted)' }}>工作区暂无论文</p>
          <p className="text-[12px] mt-1 mb-4" style={{ color: 'var(--text-muted)', opacity: 0.7 }}>点击「从论文库添加」将论文加入工作区</p>
          <button
            onClick={handleShowAddPaper}
            className="px-5 py-2.5 text-[13px] rounded-full font-medium transition-all duration-200"
            style={{ backgroundColor: 'var(--accent)', color: '#fff' }}
          >
            + 从论文库添加
          </button>
        </div>
      ) : (
        <>
          {l1Papers.length > 0 && (
            <div className="space-y-2.5">
              {l1Papers.map(paper => (
                <PaperCard
                  key={paper.id}
                  paper={paper}
                  workspaceId={workspaceId}
                  variant="local"
                  onNavigate={() => navigate(`/reader/${paper.id}`, { state: { from: `/workspace/${workspaceId}` } })}
                  onRemove={() => handleRemovePaper(paper.id)}
                  onRequestRemove={() => setSingleRemoveConfirm(() => () => handleRemovePaper(paper.id))}
                />
              ))}
            </div>
          )}

          {/* 深度研究发现 (L2+) */}
          {discoveredPapers.length > 0 && (
            <div className="mt-8">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2.5">
                  <h2 className="text-[13px] font-medium" style={{ color: 'var(--text-secondary)' }}>
                    通过深度研究搜索到的相关论文
                  </h2>
                  <span className="text-[11px] px-2 py-0.5 rounded-full" style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }}>
                    {discoveredPapers.length}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  {selectedDiscovered.size > 0 ? (
                    <>
                      <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
                        已选 {selectedDiscovered.size} 篇
                      </span>
                      <button
                        onClick={handleBatchPromote}
                        disabled={batchLoading}
                        className="px-3 py-1 text-[12px] rounded-full transition-all duration-200 disabled:opacity-50"
                        style={{ backgroundColor: 'var(--accent)', color: '#fff' }}
                      >
                        {batchLoading ? '移入中…' : '批量移入'}
                      </button>
                      <button
                        onClick={handleBatchRemove}
                        disabled={batchLoading}
                        className="px-3 py-1 text-[12px] rounded-full transition-all duration-200 disabled:opacity-50"
                        style={{ border: '1px solid var(--error)', color: 'var(--error)', backgroundColor: 'transparent' }}
                      >
                        {batchLoading ? '移除中…' : '批量移除'}
                      </button>
                      <button
                        onClick={deselectAllDiscovered}
                        className="text-[12px] transition-colors duration-200"
                        style={{ color: 'var(--text-muted)' }}
                      >
                        取消选择
                      </button>
                    </>
                  ) : (
                    <button
                      onClick={selectAllDiscovered}
                      className="text-[12px] transition-colors duration-200"
                      style={{ color: 'var(--text-muted)' }}
                    >
                      全选
                    </button>
                  )}
                </div>
              </div>
              <p className="text-[12px] mb-3" style={{ color: 'var(--text-muted)' }}>
                这些论文已索引、可参与跨论文检索；点击「移入工作区」可将其升级为本地正式论文。
              </p>
              <div className="space-y-2.5">
                {discoveredPapers.map(paper => (
                  <PaperCard
                    key={paper.id}
                    paper={paper}
                    workspaceId={workspaceId}
                    variant="discovered"
                    selected={selectedDiscovered.has(paper.id)}
                    onToggleSelect={() => toggleDiscovered(paper.id)}
                    onNavigate={() => navigate(`/reader/${paper.id}`, { state: { from: `/workspace/${workspaceId}` } })}
                    onRemove={() => handleRemovePaper(paper.id)}
                    onPromote={() => handlePromote(paper.id)}
                    onRequestRemove={() => setSingleRemoveConfirm(() => () => handleRemovePaper(paper.id))}
                  />
                ))}
              </div>
            </div>
          )}
        </>
      )}

      <ConfirmDialog
        open={batchRemoveConfirm}
        title="批量移除"
        message={`确定从工作区移除选中的 ${selectedDiscovered.size} 篇论文？`}
        variant="danger"
        onConfirm={confirmBatchRemove}
        onCancel={() => setBatchRemoveConfirm(false)}
      />

      <ConfirmDialog
        open={singleRemoveConfirm !== null}
        title="确认移除"
        message="确定从工作区移除此论文？"
        variant="danger"
        onConfirm={() => { singleRemoveConfirm?.(); setSingleRemoveConfirm(null) }}
        onCancel={() => setSingleRemoveConfirm(null)}
      />
    </div>
  )
}


/* ============================================================
 * PaperCard
 * ============================================================ */

interface PaperCardProps {
  paper: WorkspacePaper
  workspaceId: number
  variant: 'local' | 'discovered'
  selected?: boolean
  onToggleSelect?: () => void
  onNavigate: () => void
  onRemove: () => void
  onPromote?: () => void
  onRequestRemove: () => void
}

function PaperCard({ paper, workspaceId, variant, selected, onToggleSelect, onNavigate, onPromote, onRequestRemove }: PaperCardProps) {
  const isDiscovered = variant === 'discovered'
  return (
    <div
      className="list-row group card-hover press"
      style={{
        borderBottomStyle: isDiscovered ? 'dashed' : 'solid',
        backgroundColor: selected ? 'var(--accent-subtle)' : undefined,
        borderLeft: selected ? '2px solid var(--accent)' : undefined,
        paddingLeft: selected ? 'calc(0.875rem - 2px)' : undefined,
      }}
      role="button"
      tabIndex={0}
      onClick={onNavigate}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onNavigate() } }}
    >
      <div className="flex items-start justify-between">
        {/* Checkbox for discovered */}
        {isDiscovered && onToggleSelect && (
          <div
            className="shrink-0 mr-3 mt-0.5"
            onClick={e => { e.stopPropagation(); onToggleSelect() }}
          >
            <div
              className="w-4 h-4 rounded flex items-center justify-center transition-colors duration-200"
              style={{
                borderColor: selected ? 'var(--accent)' : 'var(--border)',
                backgroundColor: selected ? 'var(--accent)' : 'transparent',
                border: `1.5px solid ${selected ? 'var(--accent)' : 'var(--border)'}`,
              }}
            >
              {selected && (
                <svg className="w-3 h-3" fill="none" stroke="#fff" viewBox="0 0 24 24" strokeWidth={3}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
              )}
            </div>
          </div>
        )}
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-medium transition-colors" style={{ color: 'var(--text-primary)' }}>
            {paper.title || paper.filename}
          </p>
          <div className="flex items-center gap-2.5 mt-1.5 flex-wrap">
            {paper.venue && <span className="text-[12px]" style={{ color: 'var(--text-muted)' }}>{paper.venue} {paper.year || ''}</span>}
            <span
              className="text-[11px] px-2 py-0.5 rounded-full"
              style={{
                backgroundColor:
                  paper.status === 'done' ? 'var(--success-subtle)' :
                  paper.status === 'indexed' ? 'var(--warning-subtle)' :
                  paper.status === 'failed' ? 'var(--error-subtle)' :
                  'var(--bg-elevated)',
                color:
                  paper.status === 'done' ? 'var(--success)' :
                  paper.status === 'indexed' ? 'var(--warning)' :
                  paper.status === 'failed' ? 'var(--error)' :
                  'var(--text-muted)',
              }}
            >
              {paper.status === 'done' ? '已解析' :
               paper.status === 'indexed' ? '未解析·已索引' :
               paper.status === 'failed' ? '失败' : '待解析'}
            </span>
            {isDiscovered && (
              <span
                className="text-[11px] px-2 py-0.5 rounded-full"
                style={{ border: '1px solid var(--border)', color: 'var(--text-muted)' }}
                title="联网扩展下载的论文"
              >
                L{paper.level} · 扩展
              </span>
            )}
          </div>
          <div className="flex items-center gap-3 mt-2.5">
            <Link
              to={`/reader/${paper.id}`}
              state={{ from: `/workspace/${workspaceId}` }}
              onClick={e => e.stopPropagation()}
              className="text-[12px] transition-colors duration-200"
              style={{ color: 'var(--accent)' }}
            >
              论文分析
            </Link>
            {paper.code_url && (
              <Link
                to={`/code/${paper.id}`}
                state={{ from: `/workspace/${workspaceId}` }}
                onClick={e => e.stopPropagation()}
                className="text-[12px] transition-colors duration-200"
                style={{ color: 'var(--text-muted)' }}
              >
                源码分析
              </Link>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 ml-3 shrink-0">
          {isDiscovered && onPromote && (
            <button
              onClick={(e) => { e.stopPropagation(); onPromote() }}
              className="px-3 py-1 text-[12px] rounded-full transition-all duration-200"
              style={{ border: '1px solid var(--accent)', color: 'var(--accent)', backgroundColor: 'var(--accent-subtle)' }}
              title="升级为本地正式论文"
            >
              移入工作区
            </button>
          )}
          <button
            onClick={(e) => { e.stopPropagation(); onRequestRemove() }}
            className="opacity-0 group-hover:opacity-100 focus:opacity-100 px-2.5 py-1 text-[12px] rounded-full transition-all duration-200"
            style={{ color: 'var(--text-muted)' }}
          >
            移除
          </button>
        </div>
      </div>
    </div>
  )
}
