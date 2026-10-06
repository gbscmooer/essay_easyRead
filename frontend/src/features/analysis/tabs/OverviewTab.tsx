import { useState, useEffect, useCallback, useRef } from 'react'
import type { AnalysisResult, RelatedPaper } from '@/types'
import { refreshRelatedPapers, importRelatedPapers, exportBibtex } from '@/api/papers'
import type { ImportPaperResult } from '@/api/papers'
import { listWorkspaces } from '@/api/workspaces'
import { toast } from '@/hooks/useToast'
import type { WorkspaceItem } from '@/api/workspaces'
import { TagManager } from '../TagManager'
import { LoadingSpinner } from '@/ds-entry'

const SPRING = 'var(--ease-press)'

interface OverviewTabProps {
  result: AnalysisResult
  paperId?: number
  analysisStatus?: 'idle' | 'extracting' | 'analyzing' | 'generating' | 'done' | 'failed'
  /** 相关论文模块独立状态：loading 搜索中 / failed 超时或失败 */
  relatedStatus?: 'idle' | 'loading' | 'ok' | 'failed'
}

export function OverviewTab({ result, paperId, analysisStatus, relatedStatus }: OverviewTabProps) {
  const [relatedPapers, setRelatedPapers] = useState<RelatedPaper[]>(result.related_papers || [])
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [bibtexLoading, setBibtexLoading] = useState(false)

  // Import mode state
  const [importMode, setImportMode] = useState(false)
  const [selectedIndices, setSelectedIndices] = useState<Set<number>>(new Set())
  const [isImporting, setIsImporting] = useState(false)
  const [importProgress, setImportProgress] = useState<{ done: number; total: number } | null>(null)
  const [importResults, setImportResults] = useState<ImportPaperResult[] | null>(null)

  const [importResultsByIndex, setImportResultsByIndex] = useState<Record<number, ImportPaperResult>>({})

  // Workspace selector state
  const [showWsSelector, setShowWsSelector] = useState(false)
  const [workspaces, setWorkspaces] = useState<WorkspaceItem[]>([])
  const [wsLoading, setWsLoading] = useState(false)
  const wsModalRef = useRef<HTMLDivElement>(null)

  // Focus first interactive element when workspace selector opens
  useEffect(() => {
    if (!showWsSelector || !wsModalRef.current) return
    const first = wsModalRef.current.querySelector<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    )
    first?.focus()
  }, [showWsSelector, wsLoading])

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
    setImportResultsByIndex({})
    setImportProgress(null)
    // Load workspaces
    setWsLoading(true)
    try {
      const wss = await listWorkspaces()
      setWorkspaces(wss)
    } catch {
      setWorkspaces([])
      toast({ type: 'warning', message: '加载工作区列表失败' })
    } finally {
      setWsLoading(false)
    }
  }, [])

  // Exit import mode
  const handleExitImportMode = useCallback(() => {
    setImportMode(false)
    setSelectedIndices(new Set())
    setImportResults(null)
    setImportResultsByIndex({})
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
    const requestIndices = Array.from(selectedIndices)
    const papersToImport = requestIndices.map(idx => relatedPapers[idx])
    setImportResultsByIndex({})
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
      setImportResultsByIndex(Object.fromEntries(requestIndices.map((idx, order) => [idx, resp.results[order]])))
      setImportProgress({ done: papersToImport.length, total: papersToImport.length })
    } catch {
      const failed = papersToImport.map(p => ({ title: p.title, status: 'failed' as const, message: '导入请求失败' }))
      setImportResults(failed)
      setImportResultsByIndex(Object.fromEntries(requestIndices.map((idx, order) => [idx, failed[order]])))
    } finally {
      setIsImporting(false)
    }
  }, [selectedIndices, relatedPapers])

  // ── shared presentational helpers ────────────────────────────────────────
  /** Ghost pill button: surface tint + hairline, hover lifts luminance (never color). */
  const ghostPill = {
    color: 'var(--text-secondary)',
    backgroundColor: 'var(--bg-surface)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--seed-radius, 6px)',
    transitionTimingFunction: SPRING,
  } as const
  const onGhostEnter = (e: React.MouseEvent) => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-elevated)' }
  const onGhostLeave = (e: React.MouseEvent) => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-surface)' }

  return (
    <div className="space-y-8">
      {/* Title — editorial display type */}
      <header>
        <h1
          className="text-display text-[27px] mb-3"
          style={{ color: 'var(--text-primary)', letterSpacing: '-0.022em', lineHeight: 1.12 }}
        >
          {result.title}
        </h1>
        <div className="flex flex-wrap items-center gap-2.5">
          {result.venue && (
            <span
              className="text-[11px] font-medium px-2.5 py-1 rounded-md"
              style={{ backgroundColor: 'var(--bg-surface)', color: 'var(--text-secondary)', border: '1px solid var(--border)' }}
            >
              {result.venue}
            </span>
          )}
          {result.year && (
            <span className="text-[12px] tabular-nums" style={{ color: 'var(--text-muted)' }}>{result.year}</span>
          )}
        </div>
        {result.authors && (
          <p className="text-[13px] mt-2.5 leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{result.authors}</p>
        )}
      </header>

      {/* Tags + BibTeX export */}
      {paperId && (
        <div className="flex items-center justify-between gap-3">
          <TagManager paperId={paperId} analysisStatus={analysisStatus} />
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
                } catch (e: unknown) {
                  toast({ type: 'error', message: e instanceof Error ? e.message : '导出 BibTeX 失败' })
                } finally {
                  setBibtexLoading(false)
                }
              }}
              disabled={bibtexLoading}
              className="flex items-center gap-1.5 text-[12px] px-3 py-1.5 transition-all duration-200 disabled:opacity-50"
              style={ghostPill}
              onMouseEnter={onGhostEnter}
              onMouseLeave={onGhostLeave}
              title="导出 BibTeX"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              BibTeX
            </button>
          </div>
        </div>
      )}

      {/* One-sentence contribution — surface-tinted highlight (no left-border accent card) */}
      {result.one_sentence && (
        <div
          className="p-4 rounded-xl border border-blue-500/15 shadow-xs"
          style={{ backgroundColor: 'var(--accent-subtle)' }}
        >
          <p className="text-label mb-2" style={{ color: 'var(--accent)' }}>核心一句</p>
          <p className="text-[15px] leading-relaxed" style={{ color: 'var(--text-primary)', letterSpacing: '-0.005em' }}>
            {result.one_sentence}
          </p>
        </div>
      )}

      {/* Research problem */}
      {result.problem && (
        <section>
          <h2 className="text-heading text-[15px] mb-2.5" style={{ color: 'var(--text-primary)' }}>研究问题</h2>
          <p className="text-[13px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{result.problem}</p>
        </section>
      )}

      {/* Key contributions */}
      {result.key_contributions && result.key_contributions.length > 0 && (
        <section>
          <h2 className="text-heading text-[15px] mb-3.5" style={{ color: 'var(--text-primary)' }}>核心贡献</h2>
          <ol className="space-y-3">
            {result.key_contributions.map((item, idx) => (
              <li key={idx} className="flex gap-3.5 text-[13px]" style={{ color: 'var(--text-secondary)' }}>
                <span
                  className="flex-shrink-0 w-6 h-6 rounded-md flex items-center justify-center text-[11px] font-medium tabular-nums mt-px"
                  style={{ backgroundColor: 'var(--bg-elevated)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}
                >
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
        <div className="flex items-center justify-between gap-3 mb-3.5">
          <h2 className="text-heading text-[15px]" style={{ color: 'var(--text-primary)' }}>相关论文推荐</h2>
          <div className="flex items-center gap-2">
            {importMode ? (
              <>
                <button
                  onClick={selectAll}
                  className="px-3 py-1.5 text-[12px] transition-all duration-200"
                  style={ghostPill}
                  onMouseEnter={onGhostEnter}
                  onMouseLeave={onGhostLeave}
                >
                  {selectedIndices.size === relatedPapers.length ? '取消全选' : '全选'}
                </button>
                <button
                  onClick={handleStartImport}
                  disabled={selectedIndices.size === 0 || isImporting}
                  className="px-3.5 py-1.5 text-[12px] font-medium transition-all duration-200 disabled:opacity-50"
                  style={{ backgroundColor: 'var(--accent)', color: '#ffffff', transitionTimingFunction: SPRING }}
                  onMouseEnter={e => { (e.currentTarget as HTMLElement).style.opacity = '0.9' }}
                  onMouseLeave={e => { (e.currentTarget as HTMLElement).style.opacity = '1' }}
                >
                  导入选中 ({selectedIndices.size})
                </button>
                <button
                  onClick={handleExitImportMode}
                  disabled={isImporting}
                  className="px-3 py-1.5 text-[12px] transition-all duration-200 disabled:opacity-50"
                  style={ghostPill}
                  onMouseEnter={onGhostEnter}
                  onMouseLeave={onGhostLeave}
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
                      className="flex items-center gap-1.5 px-3 py-1.5 text-[12px] transition-all duration-200"
                      style={ghostPill}
                      onMouseEnter={onGhostEnter}
                      onMouseLeave={onGhostLeave}
                    >
                      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
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
                      className="flex items-center gap-1.5 px-3 py-1.5 text-[12px] transition-all duration-200"
                      style={ghostPill}
                      onMouseEnter={onGhostEnter}
                      onMouseLeave={onGhostLeave}
                    >
                      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
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
                    className="flex items-center gap-1.5 px-3 py-1.5 text-[12px] transition-all duration-200 disabled:opacity-50"
                    style={ghostPill}
                    onMouseEnter={onGhostEnter}
                    onMouseLeave={onGhostLeave}
                  >
                    {isRefreshing ? (
                      <>
                        <LoadingSpinner size="xs" />
                        搜索中...
                      </>
                    ) : (
                      <>
                        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
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
          <div>
            {relatedPapers.map((paper, idx) => {
              const selected = importMode && selectedIndices.has(idx)
              return (
                <div
                  key={idx}
                  className="list-row transition-all duration-200"
                  style={selected
                    ? { backgroundColor: 'var(--accent-subtle)', borderLeft: '2px solid var(--accent)', paddingLeft: 'calc(0.875rem - 2px)', transitionTimingFunction: SPRING }
                    : { transitionTimingFunction: SPRING }}
                >
                  <div className="flex items-start gap-3">
                    {/* Checkbox (import mode only) */}
                    {importMode && !isImporting && !importResults && (
                      <button
                        onClick={() => toggleSelect(idx)}
                        className="flex-shrink-0 mt-0.5 w-4 h-4 border transition-colors flex items-center justify-center"
                        style={selectedIndices.has(idx) ? { backgroundColor: 'var(--accent)', borderColor: 'var(--accent)' } : { borderColor: 'var(--text-muted)' }}
                      >
                        {selectedIndices.has(idx) && (
                          <svg className="w-3 h-3" style={{ color: '#fff' }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                          </svg>
                        )}
                      </button>
                    )}
                    <div className="flex-1 min-w-0">
                      <p className="text-[14px] font-medium mb-1 leading-snug" style={{ color: 'var(--text-primary)' }}>
                        {paper.url && paper.url.match(/^https?:\/\//) ? (
                          <a href={paper.url} target="_blank" rel="noopener noreferrer"
                             className="transition-colors hover:opacity-80" style={{ color: 'var(--text-primary)' }}>
                            {paper.title}
                          </a>
                        ) : paper.title}
                      </p>
                      {paper.authors && (
                        <p className="text-[12px] mb-1.5" style={{ color: 'var(--text-secondary)' }}>{paper.authors}</p>
                      )}
                      <div className="flex flex-wrap items-center gap-2.5 text-[12px]">
                        {paper.venue && (
                          <span style={{ color: 'var(--text-muted)' }}>{paper.venue}</span>
                        )}
                        {paper.year > 0 && (
                          <span className="tabular-nums" style={{ color: 'var(--text-muted)' }}>{paper.year}</span>
                        )}
                        {paper.citation_count > 0 && (
                          <span className="tabular-nums" style={{ color: 'var(--text-muted)' }}>{paper.citation_count} 引用</span>
                        )}
                        {paper.doi && (
                          <a href={`https://doi.org/${paper.doi}`} target="_blank" rel="noopener noreferrer"
                             className="transition-colors hover:opacity-80" style={{ color: 'var(--accent)' }}>
                            DOI: {paper.doi}
                          </a>
                        )}
                        {/* Import result — subtle text tint, no filled badge */}
                        {importResults && importResultsByIndex[idx] && (
                          <span
                            className="font-medium"
                            style={{
                              color: importResultsByIndex[idx].status === 'local'
                                ? 'var(--success)'
                                : importResultsByIndex[idx].status === 'downloaded'
                                  ? 'var(--accent)'
                                  : importResultsByIndex[idx].status === 'registered'
                                    ? 'var(--text-muted)'
                                    : 'var(--error)',
                            }}
                          >
                            {importResultsByIndex[idx].message}
                          </span>
                        )}
                      </div>
                      {paper.relevance && (
                        <p className="text-[12px] italic mt-1.5 leading-relaxed" style={{ color: 'var(--text-muted)' }}>{paper.relevance}</p>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        ) : relatedStatus === 'loading' && !isRefreshing ? (
          <div className="flex items-center justify-center gap-2.5 py-8">
            <LoadingSpinner size="sm" />
            <span className="text-[13px]" style={{ color: 'var(--text-secondary)' }}>主体分析已完成，正在后台搜索相关论文…</span>
          </div>
        ) : relatedStatus === 'failed' && !isRefreshing ? (
          <div
            className="text-center py-8"
          >
            <p className="text-[13px] mb-1" style={{ color: 'var(--error)' }}>相关论文搜索超时或失败</p>
            <p className="text-[12px] mb-3" style={{ color: 'var(--text-muted)' }}>不影响其他分析结果，可手动重试</p>
            {paperId && (
              <button
                onClick={handleRefresh}
                disabled={isRefreshing}
                className="text-[12px] transition-colors disabled:opacity-50 hover:opacity-80"
                style={{ color: 'var(--accent)' }}
              >
                {isRefreshing ? '搜索中…' : '重新搜索'}
              </button>
            )}
          </div>
        ) : (
          <div className="text-center py-8">
            <p className="text-[13px] mb-2" style={{ color: 'var(--text-muted)' }}>暂无相关论文推荐</p>
            {paperId && (
              <button
                onClick={handleRefresh}
                disabled={isRefreshing}
                className="text-[12px] transition-colors disabled:opacity-50 hover:opacity-80"
                style={{ color: 'var(--accent)' }}
              >
                {isRefreshing ? '搜索中…' : '通过 Semantic Scholar 搜索'}
              </button>
            )}
          </div>
        )}

        {/* Import progress */}
        {isImporting && importProgress && (
          <div className="mt-3 content-section">
            <div className="flex items-center gap-2.5 mb-2.5">
              <LoadingSpinner size="sm" />
              <span className="text-[13px]" style={{ color: 'var(--text-secondary)' }}>
                正在导入… {importProgress.done}/{importProgress.total}
              </span>
            </div>
            <div className="w-full h-1.5 overflow-hidden" style={{ backgroundColor: 'var(--bg-elevated)' }}>
              <div
                className="h-1.5 transition-all duration-500"
                style={{ width: `${(importProgress.done / importProgress.total) * 100}%`, backgroundColor: 'var(--accent)' }}
              />
            </div>
            <p className="text-[12px] mt-2" style={{ color: 'var(--text-muted)' }}>
              正在通过 Semantic Scholar 查找 PDF 并下载，请耐心等待…
            </p>
          </div>
        )}

        {/* Import results summary — subtle text tints */}
        {importResults && !isImporting && (
          <div className="mt-3 content-section">
            <div className="flex items-center justify-between mb-2.5">
              <span className="text-[13px] font-medium" style={{ color: 'var(--text-primary)' }}>导入完成</span>
              <button
                onClick={handleExitImportMode}
                className="text-[12px] transition-colors hover:opacity-80" style={{ color: 'var(--text-secondary)' }}
              >
                关闭
              </button>
            </div>
            <div className="flex gap-4 text-[12px] font-medium">
              <span style={{ color: 'var(--success)' }}>
                本地导入 {importResults.filter(r => r.status === 'local').length}
              </span>
              <span style={{ color: 'var(--accent)' }}>
                新下载 {importResults.filter(r => r.status === 'downloaded').length}
              </span>
              <span style={{ color: 'var(--text-muted)' }}>
                已登记 {importResults.filter(r => r.status === 'registered').length}
              </span>
              <span style={{ color: 'var(--error)' }}>
                失败 {importResults.filter(r => r.status === 'failed').length}
              </span>
            </div>
          </div>
        )}
      </section>

      {/* Limitations */}
      {result.limitations && (
        <section>
          <h2 className="text-heading text-[15px] mb-2.5" style={{ color: 'var(--text-primary)' }}>局限性</h2>
          <p className="text-[13px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{result.limitations}</p>
        </section>
      )}

      {/* Workspace selector modal — frosted backdrop + elevated card */}
      {showWsSelector && (
        <div
          className="fixed inset-0 z-[200] flex items-center justify-center"
          style={{ backgroundColor: 'rgba(0,0,0,0.4)', backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)' }}
          onMouseDown={() => !isImporting && setShowWsSelector(false)}
        >
          <div
            ref={wsModalRef}
            role="dialog"
            aria-modal="true"
            className="w-96 max-h-[70vh] p-6 flex flex-col"
            style={{
              backgroundColor: 'var(--bg-elevated)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--seed-radius, 12px)',
              boxShadow: '0 24px 48px rgba(0,0,0,0.16), 0 8px 16px rgba(0,0,0,0.08)',
            }}
            onMouseDown={e => e.stopPropagation()}
          >
            <h3 className="text-heading text-[15px] mb-1" style={{ color: 'var(--text-primary)' }}>选择工作区</h3>
            <p className="text-[12px] mb-4" style={{ color: 'var(--text-secondary)' }}>
              将 {selectedIndices.size} 篇论文导入到以下工作区：
            </p>

            {wsLoading ? (
              <div className="flex items-center justify-center py-8 gap-2.5">
                <LoadingSpinner size="sm" />
                <span className="text-[13px]" style={{ color: 'var(--text-secondary)' }}>加载工作区…</span>
              </div>
            ) : workspaces.length === 0 ? (
              <div className="text-center py-8">
                <p className="text-[13px] mb-2" style={{ color: 'var(--text-muted)' }}>暂无工作区</p>
                <p className="text-[12px]" style={{ color: 'var(--text-muted)' }}>请先在「工作区」页面创建工作区</p>
              </div>
            ) : (
              <div className="flex-1 overflow-auto space-y-2 max-h-60 -mx-1 px-1">
                {workspaces.map(ws => (
                  <button
                    key={ws.id}
                    onClick={() => handleImportToWorkspace(ws.id)}
                    disabled={isImporting}
                    className="w-full text-left list-row card-hover transition-all duration-200 disabled:opacity-50"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-[13px] font-medium" style={{ color: 'var(--text-primary)' }}>{ws.name}</span>
                      <span className="text-[12px] tabular-nums" style={{ color: 'var(--text-muted)' }}>{ws.paper_count} 篇</span>
                    </div>
                  </button>
                ))}
              </div>
            )}

            <div className="flex justify-end mt-5">
              <button
                onClick={() => setShowWsSelector(false)}
                disabled={isImporting}
                className="px-4 py-2 text-[12px] transition-all duration-200 disabled:opacity-50"
                style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)', transitionTimingFunction: SPRING }}
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
