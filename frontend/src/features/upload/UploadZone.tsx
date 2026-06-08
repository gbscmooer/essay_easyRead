/**
 * UploadZone 组件
 *
 * PDF 文件上传区域，清新学术风格设计。
 */

import React, { useState, useRef, useCallback } from 'react'


interface UploadZoneProps {
  onUpload: (file: File) => void
  isUploading: boolean
  uploadProgress?: number
}

export function UploadZone({ onUpload, isUploading, uploadProgress = 0 }: UploadZoneProps) {
  const [isDragOver, setIsDragOver] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const validateAndUpload = useCallback((file: File) => {
    setError(null)

    if (!file.name.toLowerCase().endsWith('.pdf') && file.type !== 'application/pdf') {
      setError('仅支持 PDF 文件')
      return
    }

    if (file.size > 50 * 1024 * 1024) {
      setError('文件大小不能超过 50MB')
      return
    }

    onUpload(file)
  }, [onUpload])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(false)

    if (isUploading) return

    const file = e.dataTransfer.files[0]
    if (file) validateAndUpload(file)
  }, [isUploading, validateAndUpload])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    if (!isUploading) setIsDragOver(true)
  }, [isUploading])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(false)
  }, [])

  const handleClick = useCallback(() => {
    if (!isUploading) inputRef.current?.click()
  }, [isUploading])

  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) validateAndUpload(file)
    // 重置 input 以支持重复上传同一文件
    e.target.value = ''
  }, [validateAndUpload])

  return (
    <div className="w-full max-w-lg mx-auto">
      <div
        onClick={handleClick}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        className="flex flex-col items-center justify-center p-10 rounded-xl cursor-pointer transition-all"
        style={{
          border: isDragOver
            ? '2px dashed var(--accent)'
            : isUploading
            ? '2px dashed var(--border)'
            : '2px dashed var(--border-light)',
          backgroundColor: isDragOver
            ? 'var(--accent-subtle)'
            : isUploading
            ? 'var(--bg-surface)'
            : 'var(--bg-surface)',
          opacity: isUploading ? 0.6 : 1,
          cursor: isUploading ? 'not-allowed' : 'pointer',
        }}
      >
        {/* 上传图标 */}
        <svg
          className="w-10 h-10 mb-3"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={1.2}
          style={{ color: isDragOver ? 'var(--accent)' : 'var(--text-muted)' }}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 16.5V9.75m0 0l3 3m-3-3l-3 3M6.75 19.5a4.5 4.5 0 01-1.41-8.775 5.25 5.25 0 0110.233-2.33 3 3 0 013.758 3.848A3.752 3.752 0 0118 19.5H6.75z" />
        </svg>

        {/* 文字提示 */}
        {isUploading ? (
          <div className="flex flex-col items-center gap-2 w-full max-w-xs">
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 border-2 rounded-full animate-spin" style={{ borderColor: 'var(--border)', borderTopColor: 'var(--accent)' }} />
              <span className="text-sm" style={{ color: 'var(--text-muted)' }}>
                {uploadProgress < 100 ? `上传中 ${uploadProgress}%` : '处理中...'}
              </span>
            </div>
            <div className="w-full h-1.5 rounded-full overflow-hidden" style={{ backgroundColor: 'var(--border)' }}>
              <div
                className="h-full rounded-full transition-all duration-300"
                style={{ width: `${uploadProgress}%`, backgroundColor: 'var(--accent)' }}
              />
            </div>
          </div>
        ) : (
          <>
            <p className="text-sm mb-1" style={{ color: 'var(--text-secondary)' }}>
              {isDragOver ? '释放以上传文件' : '拖拽 PDF 到此处，或点击选择文件'}
            </p>
            <p className="text-xs" style={{ color: 'var(--text-muted)' }}>仅支持 PDF，最大 50MB</p>
          </>
        )}
      </div>

      {/* 错误提示 */}
      {error && (
        <p className="mt-2 text-sm text-center" style={{ color: 'var(--error)' }}>{error}</p>
      )}

      {/* 隐藏的文件输入 */}
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,application/pdf"
        onChange={handleFileChange}
        data-shortcut-upload
        className="hidden"
      />
    </div>
  )
}
