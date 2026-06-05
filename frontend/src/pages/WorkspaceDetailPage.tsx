import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { getWorkspace, updateWorkspace, addPaperToWorkspace, removePaperFromWorkspace, type WorkspaceDetail } from '@/api/workspaces'
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

  if (loading || !workspace) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-6 h-6 border-2 border-gray-600 border-t-blue-500 rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="max-w-3xl mx-auto px-6 py-8">
      {/* 标题 */}
      <div className="mb-6">
        {editing ? (
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={editName}
              onChange={e => setEditName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleRename()}
              className="flex-1 px-3 py-1.5 text-lg bg-gray-800 border border-gray-700 rounded-lg text-gray-200 focus:outline-none focus:border-blue-500"
              autoFocus
            />
            <button onClick={handleRename} className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-500">保存</button>
            <button onClick={() => { setEditing(false); setEditName(workspace.name) }} className="px-3 py-1.5 text-sm text-gray-400 hover:text-gray-200">取消</button>
          </div>
        ) : (
          <div className="flex items-center gap-3">
            <h1
              className="text-xl font-bold text-gray-100 cursor-pointer hover:text-blue-400 transition-colors"
              onClick={() => setEditing(true)}
            >
              {workspace.name}
            </h1>
            <button
              onClick={() => setEditing(true)}
              className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
            >
              重命名
            </button>
          </div>
        )}
        <p className="text-sm text-gray-500 mt-1">{workspace.papers.length} 篇论文</p>
      </div>

      {/* 添加论文按钮 */}
      <div className="mb-4">
        <button
          onClick={handleShowAddPaper}
          className="px-4 py-2 text-sm bg-gray-800 border border-gray-700 text-gray-300 rounded-lg hover:border-gray-600 hover:text-gray-200 transition-colors"
        >
          + 从论文库添加
        </button>
      </div>

      {/* 添加论文面板 */}
      {showAddPaper && (
        <div className="mb-6 bg-gray-800/50 border border-gray-700/50 rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-gray-300">选择论文添加到工作区</h3>
            <button onClick={() => setShowAddPaper(false)} className="text-xs text-gray-500 hover:text-gray-300">关闭</button>
          </div>
          {loadingPapers ? (
            <div className="flex justify-center py-4">
              <div className="w-5 h-5 border-2 border-gray-600 border-t-blue-500 rounded-full animate-spin" />
            </div>
          ) : libraryPapers.length === 0 ? (
            <p className="text-xs text-gray-500 text-center py-4">没有可添加的论文</p>
          ) : (
            <div className="max-h-60 overflow-auto space-y-2">
              {libraryPapers.map(paper => (
                <div key={paper.id} className="flex items-center justify-between px-3 py-2 bg-gray-900/50 rounded hover:bg-gray-800 transition-colors">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-gray-300 truncate">{paper.title || paper.filename}</p>
                    {paper.venue && <p className="text-xs text-gray-500">{paper.venue} {paper.year || ''}</p>}
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

      {/* 论文列表 */}
      {workspace.papers.length === 0 ? (
        <div className="text-center py-12">
          <p className="text-gray-500">工作区暂无论文</p>
          <p className="text-xs text-gray-600 mt-1">点击"从论文库添加"将论文加入工作区</p>
        </div>
      ) : (
        <div className="space-y-3">
          {workspace.papers.map(paper => (
            <div
              key={paper.id}
              className="bg-gray-800/50 border border-gray-700/50 rounded-lg p-4 group transition-colors"
            >
              <div className="flex items-start justify-between">
                <div className="min-w-0 flex-1">
                  <Link
                    to={`/reader/${paper.id}`}
                    className="text-sm font-medium text-gray-200 hover:text-blue-400 transition-colors"
                  >
                    {paper.title || paper.filename}
                  </Link>
                  <div className="flex items-center gap-3 mt-1">
                    {paper.venue && <span className="text-xs text-gray-500">{paper.venue} {paper.year || ''}</span>}
                    <span className={`text-xs px-1.5 py-0.5 rounded ${
                      paper.status === 'done' ? 'bg-green-500/10 text-green-400' :
                      paper.status === 'failed' ? 'bg-red-500/10 text-red-400' :
                      'bg-gray-700 text-gray-400'
                    }`}>
                      {paper.status === 'done' ? '已解析' : paper.status === 'failed' ? '失败' : '待解析'}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 mt-2">
                    <Link
                      to={`/reader/${paper.id}`}
                      className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
                    >
                      论文分析
                    </Link>
                    {paper.code_url && (
                      <Link
                        to={`/code/${paper.id}`}
                        className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
                      >
                        源码分析
                      </Link>
                    )}
                  </div>
                </div>
                <button
                  onClick={() => handleRemovePaper(paper.id)}
                  className="opacity-0 group-hover:opacity-100 ml-3 px-2 py-1 text-xs text-gray-500 hover:text-red-400 transition-all shrink-0"
                >
                  移除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
