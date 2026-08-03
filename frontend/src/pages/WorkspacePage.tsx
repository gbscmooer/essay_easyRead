/**
 * WorkspacePage — 工作区列表
 *
 * 不对称标题 + surface-lifted 卡片 + 药丸创建按钮。
 */

import { useState, useEffect, useCallback, useRef, type CSSProperties } from 'react'
import { useNavigate } from 'react-router-dom'
import { listWorkspaces, createWorkspace, deleteWorkspace, type WorkspaceItem } from '@/api/workspaces'
import { toast } from '@/hooks/useToast'
import { ConfirmDialog, LoadingSpinner } from '@/ds-entry'


/** 相对时间展示（纯展示，无副作用）；解析失败时回退到本地日期。 */
function formatRelativeTime(iso: string): string {
  const t = new Date(iso).getTime()
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

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <LoadingSpinner />
      </div>
    )
  }

  return (
    <div className="er-page er-page--workspace max-w-5xl mx-auto px-8 py-10">
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
          ref={createInputRef}
          type="text"
          value={newName}
          onChange={e => setNewName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleCreate()}
          placeholder="输入工作区名称..."
          aria-label="工作区名称"
          className="flex-1 px-4 py-2.5 text-[13px] transition-all duration-200 placeholder-gray-600"
          style={{
            borderRadius: 'var(--seed-radius, 12px)',
            backgroundColor: 'var(--bg-elevated)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
            outline: 'none',
            transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
          }}
          onFocus={e => {
            e.currentTarget.style.borderColor = 'var(--accent)'
            e.currentTarget.style.boxShadow = '0 0 0 3px var(--accent-subtle)'
          }}
          onBlur={e => {
            e.currentTarget.style.borderColor = 'var(--border)'
            e.currentTarget.style.boxShadow = 'none'
          }}
        />
        <button
          onClick={handleCreate}
          disabled={creating}
          aria-busy={creating}
          className="px-5 py-2.5 text-[13px] font-medium rounded-full disabled:opacity-60 disabled:cursor-not-allowed transition-all duration-200 shrink-0 btn-glow sheen press inline-flex items-center justify-center gap-2"
          style={{
            backgroundColor: 'var(--accent)',
            color: '#fff',
            transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
          }}
        >
          {creating && (
            <span
              className="w-3.5 h-3.5 border-2 rounded-full animate-spin"
              style={{ borderColor: 'rgba(255,255,255,0.4)', borderTopColor: '#fff' }}
              aria-hidden="true"
            />
          )}
          {creating ? '创建中…' : '新建工作区'}
        </button>
      </div>

      {/* 工作区列表 */}
      {workspaces.length === 0 ? (
        <div className="text-center py-16 animate-reveal-up">
          <div
            className="group/empty w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-4 animate-reveal-scale transition-transform duration-500 hover:scale-105"
            style={{
              backgroundColor: 'var(--bg-surface)',
              border: '1px solid var(--border)',
              transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
            }}
          >
            <svg width="24" height="24" fill="none" stroke="currentColor" viewBox="0 0 24 24"
              strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round"
              className="transition-transform duration-500 group-hover/empty:scale-110"
              style={{ color: 'var(--text-muted)', transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)' }}>
              <rect x="2" y="7" width="20" height="14" rx="2" />
              <path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2" />
            </svg>
          </div>
          <p className="text-[13px]" style={{ color: 'var(--text-primary)' }}>暂无工作区</p>
          <p className="text-[12px] mt-1 mb-5" style={{ color: 'var(--text-muted)' }}>
            创建一个工作区，开始组织你的论文
          </p>
          <button
            onClick={() => createInputRef.current?.focus()}
            className="px-5 py-2.5 text-[13px] font-medium rounded-full transition-all duration-200 btn-glow sheen press"
            style={{
              backgroundColor: 'var(--accent)',
              color: '#fff',
              transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
            }}
          >
            + 新建工作区
          </button>
        </div>
      ) : (
        <div className="stagger">
          {workspaces.map((ws, i) => (
            <div
              key={ws.id}
              role="button"
              tabIndex={0}
              onClick={() => navigate(`/workspace/${ws.id}`)}
              onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate(`/workspace/${ws.id}`) } }}
              className="relative list-row card-hover press focus-glow group flex items-center gap-4"
              style={{ '--i': i } as CSSProperties}
            >
              <span
                className="flex items-center justify-center w-10 h-10 rounded-md text-[15px] font-semibold shrink-0"
                style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }}
              >
                {(ws.name.trim()[0] || '·').toUpperCase()}
              </span>
              <div className="flex-1 min-w-0">
                <h3 className="text-[15px] font-medium truncate" style={{ color: 'var(--text-primary)', letterSpacing: '-0.01em' }}>
                  {ws.name}
                </h3>
                <p className="text-[12px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
                  更新于 {formatRelativeTime(ws.updated_at)} · {ws.paper_count} 篇论文
                </p>
              </div>
              <button
                onClick={(e) => handleDelete(ws.id, e)}
                aria-label={`删除工作区 ${ws.name}`}
                className="opacity-40 group-hover:opacity-100 focus-visible:opacity-100 px-2.5 py-1 text-[11px] rounded-md transition-all duration-200 press shrink-0"
                style={{ color: 'var(--text-muted)' }}
                onMouseEnter={e => { e.currentTarget.style.color = 'var(--error)' }}
                onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)' }}
              >
                删除
              </button>
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
