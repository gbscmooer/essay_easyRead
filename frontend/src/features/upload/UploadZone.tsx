/**
 * UploadZone 组件
 *
 * PDF 文件上传区域。
 *
 * Props:
 *   onUpload: (file: File) => void - 上传回调
 *   isUploading: boolean - 是否正在上传（禁用交互）
 *
 * 功能：
 *   - 拖拽上传区域（支持 drag & drop）
 *   - 点击选择文件（隐藏的 input[type=file]）
 *   - 文件类型校验：仅接受 .pdf
 *   - 文件大小提示：最大 50MB
 *   - 拖拽时高亮边框
 *   - 上传中显示加载状态
 *
 * 样式：
 *   - 虚线边框：border-2, border-dashed, border-gray-600
 *   - 拖拽高亮：border-blue-500, bg-blue-500/10
 *   - 居中图标 + 文字提示
 *   - hover 时边框变亮
 */

import React, { useState, useRef, useCallback } from 'react'


interface UploadZoneProps {
  onUpload: (file: File) => void
  isUploading: boolean
}

export function UploadZone({ onUpload, isUploading }: UploadZoneProps) {
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
        className={`flex flex-col items-center justify-center p-10 border-2 border-dashed rounded-xl cursor-pointer transition-all ${
          isDragOver
            ? 'border-blue-500 bg-blue-500/10'
            : isUploading
            ? 'border-gray-700 bg-gray-900 cursor-not-allowed opacity-60'
            : 'border-gray-600 bg-gray-900/50 hover:border-gray-400 hover:bg-gray-800/50'
        }`}
      >
        {/* 上传图标 */}
        <svg
          className={`w-12 h-12 mb-3 ${isDragOver ? 'text-blue-400' : 'text-gray-500'}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={1.5}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 16.5V9.75m0 0l3 3m-3-3l-3 3M6.75 19.5a4.5 4.5 0 01-1.41-8.775 5.25 5.25 0 0110.233-2.33 3 3 0 013.758 3.848A3.752 3.752 0 0118 19.5H6.75z" />
        </svg>

        {/* 文字提示 */}
        {isUploading ? (
          <div className="flex items-center gap-2">
            <div className="w-4 h-4 border-2 border-gray-600 border-t-blue-500 rounded-full animate-spin" />
            <span className="text-sm text-gray-400">上传中...</span>
          </div>
        ) : (
          <>
            <p className="text-sm text-gray-300 mb-1">
              {isDragOver ? '释放以上传文件' : '拖拽 PDF 到此处，或点击选择文件'}
            </p>
            <p className="text-xs text-gray-500">仅支持 PDF，最大 50MB</p>
          </>
        )}
      </div>

      {/* 错误提示 */}
      {error && (
        <p className="mt-2 text-sm text-red-400 text-center">{error}</p>
      )}

      {/* 隐藏的文件输入 */}
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,application/pdf"
        onChange={handleFileChange}
        className="hidden"
      />
    </div>
  )
}
