import { useState, useEffect, useCallback, useRef } from 'react'
import type { Tag } from '@/types'
import { listTags, getPaperTags, addPaperTag, removePaperTag, createTag } from '@/api/tags'
import { toast } from '@/hooks/useToast'

interface TagManagerProps {
  paperId: number
  analysisStatus?: 'idle' | 'extracting' | 'analyzing' | 'generating' | 'done' | 'failed'
}

export function TagManager({ paperId, analysisStatus }: TagManagerProps) {
  const [allTags, setAllTags] = useState<Tag[]>([])
  const [paperTags, setPaperTags] = useState<Tag[]>([])
  const [showDropdown, setShowDropdown] = useState(false)
  const [newTagName, setNewTagName] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const mountedRef = useRef(true)

  useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false } }, [])

  const fetchTags = useCallback(async () => {
    try {
      const [all, paper] = await Promise.all([listTags(), getPaperTags(paperId)])
      if (mountedRef.current) {
        setAllTags(all)
        setPaperTags(paper)
      }
    } catch (e: unknown) {
      if (!mountedRef.current) return
      const message = e instanceof Error ? e.message : '加载标签失败'
      toast({ type: 'error', message })
    }
  }, [paperId])

  useEffect(() => { fetchTags() }, [fetchTags])

  useEffect(() => {
    if (analysisStatus === 'done') {
      fetchTags()
    }
  }, [analysisStatus, fetchTags])


  // Auto-focus input when dropdown opens
  useEffect(() => {
    if (showDropdown && inputRef.current) {
      inputRef.current.focus()
    }
  }, [showDropdown])
  // 点击外部关闭下拉菜单
  useEffect(() => {
    if (!showDropdown) return
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as HTMLElement
      if (target.closest('[data-tag-dropdown]')) return
      setShowDropdown(false)
    }
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setShowDropdown(false)
    }
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [showDropdown])

  const handleAdd = async (tagId: number) => {
    try {
      await addPaperTag(paperId, tagId)
      fetchTags()
      setShowDropdown(false)
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : '添加标签失败'
      toast({ type: 'error', message })
    }
  }

  const handleRemove = async (tagId: number) => {
    try {
      await removePaperTag(paperId, tagId)
      fetchTags()
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : '移除标签失败'
      toast({ type: 'error', message })
    }
  }

  const handleCreate = async () => {
    if (!newTagName.trim()) return
    try {
      const tag = await createTag(newTagName.trim())
      await addPaperTag(paperId, tag.id)
      setNewTagName('')
      fetchTags()
      setShowDropdown(false)
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : '创建标签失败'
      toast({ type: 'error', message })
    }
  }

  const assignedIds = new Set(paperTags.map(t => t.id))
  const availableTags = allTags.filter(t => !assignedIds.has(t.id))

  return (
    <div className="relative">
      <div className="flex flex-wrap items-center gap-1.5">
        {paperTags.map(tag => (
          <span
            key={tag.id}
            className="inline-flex items-center gap-1 text-[11px] font-medium px-2.5 py-0.5 cursor-default group"
            style={{ backgroundColor: tag.color + '22', color: tag.color, border: `1px solid ${tag.color}44` }}
          >
            {tag.name}
            <button
              onClick={() => handleRemove(tag.id)}
              className="opacity-0 group-hover:opacity-100 transition-opacity ml-0.5"
              style={{ color: tag.color }}
              aria-label={`移除标签 ${tag.name}`}
            >
              &times;
            </button>
          </span>
        ))}
        <button
          onClick={() => setShowDropdown(v => !v)}
          aria-expanded={showDropdown}
          aria-haspopup="true"
          className="text-[11px] px-2.5 py-0.5 transition-colors hover:opacity-80"
          style={{ color: 'var(--text-muted)', border: '1px dashed var(--border-light)' }}
        >
          + 标签
        </button>
      </div>

      {showDropdown && (
        <div
          data-tag-dropdown
          className="absolute z-50 mt-2 w-56 p-2.5"
          style={{
            backgroundColor: 'var(--bg-elevated)',
            border: '1px solid var(--border)',
            boxShadow: '0 12px 28px rgba(0,0,0,0.14), 0 4px 8px rgba(0,0,0,0.06)',
          }}
        >
          <div className="flex gap-1.5 mb-2">
            <input
              ref={inputRef}
              type="text"
              value={newTagName}
              onChange={e => setNewTagName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleCreate()}
              placeholder="新建标签…"
              className="flex-1 text-[12px] px-2.5 py-1.5 focus:outline-none"
              style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)', color: 'var(--text-primary)' }}
            />
            <button
              onClick={handleCreate}
              className="text-[12px] px-3 py-1.5 font-medium transition-opacity hover:opacity-90"
              style={{ backgroundColor: 'var(--accent)', color: '#ffffff' }}
            >
              创建
            </button>
          </div>
          {availableTags.length > 0 && (
            <div className="max-h-32 overflow-auto space-y-0.5">
              {availableTags.map(tag => (
                <button
                  key={tag.id}
                  onClick={() => handleAdd(tag.id)}
                  className="w-full text-left text-[12px] px-2.5 py-1.5 transition-colors flex items-center gap-2"
                  style={{ color: 'var(--text-secondary)' }}
                  onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'var(--bg-surface)')}
                  onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
                >
                  <span className="w-2.5 h-2.5 shrink-0" style={{ backgroundColor: tag.color }} />
                  {tag.name}
                </button>
              ))}
            </div>
          )}
          {availableTags.length === 0 && !newTagName && (
            <p className="text-[12px] text-center py-2" style={{ color: 'var(--text-muted)' }}>输入名称创建新标签</p>
          )}
        </div>
      )}
    </div>
  )
}
