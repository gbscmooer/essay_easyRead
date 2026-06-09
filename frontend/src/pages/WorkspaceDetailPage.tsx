import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { getWorkspace, updateWorkspace, addPaperToWorkspace, removePaperFromWorkspace, promotePaper, type WorkspaceDetail, type WorkspacePaper } from '@/api/workspaces'
import { listPapers } from '@/api/papers'
import type { Paper } from '@/types'


export function WorkspaceDetailPage() {
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
      // ignore
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
    } catch {
      // ignore
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
      // ignore
    }
  }

  const handleRemovePaper = async (paperId: number) => {
    try {
      await removePaperFromWorkspace(workspaceId, paperId)
      refresh()
    } catch {
      // ignore
    }
  }

  const handlePromote = async (paperId: number) => {
    try {
      await promotePaper(workspaceId, paperId)
      refresh()
    } catch {
      // ignore
    }
  }

  const l1Papers = (workspace?.papers ?? []).filter(p => (p.level ?? 1) === 1)
  const discoveredPapers = (workspace?.papers ?? []).filter(p => (p.level ?? 1) >= 2)

  if (loading || !workspace) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-6 h-6 border-2 rounded-full animate-spin" style={{ borderColor: 'var(--border-light)', borderTopColor: 'var(--accent)' }} />
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
              style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)', color: 'var(--text-primary)' }}
              autoFocus
            />
            <button onClick={handleRename} className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-500">保存</button>
            <button onClick={() => { setEditing(false); setEditName(workspace.name) }} className="px-3 py-1.5 text-sm"
              style={{ color: 'var(--text-muted)' }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-primary)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)' }}>取消</button>
          </div>
        ) : (
          <div className="flex items-center gap-3">
            <h1
              className="text-xl font-bold cursor-pointer transition-colors"
              style={{ color: 'var(--text-primary)' }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--accent)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-primary)' }}
              onClick={() => setEditing(true)}
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
          style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)', color: 'var(--text-secondary)' }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--text-muted)'; (e.currentTarget as HTMLElement).style.color = 'var(--text-primary)' }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border-light)'; (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)' }}
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
              <div className="w-5 h-5 border-2 rounded-full animate-spin" style={{ borderColor: 'var(--border-light)', borderTopColor: 'var(--accent)' }} />
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
                    className="ml-3 px-2 py-1 text-xs bg-blue-600/20 text-blue-400 rounded hover:bg-blue-600/40 transition-colors shrink-0"
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
          <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>点击"从论文库添加"将论文加入工作区</p>
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
                />
              ))}
            </div>
          )}

          {/* 深度研究发现 (L2+) */}
          {discoveredPapers.length > 0 && (
            <div className="mt-8">
              <div className="flex items-center gap-2 mb-3">
                <h2 className="text-sm font-semibold" style={{ color: 'var(--text-secondary)' }}>
                  通过深度研究搜索到的相关论文
                </h2>
                <span className="text-[10px] px-1.5 py-0.5 rounded-full" style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }}>
                  {discoveredPapers.length}
                </span>
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
                    onNavigate={() => navigate(`/reader/${paper.id}`, { state: { from: `/workspace/${workspaceId}` } })}
                    onRemove={() => handleRemovePaper(paper.id)}
                    onPromote={() => handlePromote(paper.id)}
                  />
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}


interface PaperCardProps {
  paper: WorkspacePaper
  workspaceId: number
  variant: 'local' | 'discovered'
  onNavigate: () => void
  onRemove: () => void
  onPromote?: () => void
}

function PaperCard({ paper, workspaceId, variant, onNavigate, onRemove, onPromote }: PaperCardProps) {
  const isDiscovered = variant === 'discovered'
  return (
    <div
      className="rounded-lg p-4 group transition-colors cursor-pointer border"
      style={{
        backgroundColor: isDiscovered ? 'transparent' : 'var(--bg-surface)',
        borderColor: isDiscovered ? 'var(--border)' : 'var(--border)',
        borderStyle: isDiscovered ? 'dashed' : 'solid',
      }}
      onClick={onNavigate}
      onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--border-light)' }}
      onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)' }}
    >
      <div className="flex items-start justify-between">
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
            onClick={(e) => { e.stopPropagation(); onRemove() }}
            className="opacity-0 group-hover:opacity-100 px-2 py-1 text-xs transition-all" style={{ color: 'var(--text-muted)' }} onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--error)' }} onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)' }}
          >
            移除
          </button>
        </div>
      </div>
    </div>
  )
}
