import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { getWorkspace, updateWorkspace, addPaperToWorkspace, removePaperFromWorkspace, promotePaper, type WorkspaceDetail, type WorkspacePaper } from '@/api/workspaces'
import { listPapers } from '@/api/papers'
import type { Paper } from '@/types'
import { toast } from '@/hooks/useToast'
import { LoadingSpinner } from '@/components/LoadingSpinner'
import { ConfirmDialog } from '@/components/ConfirmDialog'


export default function WorkspaceDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const workspaceId = Number(id)

  const [workspace, setWorkspace] = useState<WorkspaceDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState(false)
  const [editName, setEditName] = useState('')
  const [showAddPaper, setShowAddPaper] = useState(false)
  const [libraryPapers, setLibraryPapers] = useState<Paper[]>([])
  const [loadingPapers, setLoadingPapers] = useState(false)
  const [selectedDiscovered, setSelectedDiscovered] = useState<Set<number>>(new Set())
  const [batchLoading, setBatchLoading] = useState(false)
  const [batchRemoveConfirm, setBatchRemoveConfirm] = useState(false)
  const [singleRemoveConfirm, setSingleRemoveConfirm] = useState<(() => void) | null>(null)

  const refresh = useCallback(() => {
    if (!workspaceId) return
    getWorkspace(workspaceId)
      .then(ws => { setWorkspace(ws); setEditName(ws.name) })
      .catch(() => navigate('/workspace'))
      .finally(() => setLoading(false))
  }, [workspaceId, navigate])

  useEffect(() => { refresh() }, [refresh])

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
      // 过滤掉已在工作区中的论文
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
      // allSettled：部分失败时已成功的不回滚，仍要刷新列表 + 如实汇报失败数
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
    <div className="max-w-3xl mx-auto px-6 py-8">
      {/* 返回按钮 */}
      <Link
        to="/workspace"
        className="inline-flex items-center gap-1.5 text-sm transition-colors mb-4"
        style={{ color: 'var(--text-muted)' }}
        onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)' }}
        onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)' }}
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
        </svg>
        返回工作区
      </Link>

      {/* 标题 */}
      <div className="mb-6">
        {editing ? (
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={editName}
              onChange={e => setEditName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleRename()}
              className="flex-1 px-3 py-1.5 text-lg rounded-lg focus:outline-none"
              style={{
                backgroundColor: 'var(--bg-elevated)',
                border: '1px solid var(--border)',
                color: 'var(--text-primary)',
              }}
              autoFocus
            />
            <button onClick={handleRename} className="px-3 py-1.5 text-sm rounded-lg transition-colors" style={{ backgroundColor: 'var(--accent)', color: 'var(--bg-base)' }}>保存</button>
            <button onClick={() => { setEditing(false); setEditName(workspace.name) }} className="px-3 py-1.5 text-sm transition-colors" style={{ color: 'var(--text-muted)' }}>取消</button>
          </div>
        ) : (
          <div className="flex items-center gap-3">
            <h1
              className="text-xl font-bold cursor-pointer transition-colors"
              style={{ color: 'var(--text-primary)' }}
              role="button"
              tabIndex={0}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--accent)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-primary)' }}
              onClick={() => setEditing(true)}
              onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setEditing(true) } }}
            >
              {workspace.name}
            </h1>
            <button
              onClick={() => setEditing(true)}
              className="text-xs transition-colors"
              style={{ color: 'var(--text-muted)' }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)' }}
            >
              重命名
            </button>
          </div>
        )}
        <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
          {l1Papers.length} 篇本地论文
          {discoveredPapers.length > 0 && ` · ${discoveredPapers.length} 篇深度研究发现`}
        </p>
      </div>

      {/* 添加论文 + 深度研究入口 */}
      <div className="flex items-center gap-3 mb-4">
        <button
          onClick={handleShowAddPaper}
          className="px-4 py-2 text-sm rounded-lg transition-colors shrink-0"
          style={{
            backgroundColor: 'var(--bg-elevated)',
            border: '1px solid var(--border)',
            color: 'var(--text-secondary)',
          }}
        >
          + 从论文库添加
        </button>

        <button
          onClick={() => navigate(`/workspace/${workspaceId}/research`)}
          disabled={workspace.papers.length === 0}
          className="flex-1 group relative overflow-hidden px-6 py-3 rounded-xl border-2 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
          style={{
            borderColor: 'var(--accent)',
            background: 'linear-gradient(135deg, var(--accent-subtle), transparent)',
            color: 'var(--accent)',
          }}
          onMouseEnter={e => {
            if (workspace.papers.length === 0) return
            e.currentTarget.style.boxShadow = '0 4px 16px rgba(0,0,0,0.12)'
            e.currentTarget.style.transform = 'translateY(-1px)'
          }}
          onMouseLeave={e => {
            e.currentTarget.style.boxShadow = 'none'
            e.currentTarget.style.transform = 'none'
          }}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="text-2xl">⚡</span>
              <div className="text-left">
                <div className="text-base font-semibold leading-tight">深度研究</div>
                <div className="text-[11px] mt-0.5 opacity-80">
                  跨论文构建知识图谱 · RAG 问答
                </div>
              </div>
            </div>
            <span className="text-xl opacity-60 group-hover:translate-x-1 transition-transform">→</span>
          </div>
        </button>
      </div>

      {/* 添加论文面板 */}
      {showAddPaper && (
        <div className="mb-6 rounded-lg p-4" style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)' }}>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium" style={{ color: 'var(--text-secondary)' }}>选择论文添加到工作区</h3>
            <button onClick={() => setShowAddPaper(false)} className="text-xs" style={{ color: 'var(--text-muted)' }} onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)' }} onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)' }}>关闭</button>
          </div>
          {loadingPapers ? (
            <div className="flex justify-center py-4">
              <LoadingSpinner size="sm" />
            </div>
          ) : libraryPapers.length === 0 ? (
            <p className="text-xs text-center py-4" style={{ color: 'var(--text-muted)' }}>没有可添加的论文</p>
          ) : (
            <div className="max-h-60 overflow-auto space-y-2">
              {libraryPapers.map(paper => (
                <div key={paper.id} className="flex items-center justify-between px-3 py-2 rounded transition-colors" style={{ backgroundColor: 'var(--bg-surface)' }} onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--bg-elevated)' }} onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'var(--bg-surface)' }}>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm truncate" style={{ color: 'var(--text-secondary)' }}>{paper.title || paper.filename}</p>
                    {paper.venue && <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{paper.venue} {paper.year || ''}</p>}
                  </div>
                  <button
                    onClick={() => handleAddPaper(paper.id)}
                    className="ml-3 px-2 py-1 text-xs rounded transition-colors shrink-0"
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
        <div className="text-center py-12">
          <p style={{ color: 'var(--text-muted)' }}>工作区暂无论文</p>
          <p className="text-xs mt-1 mb-4" style={{ color: 'var(--text-muted)', opacity: 0.7 }}>点击"从论文库添加"将论文加入工作区</p>
          <button
            onClick={handleShowAddPaper}
            className="px-4 py-2 text-sm rounded-lg transition-colors"
            style={{ backgroundColor: 'var(--accent)', color: 'var(--bg-base)' }}
          >
            + 从论文库添加
          </button>
        </div>
      ) : (
        <>
          {l1Papers.length > 0 && (
            <div className="space-y-3">
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
                <div className="flex items-center gap-2">
                  <h2 className="text-sm font-semibold" style={{ color: 'var(--text-secondary)' }}>
                    通过深度研究搜索到的相关论文
                  </h2>
                  <span className="text-[10px] px-1.5 py-0.5 rounded-full" style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }}>
                    {discoveredPapers.length}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  {selectedDiscovered.size > 0 ? (
                    <>
                      <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                        已选 {selectedDiscovered.size} 篇
                      </span>
                      <button
                        onClick={handleBatchPromote}
                        disabled={batchLoading}
                        className="px-2.5 py-1 text-xs rounded-md transition-colors disabled:opacity-50"
                        style={{ backgroundColor: 'var(--accent)', color: 'var(--bg-base)' }}
                      >
                        {batchLoading ? '移入中…' : '批量移入工作区'}
                      </button>
                      <button
                        onClick={handleBatchRemove}
                        disabled={batchLoading}
                        className="px-2.5 py-1 text-xs rounded-md border transition-colors disabled:opacity-50"
                        style={{ borderColor: 'var(--error)', color: 'var(--error)' }}
                      >
                        {batchLoading ? '移除中…' : '批量移除'}
                      </button>
                      <button
                        onClick={deselectAllDiscovered}
                        className="text-xs transition-colors"
                        style={{ color: 'var(--text-muted)' }}
                      >
                        取消选择
                      </button>
                    </>
                  ) : (
                    <button
                      onClick={selectAllDiscovered}
                      className="text-xs transition-colors"
                      style={{ color: 'var(--text-muted)' }}
                    >
                      全选
                    </button>
                  )}
                </div>
              </div>
              <p className="text-xs mb-3" style={{ color: 'var(--text-muted)' }}>
                这些论文已索引、可参与跨论文检索；点击"移入工作区"可将其升级为本地正式论文（参与下一轮研究的 seed）。
              </p>
              <div className="space-y-3">
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
      className="rounded-lg p-4 group transition-all cursor-pointer border"
      style={{
        backgroundColor: isDiscovered ? 'transparent' : 'var(--bg-surface)',
        borderColor: selected ? 'var(--accent)' : 'var(--border)',
        borderStyle: isDiscovered ? 'dashed' : 'solid',
        boxShadow: selected ? '0 0 0 1px var(--accent)' : 'none',
      }}
      role="button"
      tabIndex={0}
      onClick={onNavigate}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onNavigate() } }}
      onMouseEnter={e => {
        if (!selected) e.currentTarget.style.borderColor = 'var(--border-light)'
        e.currentTarget.style.boxShadow = selected ? '0 0 0 1px var(--accent)' : '0 2px 8px rgba(0,0,0,0.08)'
      }}
      onMouseLeave={e => {
        if (!selected) e.currentTarget.style.borderColor = 'var(--border)'
        e.currentTarget.style.boxShadow = selected ? '0 0 0 1px var(--accent)' : 'none'
      }}
    >
      <div className="flex items-start justify-between">
        {/* Checkbox for discovered papers */}
        {isDiscovered && onToggleSelect && (
          <div
            className="shrink-0 mr-3 mt-0.5"
            onClick={e => { e.stopPropagation(); onToggleSelect() }}
          >
            <div
              className="w-4 h-4 rounded border flex items-center justify-center transition-colors"
              style={{
                borderColor: selected ? 'var(--accent)' : 'var(--border)',
                backgroundColor: selected ? 'var(--accent)' : 'transparent',
              }}
            >
              {selected && (
                <svg className="w-3 h-3" fill="none" stroke="var(--bg-base)" viewBox="0 0 24 24" strokeWidth={3}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
              )}
            </div>
          </div>
        )}
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium transition-colors" style={{ color: 'var(--text-primary)' }}>
            {paper.title || paper.filename}
          </p>
          <div className="flex items-center gap-3 mt-1 flex-wrap">
            {paper.venue && <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{paper.venue} {paper.year || ''}</span>}
            <span className={`text-xs px-1.5 py-0.5 rounded ${
              paper.status === 'done' ? 'bg-green-500/10 text-green-400' :
              paper.status === 'indexed' ? 'bg-amber-500/10 text-amber-400' :
              paper.status === 'failed' ? 'bg-red-500/10 text-red-400' :
              'var(--bg-surface)'
            }`}>
              {paper.status === 'done' ? '已解析' :
               paper.status === 'indexed' ? '未解析·已索引' :
               paper.status === 'failed' ? '失败' : '待解析'}
            </span>
            {isDiscovered && (
              <span
                className="text-[10px] px-1.5 py-0.5 rounded border"
                style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
                title="联网扩展下载的论文（未经 LLM 深度解析）"
              >
                L{paper.level} · 扩展
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 mt-2">
            <Link
              to={`/reader/${paper.id}`}
              state={{ from: `/workspace/${workspaceId}` }}
              onClick={e => e.stopPropagation()}
              className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
            >
              论文分析
            </Link>
            {paper.code_url && (
              <Link
                to={`/code/${paper.id}`}
                state={{ from: `/workspace/${workspaceId}` }}
                onClick={e => e.stopPropagation()}
                className="text-xs transition-colors"
              style={{ color: 'var(--text-muted)' }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)' }}
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
              className="px-2.5 py-1 text-xs rounded-md border transition-colors"
              style={{ borderColor: 'var(--accent)', color: 'var(--accent)', backgroundColor: 'var(--accent-subtle)' }}
              title="把这篇论文升级为本地正式论文：下一次研究会把它当作 seed 进行扩展"
            >
              移入工作区
            </button>
          )}
          <button
            onClick={(e) => {
              e.stopPropagation()
              onRequestRemove()
            }}
            className="opacity-0 group-hover:opacity-100 focus:opacity-100 px-2 py-1 text-xs transition-all"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--error)' }}
            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)' }}
          >
            移除
          </button>
        </div>
      </div>
    </div>
  )
}
