/**
 * 配置文件管理与迁移组件
 *
 * 支持：
 * 1. 显示当前生效的配置文件（项目根目录 config.yaml / config.toml / data/ai_config.json）
 * 2. 导出配置（YAML / TOML / JSON，支持选择是否包含 API Key 用于迁移备份）
 * 3. 导入配置（文件上传或文本粘贴，原子合并并即时生效）
 */

import { useState, useEffect, useRef } from 'react'
import {
  getConfigFileInfo,
  getExportConfigUrl,
  importConfigFile,
  importConfigText,
  type ConfigFileInfo,
} from '@/api/settings'
import { toast } from '@/hooks/useToast'
import { LoadingSpinner } from '@/ds-entry'

interface ConfigFileManagementProps {
  onConfigReload: () => void
}

export function ConfigFileManagement({ onConfigReload }: ConfigFileManagementProps) {
  const [fileInfo, setFileInfo] = useState<ConfigFileInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [showExportModal, setShowExportModal] = useState(false)
  const [showImportModal, setShowImportModal] = useState(false)
  const [showGuide, setShowGuide] = useState(false)

  // 导出设置
  const [exportFormat, setExportFormat] = useState<'yaml' | 'toml' | 'json'>('yaml')
  const [includeSecrets, setIncludeSecrets] = useState(true)

  // 导入设置
  const [importMode, setImportMode] = useState<'file' | 'text'>('file')
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [importTextContent, setImportTextContent] = useState('')
  const [importing, setImporting] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const loadInfo = async () => {
    try {
      const info = await getConfigFileInfo()
      setFileInfo(info)
    } catch {
      // 忽略加载错误
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadInfo()
  }, [])

  const handleExportDownload = () => {
    const url = getExportConfigUrl(exportFormat, includeSecrets)
    // 创建隐藏 a 标签触发下载
    const a = document.createElement('a')
    a.href = url
    a.download = `config.${exportFormat === 'yaml' ? 'yaml' : exportFormat === 'toml' ? 'toml' : 'json'}`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    setShowExportModal(false)
    toast({ type: 'success', message: `已导出 ${exportFormat.toUpperCase()} 配置文件` })
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setSelectedFile(e.target.files[0])
    }
  }

  const handleExecuteImport = async () => {
    setImporting(true)
    try {
      if (importMode === 'file') {
        if (!selectedFile) {
          toast({ type: 'error', message: '请先选择要导入的配置文件' })
          setImporting(false)
          return
        }
        await importConfigFile(selectedFile)
      } else {
        if (!importTextContent.trim()) {
          toast({ type: 'error', message: '请先填入配置文本内容' })
          setImporting(false)
          return
        }
        await importConfigText(importTextContent)
      }

      toast({ type: 'success', message: '配置已成功导入并即时生效' })
      setShowImportModal(false)
      setSelectedFile(null)
      setImportTextContent('')
      await loadInfo()
      onConfigReload()
    } catch (err: unknown) {
      toast({
        type: 'error',
        message: err instanceof Error ? err.message : '配置文件导入失败，请检查格式',
      })
    } finally {
      setImporting(false)
    }
  }

  const isRootConfig = fileInfo?.active_path?.startsWith('config.')

  return (
    <div className="set-file-card mb-5">
      <div className="set-file-card__main">
        <div className="set-file-card__meta">
          <div className="flex items-center gap-2">
            <span className="set-file-card__icon" aria-hidden="true">
              <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            </span>
            <span className="set-file-card__title">配置文件状态</span>
            {loading ? (
              <LoadingSpinner size="xs" />
            ) : (
              <>
                <span className="set-file-card__path" title={fileInfo?.full_path || ''}>
                  <code>{fileInfo?.active_path || 'config.yaml'}</code>
                </span>
                <span className={`set-file-card__badge ${fileInfo?.exists ? 'set-file-card__badge--active' : 'set-file-card__badge--fallback'}`}>
                  {fileInfo?.format?.toUpperCase() || 'YAML'} · {fileInfo?.exists ? '已生效' : '默认回退'}
                </span>
              </>
            )}
          </div>
          <p className="set-file-card__desc">
            {isRootConfig ? (
              <>
                系统已关联项目根目录下的 <strong>{fileInfo?.active_path}</strong>。您可以直接在本地用编辑器批量修改 API Key 与参数，修改保存即生效。
              </>
            ) : (
              <>
                当前正从 <strong>{fileInfo?.active_path || 'data/ai_config.json'}</strong> 读取配置。可在项目根目录下创建 <code>config.yaml</code> 或直接「导出配置」放置于根目录。
              </>
            )}
          </p>
        </div>

        <div className="set-file-card__actions">
          <button
            type="button"
            className="er-btn er-btn--secondary er-btn--sm"
            onClick={() => setShowExportModal(true)}
            title="导出配置文件供迁移备份"
          >
            <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
            </svg>
            导出配置
          </button>

          <button
            type="button"
            className="er-btn er-btn--secondary er-btn--sm"
            onClick={() => setShowImportModal(true)}
            title="从外部导入 YAML/TOML/JSON 配置文件"
          >
            <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
            </svg>
            导入配置
          </button>

          <button
            type="button"
            className="er-btn er-btn--ghost er-btn--sm"
            onClick={() => setShowGuide(!showGuide)}
            aria-expanded={showGuide}
          >
            {showGuide ? '收起说明' : '文件说明'}
          </button>
        </div>
      </div>

      {showGuide && (
        <div className="set-file-card__guide mt-3 pt-3 border-t border-[var(--border)] text-xs text-[var(--text-secondary)] space-y-1.5">
          <p>• <strong>优先加载顺序：</strong><code>config.yaml</code> &gt; <code>config.yml</code> &gt; <code>config.toml</code> &gt; <code>data/ai_config.json</code>。</p>
          <p>• <strong>免手动填 key：</strong>在根目录下建立 <code>config.yaml</code> 填入 <code>analysis.api_key</code> 与 <code>translate.api_key</code>，即可一处配置全系统通用。</p>
          <p>• <strong>参考模板：</strong>项目根目录已提供带完整字段中文说明的 <code>config.example.yaml</code>，可直接复制参考。</p>
          <p>• <strong>安全防泄露：</strong><code>config.yaml</code> 已在 <code>.gitignore</code> 中忽略，不会误提交至代码仓库。</p>
        </div>
      )}

      {/* 导出弹窗 */}
      {showExportModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 animate-fade-in" role="dialog" aria-modal="true">
          <div className="bg-[var(--bg-surface)] border border-[var(--border)] rounded-xl max-w-md w-full p-5 shadow-2xl space-y-4">
            <div className="flex items-center justify-between border-b border-[var(--border)] pb-3">
              <h3 className="text-base font-semibold text-[var(--text-primary)] flex items-center gap-2">
                <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                </svg>
                导出系统配置文件
              </h3>
              <button
                type="button"
                className="er-btn er-btn--ghost er-btn--sm"
                onClick={() => setShowExportModal(false)}
                aria-label="关闭"
              >
                ✕
              </button>
            </div>

            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1.5">导出文件格式</label>
                <div className="grid grid-cols-3 gap-2">
                  {(['yaml', 'toml', 'json'] as const).map(fmt => (
                    <button
                      key={fmt}
                      type="button"
                      onClick={() => setExportFormat(fmt)}
                      className={`er-btn er-btn--sm ${exportFormat === fmt ? 'er-btn--primary' : 'er-btn--secondary'}`}
                    >
                      {fmt.toUpperCase()}
                      {fmt === 'yaml' && ' (推荐)'}
                    </button>
                  ))}
                </div>
              </div>

              <div className="pt-2">
                <label className="flex items-start gap-2.5 cursor-pointer text-xs text-[var(--text-primary)]">
                  <input
                    type="checkbox"
                    checked={includeSecrets}
                    onChange={e => setIncludeSecrets(e.target.checked)}
                    className="mt-0.5 rounded border-[var(--border)] text-blue-600 focus:ring-0"
                  />
                  <span>
                    <strong>包含 API 密钥（用于环境迁移与快速部署）</strong>
                    <span className="block text-[var(--text-muted)] mt-0.5">
                      本地访问时将导出完整密钥以便在新机器即开即用；若要分享给他人请取消勾选进行脱敏。
                    </span>
                  </span>
                </label>
              </div>
            </div>

            <div className="flex justify-end gap-2.5 pt-3 border-t border-[var(--border)]">
              <button
                type="button"
                className="er-btn er-btn--ghost er-btn--sm"
                onClick={() => setShowExportModal(false)}
              >
                取消
              </button>
              <button
                type="button"
                className="er-btn er-btn--primary er-btn--sm"
                onClick={handleExportDownload}
              >
                下载配置文件
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 导入弹窗 */}
      {showImportModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 animate-fade-in" role="dialog" aria-modal="true">
          <div className="bg-[var(--bg-surface)] border border-[var(--border)] rounded-xl max-w-lg w-full p-5 shadow-2xl space-y-4">
            <div className="flex items-center justify-between border-b border-[var(--border)] pb-3">
              <h3 className="text-base font-semibold text-[var(--text-primary)] flex items-center gap-2">
                <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                </svg>
                导入配置文件
              </h3>
              <button
                type="button"
                className="er-btn er-btn--ghost er-btn--sm"
                onClick={() => !importing && setShowImportModal(false)}
                aria-label="关闭"
              >
                ✕
              </button>
            </div>

            <div className="flex gap-2 border-b border-[var(--border)] pb-2">
              <button
                type="button"
                className={`er-btn er-btn--sm ${importMode === 'file' ? 'er-btn--primary' : 'er-btn--ghost'}`}
                onClick={() => setImportMode('file')}
              >
                上传文件 (.yaml / .toml / .json)
              </button>
              <button
                type="button"
                className={`er-btn er-btn--sm ${importMode === 'text' ? 'er-btn--primary' : 'er-btn--ghost'}`}
                onClick={() => setImportMode('text')}
              >
                直接粘贴文本
              </button>
            </div>

            {importMode === 'file' ? (
              <div className="space-y-3">
                <input
                  type="file"
                  ref={fileInputRef}
                  onChange={handleFileChange}
                  accept=".yaml,.yml,.toml,.json"
                  className="hidden"
                />
                <div
                  onClick={() => fileInputRef.current?.click()}
                  className="border-2 border-dashed border-[var(--border)] hover:border-blue-500 rounded-lg p-6 text-center cursor-pointer transition-colors bg-[var(--bg-base)]"
                >
                  <svg className="mx-auto h-8 w-8 text-[var(--text-muted)] mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                  </svg>
                  <p className="text-sm font-medium text-[var(--text-primary)]">
                    {selectedFile ? selectedFile.name : '点击选择配置文件'}
                  </p>
                  <p className="text-xs text-[var(--text-muted)] mt-1">
                    {selectedFile
                      ? `大小: ${(selectedFile.size / 1024).toFixed(1)} KB`
                      : '支持 YAML (.yaml, .yml)、TOML (.toml)、JSON (.json)'}
                  </p>
                </div>
              </div>
            ) : (
              <div className="space-y-2">
                <label className="block text-xs font-medium text-[var(--text-secondary)]">
                  配置文本内容（自动解析格式）：
                </label>
                <textarea
                  value={importTextContent}
                  onChange={e => setImportTextContent(e.target.value)}
                  placeholder="在此粘贴 YAML、TOML 或 JSON 配置文本..."
                  rows={8}
                  className="w-full font-mono text-xs p-3 rounded-lg border border-[var(--border)] bg-[var(--bg-base)] text-[var(--text-primary)] focus:outline-none focus:border-blue-500"
                />
              </div>
            )}

            <div className="text-xs text-[var(--text-muted)] bg-[var(--bg-base)] p-3 rounded-lg border border-[var(--border)]">
              💡 <strong>安全提示：</strong>导入后将与当前配置深度合并并原子持久化。如果导入的内容中包含掩码占位（如 <code>***xxxx</code>），系统将自动保护并保留原有的真实密钥。
            </div>

            <div className="flex justify-end gap-2.5 pt-3 border-t border-[var(--border)]">
              <button
                type="button"
                className="er-btn er-btn--ghost er-btn--sm"
                disabled={importing}
                onClick={() => setShowImportModal(false)}
              >
                取消
              </button>
              <button
                type="button"
                className="er-btn er-btn--primary er-btn--sm"
                disabled={importing || (importMode === 'file' ? !selectedFile : !importTextContent.trim())}
                onClick={handleExecuteImport}
              >
                {importing && <LoadingSpinner size="xs" />}
                {importing ? '导入中…' : '确认导入并生效'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
