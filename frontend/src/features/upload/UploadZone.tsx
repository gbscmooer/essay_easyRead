/**
 * UploadZone 组件
 *
 * PDF 拖放上传 — 磨砂玻璃表面 + 强调色拖入光晕 + 精致进度条。
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
    e.target.value = ''
  }, [validateAndUpload])

  return (
    <div className="w-full max-w-lg mx-auto">
      <div
        onClick={handleClick}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        className="flex flex-col items-center justify-center p-12 cursor-pointer transition-all duration-300"
        style={{
          borderRadius: 'var(--seed-radius, 12px)',
          border: isDragOver
            ? '2px dashed var(--accent)'
            : isUploading
            ? '2px dashed var(--border)'
            : '2px dashed var(--border-light)',
          backgroundColor: isDragOver
            ? 'var(--accent-subtle)'
            : 'color-mix(in srgb, var(--bg-surface) 80%, transparent)',
          backdropFilter: 'blur(12px) saturate(150%)',
          WebkitBackdropFilter: 'blur(12px) saturate(150%)',
          opacity: isUploading ? 0.7 : 1,
          cursor: isUploading ? 'not-allowed' : 'pointer',
          boxShadow: isDragOver
            ? '0 0 40px rgba(var(--seed-accent-rgb, 37, 99, 235), 0.08)'
            : 'none',
          transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
        }}
      >
        {/* 上传图标 */}
        <div
          className="w-12 h-12 rounded-2xl flex items-center justify-center mb-4 transition-all duration-300"
          style={{
            backgroundColor: isDragOver ? 'var(--accent-subtle)' : 'var(--bg-elevated)',
            border: '1px solid var(--border)',
          }}
        >
          <svg
            width="22"
            height="22"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={1.5}
            strokeLinecap="round"
            strokeLinejoin="round"
            style={{
              color: isDragOver ? 'var(--accent)' : 'var(--text-muted)',
              transition: 'color 0.2s',
            }}
          >
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="17 8 12 3 7 8" />
            <line x1="12" y1="3" x2="12" y2="15" />
          </svg>
        </div>

        {/* 文字提示 */}
        {isUploading ? (
          <div className="flex flex-col items-center gap-3 w-full max-w-xs">
            <div className="flex items-center gap-2.5">
              <div
                className="w-4 h-4 border-[1.5px] rounded-full animate-spin"
                style={{ borderColor: 'var(--border)', borderTopColor: 'var(--accent)' }}
              />
              <span className="text-[13px]" style={{ color: 'var(--text-muted)' }}>
                {uploadProgress < 100 ? `上传中 ${uploadProgress}%` : '处理中...'}
              </span>
            </div>
            <div
              className="w-full h-1 rounded-full overflow-hidden"
              style={{ backgroundColor: 'var(--border)' }}
            >
              <div
                className="h-full rounded-full transition-all duration-500"
                style={{
                  width: `${uploadProgress}%`,
                  backgroundColor: 'var(--accent)',
                  transitionTimingFunction: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
                }}
              />
            </div>
          </div>
        ) : (
          <>
            <p className="text-[13px] mb-1.5" style={{ color: 'var(--text-secondary)' }}>
              {isDragOver ? '释放以上传文件' : '拖拽 PDF 到此处，或点击选择文件'}
            </p>
            <p className="text-[12px]" style={{ color: 'var(--text-muted)' }}>
              仅支持 PDF，最大 50MB
            </p>
          </>
        )}
      </div>

      {/* 错误提示 */}
      {error && (
        <p className="mt-3 text-[13px] text-center" style={{ color: 'var(--error)' }}>
          {error}
        </p>
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
