import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { listWorkspaces, createWorkspace, deleteWorkspace, type WorkspaceItem } from '@/api/workspaces'


export function WorkspacePage() {
  const navigate = useNavigate()
  const [workspaces, setWorkspaces] = useState<WorkspaceItem[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')

  const refresh = useCallback(() => {
    listWorkspaces()
      .then(setWorkspaces)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const handleCreate = async () => {
    const name = newName.trim() || '未命名工作区'
    setCreating(true)
    try {
      const ws = await createWorkspace(name)
      setNewName('')
      navigate(`/workspace/${ws.id}`)
    } catch {
      // ignore
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = async (id: number, e: React.MouseEvent) => {
    e.stopPropagation()
    if (!confirm('确定删除此工作区？')) return
    try {
      await deleteWorkspace(id)
      refresh()
    } catch {
      // ignore
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-6 h-6 border-2 border-gray-600 border-t-blue-500 rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="max-w-3xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-gray-100">工作区</h1>
          <p className="text-sm text-gray-500 mt-1">管理工作区，将论文组织在一起进行分析</p>
        </div>
      </div>

      {/* 新建工作区 */}
      <div className="flex items-center gap-2 mb-6">
        <input
          type="text"
          value={newName}
          onChange={e => setNewName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleCreate()}
          placeholder="输入工作区名称..."
          className="flex-1 px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 transition-colors"
        />
        <button
          onClick={handleCreate}
          disabled={creating}
          className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-500 disabled:opacity-50 transition-colors"
        >
          {creating ? '创建中...' : '新建工作区'}
        </button>
      </div>

      {/* 工作区列表 */}
      {workspaces.length === 0 ? (
        <div className="text-center py-12">
          <p className="text-gray-500">暂无工作区</p>
          <p className="text-xs text-gray-600 mt-1">创建一个工作区，开始组织你的论文</p>
        </div>
      ) : (
        <div className="space-y-3">
          {workspaces.map(ws => (
            <div
              key={ws.id}
              onClick={() => navigate(`/workspace/${ws.id}`)}
              className="bg-gray-800/50 border border-gray-700/50 rounded-lg p-4 hover:border-gray-600 cursor-pointer transition-colors group"
            >
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-medium text-gray-200 group-hover:text-blue-400 transition-colors">
                    {ws.name}
                  </h3>
                  <p className="text-xs text-gray-500 mt-1">
                    {ws.paper_count} 篇论文
                  </p>
                </div>
                <button
                  onClick={(e) => handleDelete(ws.id, e)}
                  className="opacity-0 group-hover:opacity-100 px-2 py-1 text-xs text-gray-500 hover:text-red-400 transition-all"
                >
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
