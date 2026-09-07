/** PDF drag-and-select intake with local validation and upload progress. */

import {
  useCallback,
  useId,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
} from 'react'

interface UploadZoneProps {
  onUpload: (file: File) => void
  isUploading: boolean
  uploadProgress?: number
}

export function UploadZone({ onUpload, isUploading, uploadProgress = 0 }: UploadZoneProps) {
  const [isDragOver, setIsDragOver] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const descriptionId = useId()
  const statusId = useId()

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

  const handleDrop = useCallback((event: DragEvent<HTMLButtonElement>) => {
    event.preventDefault()
    setIsDragOver(false)
    if (isUploading) return

    const file = event.dataTransfer.files[0]
    if (file) validateAndUpload(file)
  }, [isUploading, validateAndUpload])

  const handleDragOver = useCallback((event: DragEvent<HTMLButtonElement>) => {
    event.preventDefault()
    if (!isUploading) setIsDragOver(true)
  }, [isUploading])

  const handleDragLeave = useCallback((event: DragEvent<HTMLButtonElement>) => {
    event.preventDefault()
    const relatedTarget = event.relatedTarget
    if (!(relatedTarget instanceof Node) || !event.currentTarget.contains(relatedTarget)) {
      setIsDragOver(false)
    }
  }, [])

  const handleClick = useCallback(() => {
    if (!isUploading) inputRef.current?.click()
  }, [isUploading])

  const handleFileChange = useCallback((event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (file) validateAndUpload(file)
    event.target.value = ''
  }, [validateAndUpload])

  const progress = Math.min(100, Math.max(0, uploadProgress))
  const status = progress >= 100 ? '上传完成' : `上传中 ${progress}%`

  return (
    <div className="w-full">
      <button
        className={`w-full relative group border-2 border-dashed rounded-xl p-8 transition-all flex flex-col sm:flex-row items-center justify-between gap-6 cursor-pointer text-left ${
          isDragOver
            ? 'border-blue-500 bg-blue-50/50 dark:bg-blue-950/20 scale-[0.99]'
            : 'border-[var(--border)] hover:border-blue-500/60 bg-[var(--bg-surface)] hover:bg-[var(--bg-elevated)] shadow-sm'
        }`}
        type="button"
        aria-busy={isUploading}
        aria-disabled={isUploading}
        aria-describedby={`${descriptionId} ${isUploading ? statusId : ''}`.trim()}
        onClick={handleClick}
        onDrop={handleDrop}
        onDragEnter={handleDragOver}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
      >
        <div className="flex items-center gap-4">
          <div className={`w-12 h-12 rounded-xl flex items-center justify-center shrink-0 transition-colors ${
            isDragOver ? 'bg-blue-600 text-white' : 'bg-blue-50 dark:bg-blue-950/80 text-blue-600 dark:text-blue-400 group-hover:scale-105'
          }`}>
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
            </svg>
          </div>

          <div className="flex flex-col">
            <h3 className="text-base font-semibold text-[var(--text-primary)]">
              {isDragOver ? '松开鼠标即可上传' : '上传 PDF 论文'}
            </h3>
            <p id={descriptionId} className="text-xs text-[var(--text-muted)] mt-0.5">
              点击选择或将 PDF 拖拽至此处，系统将自动进行 AI 深度精读解析（最大 50MB）
            </p>
          </div>
        </div>

        <div className="shrink-0 w-full sm:w-auto text-right" id={statusId} aria-live="polite" aria-atomic="true">
          {isUploading ? (
            <div className="flex flex-col sm:items-end gap-1.5 w-full sm:w-48">
              <span className="text-xs font-medium text-blue-600 dark:text-blue-400">{status}</span>
              <div className="w-full h-2 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-blue-600 rounded-full transition-all duration-200"
                  style={{ width: `${progress}%` }}
                />
              </div>
            </div>
          ) : (
            <span className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium bg-[var(--accent)] text-white group-hover:opacity-90 shadow-sm transition-all">
              <span>选择文件</span>
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14 5l7 7m0 0l-7 7m7-7H3" />
              </svg>
            </span>
          )}
        </div>
      </button>

      {error && <p className="mt-2 text-xs text-red-500 font-medium" role="alert">{error}</p>}

      <input
        ref={inputRef}
        className="hidden"
        type="file"
        accept=".pdf,application/pdf"
        tabIndex={-1}
        aria-label="选择 PDF 文件"
        onChange={handleFileChange}
        data-shortcut-upload
      />
    </div>
  )
}
