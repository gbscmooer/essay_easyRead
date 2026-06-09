import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { listWorkspaces, createWorkspace, deleteWorkspace, type WorkspaceItem } from '@/api/workspaces'
import { toast } from '@/hooks/useToast'
import { ConfirmDialog } from '@/components/ConfirmDialog'


export default function WorkspacePage() {
  const navigate = useNavigate()
  const [workspaces, setWorkspaces] = useState<WorkspaceItem[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null)

  const refresh = useCallback(() => {
    listWorkspaces()
      .then(setWorkspaces)
      .catch((e: any) => { toast({ type: 'error', message: e?.message || '加载工作区列表失败' }) })
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
    } catch (e: any) {
      toast({ type: 'error', message: e?.message || '创建工作区失败' })
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = (id: number, e: React.MouseEvent) => {
    e.stopPropagation()
    setDeleteTarget(id)
  }

  const confirmDelete = async () => {
    if (deleteTarget === null) return
    try {
      await deleteWorkspace(deleteTarget)
      refresh()
    } catch (e: any) {
      toast({ type: 'error', message: e?.message || '删除工作区失败' })
    } finally {
      setDeleteTarget(null)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-6 h-6 border-2 rounded-full animate-spin" style={{ borderColor: 'var(--border)', borderTopColor: 'var(--accent)' }} />
      </div>
    )
  }

  return (
    <div className="max-w-3xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold" style={{ color: 'var(--text-primary)' }}>工作区</h1>
          <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>管理工作区，将论文组织在一起进行分析</p>
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
          className="flex-1 px-3 py-2 text-sm rounded-lg focus:outline-none"
        />
        <button
          onClick={handleCreate}
          disabled={creating}
          className="px-4 py-2 text-sm font-medium rounded-lg disabled:opacity-50 transition-colors"
          style={{ backgroundColor: 'var(--accent)', color: 'var(--bg-base)' }}
        >
          {creating ? '创建中...' : '新建工作区'}
        </button>
      </div>

      {/* 工作区列表 */}
      {workspaces.length === 0 ? (
        <div className="text-center py-12">
          <p style={{ color: 'var(--text-muted)' }}>暂无工作区</p>
          <p className="text-xs mt-1" style={{ color: 'var(--text-muted)', opacity: 0.7 }}>创建一个工作区，开始组织你的论文</p>
        </div>
      ) : (
        <div className="space-y-2">
          {workspaces.map(ws => (
            <div
              key={ws.id}
              onClick={() => navigate(`/workspace/${ws.id}`)}
              className="rounded-xl p-4 cursor-pointer transition-all group"
              style={{
                backgroundColor: 'var(--bg-surface)',
                border: '1px solid var(--border)',
              }}
              onMouseEnter={e => {
                const el = e.currentTarget
                el.style.borderColor = 'var(--border-light)'
                el.style.boxShadow = '0 1px 4px rgba(0,0,0,0.05)'
              }}
              onMouseLeave={e => {
                const el = e.currentTarget
                el.style.borderColor = 'var(--border)'
                el.style.boxShadow = 'none'
              }}
            >
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-medium transition-colors" style={{ color: 'var(--text-primary)' }}>
                    {ws.name}
                  </h3>
                  <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
                    {ws.paper_count} 篇论文
                  </p>
                </div>
                <button
                  onClick={(e) => handleDelete(ws.id, e)}
                  className="opacity-0 group-hover:opacity-100 px-2 py-1 text-xs transition-all"
                  style={{ color: 'var(--text-muted)' }}
                >
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      <ConfirmDialog
        open={deleteTarget !== null}
        title="确认删除"
        message="确定删除此工作区？"
        variant="danger"
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}
