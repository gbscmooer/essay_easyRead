import { useState, useEffect, useCallback } from 'react'
import type { AnalysisResult, RelatedPaper } from '@/types'
import { refreshRelatedPapers, importRelatedPapers, exportBibtex } from '@/api/papers'
import type { ImportPaperResult } from '@/api/papers'
import { listWorkspaces } from '@/api/workspaces'
import type { WorkspaceItem } from '@/api/workspaces'
import { TagManager } from '../TagManager'


interface OverviewTabProps {
  result: AnalysisResult
  paperId?: number
  /** 相关论文模块独立状态：loading 搜索中 / failed 超时或失败 */
  relatedStatus?: 'idle' | 'loading' | 'ok' | 'failed'
}

export function OverviewTab({ result, paperId, relatedStatus }: OverviewTabProps) {
  const [relatedPapers, setRelatedPapers] = useState<RelatedPaper[]>(result.related_papers || [])
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [bibtexLoading, setBibtexLoading] = useState(false)

  // Import mode state
  const [importMode, setImportMode] = useState(false)
  const [selectedIndices, setSelectedIndices] = useState<Set<number>>(new Set())
  const [isImporting, setIsImporting] = useState(false)
  const [importProgress, setImportProgress] = useState<{ done: number; total: number } | null>(null)
  const [importResults, setImportResults] = useState<ImportPaperResult[] | null>(null)

  // Workspace selector state
  const [showWsSelector, setShowWsSelector] = useState(false)
  const [workspaces, setWorkspaces] = useState<WorkspaceItem[]>([])
  const [wsLoading, setWsLoading] = useState(false)

  useEffect(() => {
    setRelatedPapers(result.related_papers || [])
  }, [result.related_papers])

  const handleRefresh = async () => {
    if (!paperId) return
    setIsRefreshing(true)
    try {
      const papers = await refreshRelatedPapers(paperId)
      setRelatedPapers(papers)
    } catch {
      // keep existing data
    } finally {
      setIsRefreshing(false)
    }
  }

  // Enter import mode
  const handleEnterImportMode = useCallback(async () => {
    setImportMode(true)
    setSelectedIndices(new Set())
    setImportResults(null)
    setImportProgress(null)
    // Load workspaces
    setWsLoading(true)
    try {
      const wss = await listWorkspaces()
      setWorkspaces(wss)
    } catch {
      setWorkspaces([])
    } finally {
      setWsLoading(false)
    }
  }, [])

  // Exit import mode
  const handleExitImportMode = useCallback(() => {
    setImportMode(false)
    setSelectedIndices(new Set())
    setImportResults(null)
    setImportProgress(null)
    setShowWsSelector(false)
  }, [])

  // Toggle selection
  const toggleSelect = useCallback((idx: number) => {
    setSelectedIndices(prev => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })
  }, [])

  // Select all
  const selectAll = useCallback(() => {
    if (selectedIndices.size === relatedPapers.length) {
      setSelectedIndices(new Set())
    } else {
      setSelectedIndices(new Set(relatedPapers.map((_, i) => i)))
    }
  }, [relatedPapers, selectedIndices.size])

  // Start import → show workspace selector
  const handleStartImport = useCallback(() => {
    if (selectedIndices.size === 0) return
    setShowWsSelector(true)
  }, [selectedIndices.size])

  // Execute import to a specific workspace
  const handleImportToWorkspace = useCallback(async (workspaceId: number) => {
    setShowWsSelector(false)
    setIsImporting(true)
    const papersToImport = Array.from(selectedIndices).map(idx => relatedPapers[idx])
    setImportProgress({ done: 0, total: papersToImport.length })

    try {
      const resp = await importRelatedPapers(
        workspaceId,
        papersToImport.map(p => ({
          title: p.title,
          doi: p.doi || '',
          url: p.url || '',
        })),
      )
      setImportResults(resp.results)
      setImportProgress({ done: papersToImport.length, total: papersToImport.length })
    } catch {
      setImportResults(
        papersToImport.map(p => ({
          title: p.title,
          status: 'failed' as const,
          message: '导入请求失败',
        })),
      )
    } finally {
      setIsImporting(false)
    }
  }, [selectedIndices, relatedPapers])

  return (
    <div className="space-y-6">
      {/* Title + venue + year */}
      <div>
        <h1 className="text-2xl font-bold text-gray-100 mb-2">{result.title}</h1>
        <div className="flex items-center gap-2">
          {result.venue && (
            <span className="px-2.5 py-0.5 text-xs font-medium bg-gray-700 text-gray-300 rounded-full">
              {result.venue}
            </span>
          )}
          {result.year && (
            <span className="px-2.5 py-0.5 text-xs font-medium bg-gray-700 text-gray-300 rounded-full">
              {result.year}
            </span>
          )}
        </div>
      </div>

      {/* Authors */}
      {result.authors && (
        <p className="text-sm text-gray-400">{result.authors}</p>
      )}

      {/* Tags + Export */}
      {paperId && (
        <div className="flex items-center justify-between gap-3">
          <TagManager paperId={paperId} />
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={async () => {
                setBibtexLoading(true)
                try {
                  const bib = await exportBibtex(paperId)
                  const blob = new Blob([bib], { type: 'text/plain' })
                  const url = URL.createObjectURL(blob)
                  const a = document.createElement('a')
                  a.href = url
                  a.download = `${(result.title || 'paper').replace(/[^\w\s-]/g, '').trim() || 'paper'}.bib`
                  a.click()
                  URL.revokeObjectURL(url)
                } catch {
                  // ignore
                } finally {
                  setBibtexLoading(false)
                }
              }}
              disabled={bibtexLoading}
              className="flex items-center gap-1 text-xs px-2.5 py-1 rounded-md border transition-colors disabled:opacity-50"
              style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
              title="导出 BibTeX"
            >
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              BibTeX
            </button>
          </div>
        </div>
      )}

      {/* One-sentence contribution */}
      {result.one_sentence && (
        <div className="bg-blue-500/10 border-l-4 border-blue-500 p-4 rounded-r-lg">
          <p className="text-blue-300 text-sm leading-relaxed">{result.one_sentence}</p>
        </div>
      )}

      {/* Research problem */}
      {result.problem && (
        <section>
          <h2 className="text-lg font-semibold text-gray-200 mb-2">研究问题</h2>
          <p className="text-gray-300 text-sm leading-relaxed">{result.problem}</p>
        </section>
      )}

      {/* Key contributions */}
      {result.key_contributions && result.key_contributions.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold text-gray-200 mb-3">核心贡献</h2>
          <ol className="space-y-2">
            {result.key_contributions.map((item, idx) => (
              <li key={idx} className="flex gap-3 text-sm text-gray-300">
                <span className="flex-shrink-0 w-6 h-6 flex items-center justify-center bg-blue-500/20 text-blue-400 rounded-full text-xs font-medium">
                  {idx + 1}
                </span>
                <span className="leading-relaxed">{item}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {/* Related papers */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-gray-200">相关论文推荐</h2>
          <div className="flex items-center gap-2">
            {importMode ? (
              <>
                <button
                  onClick={selectAll}
                  className="flex items-center gap-1.5 px-3 py-1 text-xs text-gray-400 hover:text-gray-200 bg-gray-800 hover:bg-gray-700 rounded transition-colors"
                >
                  {selectedIndices.size === relatedPapers.length ? '取消全选' : '全选'}
                </button>
                <button
                  onClick={handleStartImport}
                  disabled={selectedIndices.size === 0 || isImporting}
                  className="flex items-center gap-1.5 px-3 py-1 text-xs text-white bg-blue-600 hover:bg-blue-500 rounded transition-colors disabled:opacity-50"
                >
                  导入选中 ({selectedIndices.size})
                </button>
                <button
                  onClick={handleExitImportMode}
                  disabled={isImporting}
                  className="flex items-center gap-1.5 px-3 py-1 text-xs text-gray-400 hover:text-gray-200 bg-gray-800 hover:bg-gray-700 rounded transition-colors disabled:opacity-50"
                >
                  取消
                </button>
              </>
            ) : (
              <>
                {relatedPapers.length > 0 && paperId && (
                  <>
                    <button
                      onClick={handleEnterImportMode}
                      className="flex items-center gap-1.5 px-3 py-1 text-xs text-gray-400 hover:text-green-400 bg-gray-800 hover:bg-gray-700 rounded transition-colors"
                    >
                      <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                          d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                      </svg>
                      导入到工作区
                    </button>
                    <button
                      onClick={() => {
                        setSelectedIndices(new Set(relatedPapers.map((_, i) => i)))
                        setImportMode(true)
                        setShowWsSelector(true)
                        // Load workspaces if not already loaded
                        if (workspaces.length === 0) {
                          setWsLoading(true)
                          listWorkspaces().then(setWorkspaces).catch(() => setWorkspaces([])).finally(() => setWsLoading(false))
                        }
                      }}
                      className="flex items-center gap-1.5 px-3 py-1 text-xs text-gray-400 hover:text-green-400 bg-gray-800 hover:bg-gray-700 rounded transition-colors"
                    >
                      <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                          d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                      </svg>
                      导入全部
                    </button>
                  </>
                )}
                {paperId && (
                  <button
                    onClick={handleRefresh}
                    disabled={isRefreshing}
                    className="flex items-center gap-1.5 px-3 py-1 text-xs text-gray-400 hover:text-blue-400 bg-gray-800 hover:bg-gray-700 rounded transition-colors disabled:opacity-50"
                  >
                    {isRefreshing ? (
                      <>
                        <div className="w-3 h-3 border border-gray-500 border-t-blue-400 rounded-full animate-spin" />
                        搜索中...
                      </>
                    ) : (
                      <>
                        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                        </svg>
                        刷新
                      </>
                    )}
                  </button>
                )}
              </>
            )}
          </div>
        </div>

        {relatedPapers.length > 0 ? (
          <div className="space-y-3">
            {relatedPapers.map((paper, idx) => (
              <div
                key={idx}
                className={`bg-gray-800/50 rounded-lg p-3 border transition-colors ${
                  importMode && selectedIndices.has(idx)
                    ? 'border-blue-500/60 bg-blue-500/5'
                    : 'border-gray-700/50'
                }`}
              >
                <div className="flex items-start gap-2.5">
                  {/* Checkbox (import mode only) */}
                  {importMode && !isImporting && !importResults && (
                    <button
                      onClick={() => toggleSelect(idx)}
                      className={`flex-shrink-0 mt-0.5 w-4 h-4 rounded border transition-colors flex items-center justify-center ${
                        selectedIndices.has(idx)
                          ? 'bg-blue-500 border-blue-500'
                          : 'border-gray-500 hover:border-gray-400'
                      }`}
                    >
                      {selectedIndices.has(idx) && (
                        <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                        </svg>
                      )}
                    </button>
                  )}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-200 mb-1">
                      {paper.url && paper.url.match(/^https?:\/\//) ? (
                        <a href={paper.url} target="_blank" rel="noopener noreferrer"
                           className="hover:text-blue-400 transition-colors">
                          {paper.title}
                        </a>
                      ) : paper.title}
                    </p>
                    {paper.authors && (
                      <p className="text-xs text-gray-400 mb-1">{paper.authors}</p>
                    )}
                    <div className="flex flex-wrap items-center gap-2 mb-1.5">
                      {paper.venue && (
                        <span className="text-xs bg-gray-700 text-gray-300 px-1.5 py-0.5 rounded">
                          {paper.venue}
                        </span>
                      )}
                      {paper.year > 0 && (
                        <span className="text-xs text-gray-500">{paper.year}</span>
                      )}
                      {paper.citation_count > 0 && (
                        <span className="text-xs text-yellow-500/80">
                          {paper.citation_count} citations
                        </span>
                      )}
                      {paper.doi && (
                        <a href={`https://doi.org/${paper.doi}`} target="_blank" rel="noopener noreferrer"
                           className="text-xs text-blue-400/70 hover:text-blue-400 transition-colors">
                          DOI: {paper.doi}
                        </a>
                      )}
                      {/* Import result badge */}
                      {importResults && importResults[idx] && (
                        <span className={`text-xs px-1.5 py-0.5 rounded ${
                          importResults[idx].status === 'local'
                            ? 'bg-green-500/20 text-green-400'
                            : importResults[idx].status === 'downloaded'
                              ? 'bg-blue-500/20 text-blue-400'
                              : 'bg-red-500/20 text-red-400'
                        }`}>
                          {importResults[idx].message}
                        </span>
                      )}
                    </div>
                    {paper.relevance && (
                      <p className="text-xs text-gray-500 italic">{paper.relevance}</p>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : relatedStatus === 'loading' && !isRefreshing ? (
          <div className="flex items-center justify-center gap-2 py-6">
            <div className="w-4 h-4 border-2 border-gray-500 border-t-blue-400 rounded-full animate-spin" />
            <span className="text-sm text-gray-400">主体分析已完成，正在后台搜索相关论文...</span>
          </div>
        ) : relatedStatus === 'failed' && !isRefreshing ? (
          <div
            className="text-center py-6 rounded-lg border"
            style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-surface)' }}
          >
            <p className="text-sm mb-1" style={{ color: '#ef4444' }}>相关论文搜索超时或失败</p>
            <p className="text-xs mb-3" style={{ color: 'var(--text-muted)' }}>不影响其他分析结果，可手动重试</p>
            {paperId && (
              <button
                onClick={handleRefresh}
                disabled={isRefreshing}
                className="text-xs text-blue-400 hover:text-blue-300 transition-colors disabled:opacity-50"
              >
                {isRefreshing ? '搜索中...' : '重新搜索'}
              </button>
            )}
          </div>
        ) : (
          <div className="text-center py-6">
            <p className="text-sm text-gray-500 mb-2">暂无相关论文推荐</p>
            {paperId && (
              <button
                onClick={handleRefresh}
                disabled={isRefreshing}
                className="text-xs text-blue-400 hover:text-blue-300 transition-colors disabled:opacity-50"
              >
                {isRefreshing ? '搜索中...' : '通过 Semantic Scholar 搜索'}
              </button>
            )}
          </div>
        )}

        {/* Import progress */}
        {isImporting && importProgress && (
          <div className="mt-3 p-3 bg-gray-800/80 rounded-lg border border-gray-700/50">
            <div className="flex items-center gap-2 mb-2">
              <div className="w-4 h-4 border-2 border-gray-500 border-t-blue-400 rounded-full animate-spin" />
              <span className="text-sm text-gray-300">
                正在导入... {importProgress.done}/{importProgress.total}
              </span>
            </div>
            <div className="w-full bg-gray-700 rounded-full h-1.5">
              <div
                className="bg-blue-500 h-1.5 rounded-full transition-all duration-500"
                style={{ width: `${(importProgress.done / importProgress.total) * 100}%` }}
              />
            </div>
            <p className="text-xs text-gray-500 mt-1.5">
              正在通过 Semantic Scholar 查找 PDF 并下载，请耐心等待...
            </p>
          </div>
        )}

        {/* Import results summary */}
        {importResults && !isImporting && (
          <div className="mt-3 p-3 bg-gray-800/80 rounded-lg border border-gray-700/50">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium text-gray-200">导入完成</span>
              <button
                onClick={handleExitImportMode}
                className="text-xs text-gray-400 hover:text-gray-200 transition-colors"
              >
                关闭
              </button>
            </div>
            <div className="flex gap-3 text-xs">
              <span className="text-green-400">
                本地导入: {importResults.filter(r => r.status === 'local').length}
              </span>
              <span className="text-blue-400">
                新下载: {importResults.filter(r => r.status === 'downloaded').length}
              </span>
              <span className="text-red-400">
                失败: {importResults.filter(r => r.status === 'failed').length}
              </span>
            </div>
          </div>
        )}
      </section>

      {/* Limitations */}
      {result.limitations && (
        <section>
          <h2 className="text-lg font-semibold text-gray-200 mb-2">局限性</h2>
          <p className="text-gray-400 text-sm leading-relaxed">{result.limitations}</p>
        </section>
      )}

      {/* Workspace selector modal */}
      {showWsSelector && (
        <div
          className="fixed inset-0 z-[200] flex items-center justify-center"
          style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
          onClick={() => !isImporting && setShowWsSelector(false)}
        >
          <div
            className="w-96 max-h-[70vh] rounded-xl border p-5 shadow-2xl flex flex-col"
            style={{ backgroundColor: 'var(--bg-elevated, #1a1a2e)', borderColor: 'var(--border, #333)' }}
            onClick={e => e.stopPropagation()}
          >
            <h3 className="text-base font-semibold text-gray-200 mb-1">选择工作区</h3>
            <p className="text-xs text-gray-400 mb-4">
              将 {selectedIndices.size} 篇论文导入到以下工作区：
            </p>

            {wsLoading ? (
              <div className="flex items-center justify-center py-8 gap-2">
                <div className="w-4 h-4 border-2 border-gray-500 border-t-blue-400 rounded-full animate-spin" />
                <span className="text-sm text-gray-400">加载工作区...</span>
              </div>
            ) : workspaces.length === 0 ? (
              <div className="text-center py-8">
                <p className="text-sm text-gray-500 mb-2">暂无工作区</p>
                <p className="text-xs text-gray-600">请先在「工作区」页面创建工作区</p>
              </div>
            ) : (
              <div className="flex-1 overflow-auto space-y-2 max-h-60">
                {workspaces.map(ws => (
                  <button
                    key={ws.id}
                    onClick={() => handleImportToWorkspace(ws.id)}
                    disabled={isImporting}
                    className="w-full text-left px-3 py-2.5 rounded-lg border transition-colors hover:border-blue-500/50 hover:bg-blue-500/5 disabled:opacity-50"
                    style={{ borderColor: 'var(--border, #333)' }}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium text-gray-200">{ws.name}</span>
                      <span className="text-xs text-gray-500">{ws.paper_count} 篇论文</span>
                    </div>
                  </button>
                ))}
              </div>
            )}

            <div className="flex justify-end mt-4">
              <button
                onClick={() => setShowWsSelector(false)}
                disabled={isImporting}
                className="px-4 py-1.5 text-xs rounded-md border transition-colors hover:opacity-80 disabled:opacity-50"
                style={{ borderColor: 'var(--border, #333)', color: 'var(--text-secondary, #aaa)' }}
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
