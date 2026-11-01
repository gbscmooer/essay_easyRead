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
        className="mg-drop"
        data-over={isDragOver || undefined}
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
        <span>
          <span className="mg-drop__title">{isDragOver ? '松开即可上传' : '投稿：上传一篇 PDF 论文'}</span>
          <span id={descriptionId} className="mg-drop__desc">
            点击选择，或把 PDF 拖到这里。上传后自动开始精读解析（单个文件不超过 50MB）。
          </span>
        </span>
        <span id={statusId} aria-live="polite" aria-atomic="true">
          {isUploading ? (
            <span className="mg-drop__progress">
              <span>{status}</span>
              <span className="mg-drop__bar"><span style={{ width: `${progress}%` }} /></span>
            </span>
          ) : (
            <span className="mg-drop__action">选择文件 <span aria-hidden="true">→</span></span>
          )}
        </span>
      </button>
      {error && <p className="mg-drop__error" role="alert">{error}</p>}
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
