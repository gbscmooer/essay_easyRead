import { useState, useEffect, useCallback } from 'react'
import type { Tag } from '@/types'
import { listTags, getPaperTags, addPaperTag, removePaperTag, createTag } from '@/api/tags'

interface TagManagerProps {
  paperId: number
}

export function TagManager({ paperId }: TagManagerProps) {
  const [allTags, setAllTags] = useState<Tag[]>([])
  const [paperTags, setPaperTags] = useState<Tag[]>([])
  const [showDropdown, setShowDropdown] = useState(false)
  const [newTagName, setNewTagName] = useState('')

  const fetchTags = useCallback(async () => {
    try {
      const [all, paper] = await Promise.all([listTags(), getPaperTags(paperId)])
      setAllTags(all)
      setPaperTags(paper)
    } catch {
      // ignore
    }
  }, [paperId])

  useEffect(() => { fetchTags() }, [fetchTags])

  const handleAdd = async (tagId: number) => {
    try {
      await addPaperTag(paperId, tagId)
      fetchTags()
      setShowDropdown(false)
    } catch {
      // ignore
    }
  }

  const handleRemove = async (tagId: number) => {
    try {
      await removePaperTag(paperId, tagId)
      fetchTags()
    } catch {
      // ignore
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
    } catch {
      // ignore
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
            className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full cursor-default group"
            style={{ backgroundColor: tag.color + '22', color: tag.color, border: `1px solid ${tag.color}44` }}
          >
            {tag.name}
            <button
              onClick={() => handleRemove(tag.id)}
              className="opacity-0 group-hover:opacity-100 transition-opacity ml-0.5"
              style={{ color: tag.color }}
            >
              &times;
            </button>
          </span>
        ))}
        <button
          onClick={() => setShowDropdown(v => !v)}
          className="text-xs px-2 py-0.5 rounded-full transition-colors"
          style={{ color: 'var(--text-muted)', border: '1px dashed var(--border)' }}
        >
          + 标签
        </button>
      </div>

      {showDropdown && (
        <div
          className="absolute z-50 mt-2 w-56 rounded-lg border shadow-lg p-2"
          style={{ backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border)' }}
        >
          <div className="flex gap-1 mb-2">
            <input
              type="text"
              value={newTagName}
              onChange={e => setNewTagName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleCreate()}
              placeholder="新建标签..."
              className="flex-1 text-xs px-2 py-1 rounded border focus:outline-none"
              style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-surface)', color: 'var(--text-primary)' }}
            />
            <button
              onClick={handleCreate}
              className="text-xs px-2 py-1 rounded transition-colors"
              style={{ backgroundColor: 'var(--accent)', color: 'var(--bg-base)' }}
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
                  className="w-full text-left text-xs px-2 py-1 rounded transition-colors flex items-center gap-2"
                  style={{ color: 'var(--text-secondary)' }}
                  onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'var(--bg-surface)')}
                  onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
                >
                  <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: tag.color }} />
                  {tag.name}
                </button>
              ))}
            </div>
          )}
          {availableTags.length === 0 && !newTagName && (
            <p className="text-xs text-center py-2" style={{ color: 'var(--text-muted)' }}>输入名称创建新标签</p>
          )}
        </div>
      )}
    </div>
  )
}
