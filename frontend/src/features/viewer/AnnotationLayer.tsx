import { useState, useRef, useEffect, useCallback } from 'react'
import type { Annotation } from '@/types'

interface AnnotationLayerProps {
  annotations: Annotation[]
  pageNumber: number
  onDelete: (id: number) => void
}

const HIGHLIGHT_OPACITY = '0.35'

export function AnnotationLayer({ annotations, pageNumber, onDelete }: AnnotationLayerProps) {
  const pageAnnotations = annotations.filter(a => a.page_number === pageNumber)
  const [pendingId, setPendingId] = useState<number | null>(null)
  const [popupPos, setPopupPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 })
  const popupRef = useRef<HTMLDivElement>(null)
  const layerRef = useRef<HTMLDivElement>(null)

  // Dismiss popup when clicking outside
  useEffect(() => {
    if (pendingId === null) return

    const handleClickOutside = (e: MouseEvent) => {
      if (popupRef.current && !popupRef.current.contains(e.target as Node)) {
        setPendingId(null)
      }
    }
    // Use setTimeout so the current click doesn't immediately dismiss
    const timer = setTimeout(() => {
      document.addEventListener('mousedown', handleClickOutside)
    }, 0)

    return () => {
      clearTimeout(timer)
      document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [pendingId])

  const handleAnnotationClick = useCallback((ann: Annotation, e: React.MouseEvent) => {
    e.stopPropagation()
    const layerEl = layerRef.current
    if (!layerEl) return

    const layerRect = layerEl.getBoundingClientRect()
    // Position popup at the click location relative to the layer
    setPopupPos({
      x: e.clientX - layerRect.left,
      y: e.clientY - layerRect.top,
    })
    setPendingId(ann.id)
  }, [])

  const handleConfirm = useCallback(() => {
    if (pendingId !== null) {
      onDelete(pendingId)
      setPendingId(null)
    }
  }, [pendingId, onDelete])

  const handleCancel = useCallback(() => {
    setPendingId(null)
  }, [])

  if (pageAnnotations.length === 0) return null

  // Count how many annotations share the same group as the pending one (across all pages)
  const pendingAnn = pendingId !== null ? pageAnnotations.find(a => a.id === pendingId) : null
  const groupCount = pendingAnn?.group_id
    ? annotations.filter(a => a.group_id === pendingAnn.group_id).length
    : 1

  return (
    <div ref={layerRef} className="absolute inset-0 pointer-events-none z-[5]">
      {pageAnnotations.map(ann => {
        const isPending = ann.id === pendingId || (
          pendingAnn?.group_id != null && ann.group_id === pendingAnn.group_id
        )

        if (ann.annotation_type === 'highlight') {
          return (
            <div
              key={ann.id}
              className="absolute group pointer-events-auto cursor-pointer"
              style={{
                left: `${ann.x * 100}%`,
                top: `${ann.y * 100}%`,
                width: `${ann.width * 100}%`,
                height: `${ann.height * 100}%`,
                backgroundColor: ann.color,
                opacity: isPending ? '0.6' : HIGHLIGHT_OPACITY,
                mixBlendMode: 'multiply',
                outline: isPending ? '2px dashed rgba(239,68,68,0.7)' : 'none',
                transition: 'opacity 0.15s, outline 0.15s',
              }}
              onClick={(e) => handleAnnotationClick(ann, e)}
              title={`点击删除标注: ${ann.text.slice(0, 40)}`}
            >
              <span
                className="absolute -top-5 left-0 hidden group-hover:block text-[9px] px-1.5 py-0.5 whitespace-nowrap z-50"
                style={{ backgroundColor: 'var(--bg-surface)', color: 'var(--text-primary)' }}
              >
                点击删除
              </span>
            </div>
          )
        }

        return (
          <div
            key={ann.id}
            className="absolute group pointer-events-auto cursor-pointer"
            style={{
              left: `${ann.x * 100}%`,
              top: `${ann.y * 100}%`,
              width: `${ann.width * 100}%`,
              height: `${ann.height * 100}%`,
              outline: isPending ? '2px dashed rgba(239,68,68,0.7)' : 'none',
              transition: 'outline 0.15s',
            }}
            onClick={(e) => handleAnnotationClick(ann, e)}
            title={`点击删除标注: ${ann.text.slice(0, 40)}`}
          >
            {ann.annotation_type === 'underline' && (
              <div className="absolute bottom-0 left-0 w-full" style={{ height: '2px', backgroundColor: ann.color }} />
            )}
            {ann.annotation_type === 'strikethrough' && (
              <div className="absolute left-0 w-full" style={{ top: '50%', height: '2px', backgroundColor: ann.color, transform: 'translateY(-50%)' }} />
            )}
            <span
              className="absolute -top-5 left-0 hidden group-hover:block text-[9px] px-1.5 py-0.5 whitespace-nowrap z-50"
              style={{ backgroundColor: 'var(--bg-surface)', color: 'var(--text-primary)' }}
            >
              点击删除
            </span>
          </div>
        )
      })}

      {/* Delete confirmation popup */}
      {pendingId !== null && (
        <div
          ref={popupRef}
          className="absolute pointer-events-auto z-[60]"
          style={{
            left: `${popupPos.x}px`,
            top: `${popupPos.y}px`,
            transform: 'translate(-50%, -120%)',
          }}
        >
          <div
            className="px-3 py-2 flex items-center gap-2 whitespace-nowrap"
            style={{ backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border-light)' }}
          >
            <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
              {groupCount > 1 ? `删除这 ${groupCount} 条标注？` : '删除此标注？'}
            </span>
            <button
              onClick={handleConfirm}
              className="px-2.5 py-1 text-xs font-medium transition-colors hover:opacity-90"
              style={{ backgroundColor: 'var(--error)', color: 'var(--bg-base)' }}
            >
              确认
            </button>
            <button
              onClick={handleCancel}
              className="px-2.5 py-1 text-xs font-medium transition-colors hover:opacity-80"
              style={{ backgroundColor: 'var(--border)', color: 'var(--text-secondary)' }}
            >
              取消
            </button>
          </div>
          {/* Arrow */}
          <div className="flex justify-center">
            <div
              className="w-2 h-2 transform rotate-45"
              style={{
                backgroundColor: 'var(--bg-elevated)',
                borderRight: '1px solid var(--border)',
                borderBottom: '1px solid var(--border)',
                marginTop: '-4px',
              }}
            />
          </div>
        </div>
      )}
    </div>
  )
}
