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
import '@/styles/workspace.css'
import { stopRowActivation } from '@/utils/keyboard'


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

  const unreadCount = (workspace.papers ?? []).filter(p => !p.read_level || p.read_level === 'none').length

  return (
    <div className="er-page er-page--workspace-detail">
      <Link to="/workspace" className="ws-back">
        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 19l-7-7 7-7" />
        </svg>
        返回工作区
      </Link>

      <header className="er-page-head">
        <p className="er-page-head__eyebrow">工作区详情 · 论文集合与深度研究</p>
        {editing ? (
          <div className="ws-rename">
            <input
              type="text"
              value={editName}
              onChange={e => setEditName(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') handleRename()
                if (e.key === 'Escape') { setEditing(false); setEditName(workspace.name) }
              }}
              className="er-input ws-rename__input"
              aria-label="工作区名称"
              autoFocus
            />
            <button type="button" onClick={handleRename} className="er-btn er-btn--primary er-btn--sm">保存</button>
            <button
              type="button"
              onClick={() => { setEditing(false); setEditName(workspace.name) }}
              className="er-btn er-btn--ghost er-btn--sm"
            >
              取消
            </button>
          </div>
        ) : (
          <h1 className="er-page-head__title">{workspace.name}</h1>
        )}
        {!editing && (
          <div className="er-page-head__actions">
            <button type="button" onClick={() => setEditing(true)} className="er-btn er-btn--ghost er-btn--sm">
              重命名
            </button>
          </div>
        )}
      </header>

      <div className="er-page-head__readouts">
        <span className="er-readout"><b>{l1Papers.length}</b>篇本地论文</span>
        {discoveredPapers.length > 0 && (
          <span className="er-readout"><b>{discoveredPapers.length}</b>篇深度研究发现</span>
        )}
        {(workspace.papers ?? []).length > 0 && (
          unreadCount === 0
            ? <span className="er-tag">已全部速读</span>
            : <span className="er-tag er-tag--warn">还有 {unreadCount} 篇未速读</span>
        )}
      </div>

      {/* 操作区：本页唯一的主行动是「深度研究」，它拿到唯一的实心质量；
          「从论文库添加」是它的前置准备，退到描边一级。 */}
      <div className="ws-actions">
        <button
          type="button"
          onClick={handleShowAddPaper}
          className="er-btn er-btn--secondary er-btn--lg"
        >
          从论文库添加
        </button>

        <button
          type="button"
          onClick={() => navigate(`/workspace/${workspaceId}/research`)}
          disabled={(workspace.papers ?? []).length === 0}
          className="ws-research er-relief"
        >
          <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
          </svg>
          <span className="ws-research__copy">
            <strong>发起文献研究</strong>
            <small>{(workspace.papers ?? []).length === 0 ? '先往集合里放入论文' : `${(workspace.papers ?? []).length} 篇母论文`}</small>
          </span>
          <span className="ws-research__go" aria-hidden="true">→</span>
        </button>
      </div>

      <WorkspaceComparison workspaceId={workspaceId} papers={workspace.papers ?? []} />
      <WorkspaceInnovationPanel plan={readingPlan} survey={survey} loading={innovationLoading} />
      <WorkspaceInsights insights={insights} loading={insightsLoading} />

      {/* 添加论文面板 */}
      {showAddPaper && (
        <section className="er-panel ws-add" aria-label="从论文库添加论文">
          <div className="er-panel__head">
            <h3 className="er-panel__title">选择论文加入工作区</h3>
            <button
              type="button"
              onClick={() => setShowAddPaper(false)}
              className="er-btn er-btn--ghost er-btn--sm"
            >
              关闭
            </button>
          </div>
          {loadingPapers ? (
            <div className="ws-add__loading"><LoadingSpinner size="sm" /></div>
          ) : libraryPapers.length === 0 ? (
            <p className="ws-add__empty">论文库里的论文都已在这个工作区里了。</p>
          ) : (
            <div className="er-scroll ws-add__scroll">
              <div className="er-ledger">
                {libraryPapers.map((paper, i) => (
                  <div className="er-ledger__row" key={paper.id}>
                    <span className="er-ledger__index" aria-hidden="true">{String(i + 1).padStart(2, '0')}</span>
                    <span className="er-ledger__body">
                      <span className="er-ledger__title"><span>{paper.title || paper.filename}</span></span>
                      {paper.venue && (
                        <span className="er-ledger__meta">
                          <span>{paper.venue}</span>
                          {paper.year && <span>{paper.year}</span>}
                        </span>
                      )}
                    </span>
                    <span className="er-ledger__aside">
                      <button
                        type="button"
                        onClick={() => handleAddPaper(paper.id)}
                        className="er-btn er-btn--secondary er-btn--sm"
                      >
                        添加
                      </button>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      {/* 本地论文 (L1) */}
      {l1Papers.length === 0 && discoveredPapers.length === 0 ? (
        <div className="er-empty">
          <span className="er-empty__mark" aria-hidden="true">00</span>
          <div className="er-empty__body">
            <p className="er-empty__title">这个集合还是空的</p>
            <p className="er-empty__text">
              从论文库里挑几篇同方向的论文放进来。集合里有两篇以上，深度研究才有关系可建。
            </p>
            <button type="button" onClick={handleShowAddPaper} className="er-btn er-btn--primary">
              从论文库添加
            </button>
          </div>
        </div>
      ) : (
        <>
          {l1Papers.length > 0 && (
            <section aria-label="本地论文">
              <div className="ws-group-head">
                <h2 className="ws-group-head__title">本地论文</h2>
                <span className="er-tag er-tag--mute">{l1Papers.length}</span>
              </div>
              <div className="er-ledger">
                {l1Papers.map((paper, i) => (
                  <PaperCard
                    key={paper.id}
                    paper={paper}
                    index={i}
                    workspaceId={workspaceId}
                    variant="local"
                    onNavigate={() => navigate(`/reader/${paper.id}`, { state: { from: `/workspace/${workspaceId}` } })}
                    onRequestRemove={() => setSingleRemoveConfirm(() => () => handleRemovePaper(paper.id))}
                  />
                ))}
              </div>
            </section>
          )}

          {/* 深度研究发现 (L2+) */}
          {discoveredPapers.length > 0 && (
            <section className="ws-discovered" aria-label="深度研究发现的论文">
              <div className="ws-group-head">
                <h2 className="ws-group-head__title">深度研究发现</h2>
                <span className="er-tag er-tag--mute">{discoveredPapers.length}</span>
                <div className="ws-group-head__tools">
                  {selectedDiscovered.size > 0 ? (
                    <>
                      <span className="ws-group-head__selection">已选 {selectedDiscovered.size} 篇</span>
                      <button
                        type="button"
                        onClick={handleBatchPromote}
                        disabled={batchLoading}
                        className="er-btn er-btn--secondary er-btn--sm"
                      >
                        {batchLoading ? '移入中…' : '批量移入'}
                      </button>
                      <button
                        type="button"
                        onClick={handleBatchRemove}
                        disabled={batchLoading}
                        className="er-btn er-btn--ghost er-btn--destructive er-btn--sm"
                      >
                        {batchLoading ? '移除中…' : '批量移除'}
                      </button>
                      <button
                        type="button"
                        onClick={deselectAllDiscovered}
                        className="er-btn er-btn--ghost er-btn--sm"
                      >
                        取消选择
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      onClick={selectAllDiscovered}
                      className="er-btn er-btn--ghost er-btn--sm"
                    >
                      全选
                    </button>
                  )}
                </div>
              </div>
              <p className="ws-discovered__note">
                这些论文已索引、可参与跨论文检索。「移入工作区」会把它升级为本地正式论文。
              </p>
              <div className="er-ledger">
                {discoveredPapers.map((paper, i) => (
                  <PaperCard
                    key={paper.id}
                    paper={paper}
                    index={i}
                    workspaceId={workspaceId}
                    variant="discovered"
                    selected={selectedDiscovered.has(paper.id)}
                    onToggleSelect={() => toggleDiscovered(paper.id)}
                    onNavigate={() => navigate(`/reader/${paper.id}`, { state: { from: `/workspace/${workspaceId}` } })}
                    onPromote={() => handlePromote(paper.id)}
                    onRequestRemove={() => setSingleRemoveConfirm(() => () => handleRemovePaper(paper.id))}
                  />
                ))}
              </div>
            </section>
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
  index: number
  workspaceId: number
  variant: 'local' | 'discovered'
  selected?: boolean
  onToggleSelect?: () => void
  onNavigate: () => void
  onPromote?: () => void
  onRequestRemove: () => void
}

const PAPER_STATUS: Record<string, { text: string; tone: string }> = {
  done: { text: '已解析', tone: '' },
  indexed: { text: '已索引 · 待解析', tone: 'er-tag--warn' },
  failed: { text: '解析失败', tone: 'er-tag--signal' },
}

function PaperCard({
  paper, index, workspaceId, variant, selected, onToggleSelect, onNavigate, onPromote, onRequestRemove,
}: PaperCardProps) {
  const isDiscovered = variant === 'discovered'
  const status = PAPER_STATUS[paper.status] ?? { text: '待解析', tone: 'er-tag--mute' }

  return (
    <div
      className="er-ledger__row ws-paper"
      data-discovered={isDiscovered || undefined}
      data-active={selected || undefined}
      role="button"
      tabIndex={0}
      onClick={onNavigate}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onNavigate() } }}
    >
      {isDiscovered && onToggleSelect ? (
        <label className="ws-paper__select" onClick={e => e.stopPropagation()}>
          <input
            type="checkbox"
            checked={!!selected}
            onChange={onToggleSelect}
            onKeyDown={stopRowActivation}
            aria-label={`选择 ${paper.title || paper.filename}`}
          />
          <i aria-hidden="true" />
        </label>
      ) : (
        <span className="er-ledger__index" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
      )}

      <span className="er-ledger__body">
        <span className="er-ledger__title"><span>{paper.title || paper.filename}</span></span>
        <span className="er-ledger__meta">
          {paper.venue && <span>{paper.venue}{paper.year ? ` ${paper.year}` : ''}</span>}
          {isDiscovered && <span>L{paper.level} 扩展</span>}
        </span>
        <span className="ws-paper__links">
          <Link
            to={`/reader/${paper.id}`}
            state={{ from: `/workspace/${workspaceId}` }}
            onClick={e => e.stopPropagation()}
            onKeyDown={stopRowActivation}
          >
            论文分析
          </Link>
          {paper.code_url && (
            <Link
              to={`/code/${paper.id}`}
              state={{ from: `/workspace/${workspaceId}` }}
              onClick={e => e.stopPropagation()}
              onKeyDown={stopRowActivation}
            >
              源码分析
            </Link>
          )}
        </span>
      </span>

      <span className="er-ledger__aside">
        <span className={`er-tag ${status.tone}`}>{status.text}</span>
        {isDiscovered && onPromote && (
          <button
            type="button"
            onClick={e => { e.stopPropagation(); onPromote() }}
            onKeyDown={stopRowActivation}
            className="er-btn er-btn--secondary er-btn--sm"
            title="升级为本地正式论文"
          >
            移入工作区
          </button>
        )}
        <button
          type="button"
          onClick={e => { e.stopPropagation(); onRequestRemove() }}
          onKeyDown={stopRowActivation}
          className="er-btn er-btn--ghost er-btn--destructive er-btn--sm er-ledger__action"
        >
          移除
        </button>
      </span>
    </div>
  )
}
