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
    <div className="origin-upload">
      <button
        className="origin-upload__surface"
        type="button"
        data-dragging={isDragOver || undefined}
        data-uploading={isUploading || undefined}
        aria-busy={isUploading}
        aria-disabled={isUploading}
        aria-describedby={`${descriptionId} ${isUploading ? statusId : ''}`.trim()}
        onClick={handleClick}
        onDrop={handleDrop}
        onDragEnter={handleDragOver}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
      >
        <span className="origin-upload__mark" aria-hidden="true">
          <svg viewBox="0 0 36 48" focusable="false">
            <path d="M1 1h23l11 11v35H1z" />
            <path d="M24 1v11h11M18 35V18m0 0-6 6m6-6 6 6" />
          </svg>
        </span>

        <span className="origin-upload__copy">
          <span className="origin-upload__register">PDF / SOURCE—01</span>
          <strong>{isDragOver ? '释放，登记这份文档' : '放入一篇论文'}</strong>
          <small id={descriptionId}>拖拽至此或按下选择 / PDF / 最大 50 MB</small>
        </span>

        <span className="origin-upload__status" id={statusId} aria-live="polite" aria-atomic="true">
          {isUploading ? (
            <>
              <span>{status}</span>
              <progress max={100} value={progress} aria-label={`上传进度 ${progress}%`} />
            </>
          ) : (
            <>
              <span>SELECT DOCUMENT</span>
              <i aria-hidden="true">&#8599;</i>
            </>
          )}
        </span>
      </button>

      {error && <p className="origin-upload__error" role="alert">{error}</p>}

      <input
        ref={inputRef}
        className="origin-upload__input"
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
