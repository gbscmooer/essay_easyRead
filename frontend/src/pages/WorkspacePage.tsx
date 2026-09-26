/**
 * WorkspacePage — 工作区列表
 *
 * 不对称标题 + surface-lifted 卡片 + 药丸创建按钮。
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { listWorkspaces, createWorkspace, deleteWorkspace, type WorkspaceItem } from '@/api/workspaces'
import { toast } from '@/hooks/useToast'
import { ConfirmDialog, LoadingSpinner } from '@/ds-entry'
import { stopRowActivation } from '@/utils/keyboard'
import { parseServerDate } from '@/utils/datetime'
import '@/styles/workspace.css'


/** 相对时间展示（纯展示，无副作用）；解析失败时回退到本地日期。 */
function formatRelativeTime(iso: string): string {
  const t = parseServerDate(iso).getTime()
  if (Number.isNaN(t)) return ''
  const diff = Date.now() - t
  if (diff < 0) return '刚刚'
  const min = Math.floor(diff / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr} 小时前`
  const day = Math.floor(hr / 24)
  if (day < 7) return `${day} 天前`
  return new Date(t).toLocaleDateString('zh-CN')
}


export default function WorkspacePage() {
  const navigate = useNavigate()
  const [workspaces, setWorkspaces] = useState<WorkspaceItem[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null)
  const createInputRef = useRef<HTMLInputElement>(null)

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

  const totalPapers = workspaces.reduce((n, w) => n + (w.paper_count || 0), 0)

  return (
    <div className="er-page er-page--workspace">
      <header className="er-page-head">
        <p className="er-page-head__eyebrow">研究工作区 · 论文主题集合</p>
        <h1 className="er-page-head__title">工作区</h1>
      </header>

      <div className="er-page-head__readouts">
        <span className="er-readout"><b>{workspaces.length}</b>个工作区</span>
        <span className="er-readout"><b>{totalPapers}</b>篇藏书</span>
        <span className="ws-page__tagline">把论文按研究主题组织起来，深度研究在集合上建图谱</span>
      </div>

      {/* 新建：输入框与提交键合成一个单元，边框由外层给，内部不再各画一圈 */}
      <div className="ws-create">
        <input
          ref={createInputRef}
          type="text"
          value={newName}
          onChange={e => setNewName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleCreate()}
          placeholder="输入工作区名称，回车创建"
          aria-label="工作区名称"
          className="ws-create__input"
        />
        <button
          onClick={handleCreate}
          disabled={creating}
          aria-busy={creating}
          className="er-btn er-btn--primary ws-create__submit"
        >
          {creating && <LoadingSpinner size="xs" />}
          {creating ? '创建中' : '新建工作区'}
        </button>
      </div>

      {loading ? (
        <div className="ws-page__loading" role="status" aria-label="正在加载工作区">
          <LoadingSpinner size="lg" />
        </div>
      ) : workspaces.length === 0 ? (
        <div className="er-empty">
          <span className="er-empty__mark" aria-hidden="true">00</span>
          <div className="er-empty__body">
            <p className="er-empty__title">还没有工作区</p>
            <p className="er-empty__text">
              工作区是深度研究的输入：把同一方向的论文放进一个集合，才能在它们之间建立引文图谱与关联度。
            </p>
            <button
              type="button"
              onClick={() => createInputRef.current?.focus()}
              className="er-btn er-btn--primary"
            >
              命名第一个工作区
            </button>
          </div>
        </div>
      ) : (
        <div className="er-ledger ws-ledger stagger">
          {workspaces.map((ws, i) => (
            <div
              key={ws.id}
              role="button"
              tabIndex={0}
              onClick={() => navigate(`/workspace/${ws.id}`)}
              onKeyDown={e => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate(`/workspace/${ws.id}`) }
              }}
              className="er-ledger__row"
            >
              <span className="er-ledger__index" aria-hidden="true">{String(i + 1).padStart(2, '0')}</span>
              <span className="er-ledger__body">
                <span className="er-ledger__title"><span>{ws.name}</span></span>
                <span className="er-ledger__meta">
                  <span>更新于 {formatRelativeTime(ws.updated_at)}</span>
                </span>
              </span>
              <span className="er-ledger__aside">
                <span className="ws-ledger__count">
                  <b>{ws.paper_count}</b>
                  <small>篇</small>
                </span>
                <button
                  onClick={e => handleDelete(ws.id, e)}
                  onKeyDown={stopRowActivation}
                  aria-label={`删除工作区 ${ws.name}`}
                  className="er-btn er-btn--ghost er-btn--destructive er-btn--sm er-ledger__action"
                >
                  删除
                </button>
              </span>
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
