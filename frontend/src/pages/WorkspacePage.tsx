/**
 * WorkspacePage — 工作区列表
 *
 * 不对称标题 + surface-lifted 卡片 + 药丸创建按钮。
 */

import { useState, useEffect, useCallback, type CSSProperties } from 'react'
import { useNavigate } from 'react-router-dom'
import { listWorkspaces, createWorkspace, deleteWorkspace, type WorkspaceItem } from '@/api/workspaces'
import { toast } from '@/hooks/useToast'
import { ConfirmDialog, LoadingSpinner } from '@/ds-entry'


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
      .catch((e: unknown) => { const message = e instanceof Error ? e.message : '加载工作区列表失败'; toast({ type: 'error', message }) })
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
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : '创建工作区失败'
      toast({ type: 'error', message })
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
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : '删除工作区失败'
      toast({ type: 'error', message })
    } finally {
      setDeleteTarget(null)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <LoadingSpinner />
      </div>
    )
  }

  return (
    <div className="max-w-5xl mx-auto px-8 py-10">
      {/* 标题 + 统计 */}
      <div className="mb-8 flex items-end justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-display text-[30px] mb-1.5" style={{ color: 'var(--text-primary)' }}>
            工作区
          </h1>
          <p className="text-[13px]" style={{ color: 'var(--text-muted)' }}>
            将论文组织在一起进行分析
          </p>
        </div>
        {workspaces.length > 0 && (
          <div className="flex items-center gap-5">
            <div className="text-right">
              <div className="text-display text-[22px] leading-none tabular-nums" style={{ color: 'var(--text-primary)' }}>{workspaces.length}</div>
              <div className="text-[11px] mt-1" style={{ color: 'var(--text-muted)' }}>个工作区</div>
            </div>
            <div className="w-px h-8" style={{ backgroundColor: 'var(--border)' }} />
            <div className="text-right">
              <div className="text-display text-[22px] leading-none tabular-nums" style={{ color: 'var(--text-primary)' }}>
                {workspaces.reduce((n, w) => n + (w.paper_count || 0), 0)}
              </div>
              <div className="text-[11px] mt-1" style={{ color: 'var(--text-muted)' }}>篇藏书</div>
            </div>
          </div>
        )}
      </div>

      {/* 新建工作区 */}
      <div className="flex items-center gap-2.5 mb-8">
        <input
          type="text"
          value={newName}
          onChange={e => setNewName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleCreate()}
          placeholder="输入工作区名称..."
          className="flex-1 px-4 py-2.5 text-[13px] transition-colors duration-200"
          style={{
            borderRadius: 'var(--seed-radius, 12px)',
            backgroundColor: 'var(--bg-elevated)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
            outline: 'none',
          }}
        />
        <button
          onClick={handleCreate}
          disabled={creating}
          className="px-5 py-2.5 text-[13px] font-medium rounded-full disabled:opacity-50 transition-all duration-200 shrink-0 btn-glow sheen"
          style={{
            backgroundColor: 'var(--accent)',
            color: '#fff',
            transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
          }}
        >
          {creating ? '创建中...' : '新建工作区'}
        </button>
      </div>

      {/* 工作区列表 */}
      {workspaces.length === 0 ? (
        <div className="text-center py-16">
          <div
            className="w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-4"
            style={{
              backgroundColor: 'var(--bg-surface)',
              border: '1px solid var(--border)',
            }}
          >
            <svg width="24" height="24" fill="none" stroke="currentColor" viewBox="0 0 24 24"
              strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round"
              style={{ color: 'var(--text-muted)' }}>
              <rect x="2" y="7" width="20" height="14" rx="2" />
              <path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2" />
            </svg>
          </div>
          <p className="text-[13px]" style={{ color: 'var(--text-muted)' }}>暂无工作区</p>
          <p className="text-[12px] mt-1" style={{ color: 'var(--text-muted)', opacity: 0.7 }}>
            创建一个工作区，开始组织你的论文
          </p>
        </div>
      ) : (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3 stagger">
          {workspaces.map((ws, i) => (
            <div
              key={ws.id}
              role="button"
              tabIndex={0}
              onClick={() => navigate(`/workspace/${ws.id}`)}
              onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate(`/workspace/${ws.id}`) } }}
              className="relative p-5 cursor-pointer card-hover press spotlight focus-glow transition-all group flex flex-col gap-4"
              style={{
                backgroundColor: 'var(--bg-surface)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--seed-radius, 12px)',
                '--i': i,
              } as CSSProperties}
            >
              <div className="flex items-start justify-between">
                <span
                  className="flex items-center justify-center w-11 h-11 rounded-2xl text-[16px] font-semibold"
                  style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }}
                >
                  {(ws.name.trim()[0] || '·').toUpperCase()}
                </span>
                <button
                  onClick={(e) => handleDelete(ws.id, e)}
                  className="opacity-0 group-hover:opacity-100 px-2.5 py-1 text-[11px] rounded-full transition-all duration-200"
                  style={{ color: 'var(--text-muted)', border: '1px solid var(--border)' }}
                >
                  删除
                </button>
              </div>
              <div>
                <h3 className="text-[15px] font-medium truncate" style={{ color: 'var(--text-primary)', letterSpacing: '-0.01em' }}>
                  {ws.name}
                </h3>
                <p className="text-[12px] mt-1.5" style={{ color: 'var(--text-muted)' }}>
                  更新于 {new Date(ws.updated_at).toLocaleDateString('zh-CN')}
                </p>
              </div>
              <div className="flex items-center">
                <span className="text-[11px] px-2.5 py-1 rounded-full" style={{ backgroundColor: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}>
                  {ws.paper_count} 篇论文
                </span>
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
