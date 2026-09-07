/**
 * HomePage — Origin research intake.
 *
 * The data boundary stays unchanged: the page still loads papers and
 * workspaces from the existing API and hands uploads to the reader route.
 * The visible layer is a hard-edged 12 / 8 / 6 column composition.
 */

import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { listPapers, uploadPaperWithProgress } from '@/api/papers'
import { listWorkspaces, type WorkspaceItem } from '@/api/workspaces'
import { UploadZone } from '@/features/upload/UploadZone'
import { toast } from '@/hooks/useToast'
import type { Paper } from '@/types'
const STATUS_MAP: Record<string, { label: string; color: string; bg: string; dot: string }> = {
  done: { label: '已完成', color: 'text-emerald-600 dark:text-emerald-400', bg: 'bg-emerald-50 dark:bg-emerald-950/40 border-emerald-200 dark:border-emerald-800/60', dot: 'bg-emerald-500' },
  extracting: { label: '提取中', color: 'text-blue-600 dark:text-blue-400', bg: 'bg-blue-50 dark:bg-blue-950/40 border-blue-200 dark:border-blue-800/60', dot: 'bg-blue-500 animate-pulse' },
  analyzing: { label: '分析中', color: 'text-blue-600 dark:text-blue-400', bg: 'bg-blue-50 dark:bg-blue-950/40 border-blue-200 dark:border-blue-800/60', dot: 'bg-blue-500 animate-pulse' },
  indexed: { label: '待解析', color: 'text-amber-600 dark:text-amber-400', bg: 'bg-amber-50 dark:bg-amber-950/40 border-amber-200 dark:border-amber-800/60', dot: 'bg-amber-500' },
  failed: { label: '解析失败', color: 'text-rose-600 dark:text-rose-400', bg: 'bg-rose-50 dark:bg-rose-950/40 border-rose-200 dark:border-rose-800/60', dot: 'bg-rose-500' },
  pending: { label: '待处理', color: 'text-gray-600 dark:text-gray-400', bg: 'bg-gray-50 dark:bg-gray-800/40 border-gray-200 dark:border-gray-700', dot: 'bg-gray-400' },
}

function StatusBadge({ status }: { status: string }) {
  const conf = STATUS_MAP[status] ?? STATUS_MAP.pending
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium border ${conf.bg} ${conf.color}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${conf.dot}`} />
      {conf.label}
    </span>
  )
}

export default function HomePage() {
  const navigate = useNavigate()
  const [recentPapers, setRecentPapers] = useState<Paper[]>([])
  const [recentWorkspaces, setRecentWorkspaces] = useState<WorkspaceItem[]>([])
  const [stats, setStats] = useState({ papers: 0, workspaces: 0, expanded: 0, wsPapers: 0 })
  const [isUploading, setIsUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    abortRef.current = controller

    Promise.all([
      listPapers({ page: 1, per_page: 6, source: 'uploaded' })
        .then(response => {
          setRecentPapers(response.papers)
          setStats(current => ({ ...current, papers: response.total }))
        })
        .catch(() => { toast({ type: 'error', message: '加载最近论文失败' }) }),
      listWorkspaces()
        .then(workspaces => {
          const sorted = workspaces.sort(
            (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
          )
          setRecentWorkspaces(sorted.slice(0, 6))
          setStats(current => ({
            ...current,
            workspaces: workspaces.length,
            wsPapers: workspaces.reduce((total, workspace) => total + (workspace.paper_count || 0), 0),
          }))
        })
        .catch(() => { toast({ type: 'error', message: '加载工作区失败' }) }),
      listPapers({ per_page: 1, source: 'expanded' })
        .then(response => setStats(current => ({ ...current, expanded: response.total })))
        .catch(() => {}),
    ]).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })

    return () => controller.abort()
  }, [])

  const handleUpload = async (file: File) => {
    setIsUploading(true)
    setUploadProgress(0)
    setUploadError(null)

    try {
      const paper = await uploadPaperWithProgress(file, setUploadProgress)
      toast({ type: 'success', message: '上传成功，正在自动分析...' })
      navigate(`/reader/${paper.id}`, { state: { autoAnalyze: true } })
    } catch {
      setUploadError('上传失败，请检查文件格式后重试')
      toast({ type: 'error', message: '上传失败，请检查文件格式后重试' })
    } finally {
      setIsUploading(false)
    }
  }

  return (
    <div className="w-full max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8 flex flex-col gap-8">
      {/* Hero 区域 */}
      <section className="flex flex-col gap-6" aria-labelledby="home-title">
        <div className="flex flex-col gap-2">
          <div className="inline-flex items-center gap-2 self-start px-2.5 py-1 rounded-full text-xs font-semibold bg-blue-50 dark:bg-blue-950/60 text-blue-600 dark:text-blue-400 border border-blue-200 dark:border-blue-900/40">
            <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
            面向研究者的学术文献精读系统
          </div>
          <h1 id="home-title" className="text-2xl sm:text-3xl font-bold tracking-tight text-[var(--text-primary)]">
            论文精读与深度分析
          </h1>
          <p className="text-sm text-[var(--text-secondary)] max-w-2xl leading-relaxed">
            上传 PDF 文档，AI 自动提炼核心贡献、方法细节、实验评估与开源代码映射，助您免除逐行硬读即可高效把握前沿要点。
          </p>
        </div>

        {/* 拖拽上传区 */}
        <div className="w-full">
          <UploadZone
            onUpload={handleUpload}
            isUploading={isUploading}
            uploadProgress={uploadProgress}
          />
          {uploadError && (
            <p className="mt-2 text-xs text-rose-500 font-medium" role="alert">{uploadError}</p>
          )}
        </div>
      </section>

      {/* 统计指标卡片 */}
      <section className="grid grid-cols-2 sm:grid-cols-4 gap-4" aria-label="数据概览">
        <div className="p-4 rounded-xl bg-[var(--bg-surface)] border border-[var(--border)] shadow-xs flex flex-col gap-1">
          <span className="text-xs font-medium text-[var(--text-muted)]">论文精读库</span>
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-bold text-[var(--text-primary)] tabular-nums">{stats.papers}</span>
            <span className="text-xs text-[var(--text-muted)]">篇已收录</span>
          </div>
        </div>

        <div className="p-4 rounded-xl bg-[var(--bg-surface)] border border-[var(--border)] shadow-xs flex flex-col gap-1">
          <span className="text-xs font-medium text-[var(--text-muted)]">课题工作区</span>
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-bold text-[var(--text-primary)] tabular-nums">{stats.workspaces}</span>
            <span className="text-xs text-[var(--text-muted)]">{stats.wsPapers} 篇关联</span>
          </div>
        </div>

        <div className="p-4 rounded-xl bg-[var(--bg-surface)] border border-[var(--border)] shadow-xs flex flex-col gap-1">
          <span className="text-xs font-medium text-[var(--text-muted)]">联网扩展文献</span>
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-bold text-[var(--text-primary)] tabular-nums">{stats.expanded}</span>
            <span className="text-xs text-[var(--text-muted)]">篇图谱挖掘</span>
          </div>
        </div>

        <div className="p-4 rounded-xl bg-[var(--bg-surface)] border border-[var(--border)] shadow-xs flex flex-col gap-1">
          <span className="text-xs font-medium text-[var(--text-muted)]">文献总沉淀</span>
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-bold text-blue-600 dark:text-blue-400 tabular-nums">
              {stats.papers + stats.expanded}
            </span>
            <span className="text-xs text-[var(--text-muted)]">篇文献</span>
          </div>
        </div>
      </section>

      {/* 主体两栏结构 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8 items-start">
        {/* 左栏：最近解析论文 (占 2/3) */}
        <section className="lg:col-span-2 flex flex-col gap-4" aria-labelledby="recent-papers-title">
          <div className="flex items-center justify-between">
            <h2 id="recent-papers-title" className="text-base font-semibold text-[var(--text-primary)] flex items-center gap-2">
              <svg className="w-4 h-4 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              最近解析论文
            </h2>
            <button
              type="button"
              onClick={() => navigate('/library')}
              className="text-xs font-medium text-blue-600 dark:text-blue-400 hover:underline flex items-center gap-1 cursor-pointer"
            >
              进入论文库
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
            </button>
          </div>

          {loading ? (
            <div className="flex flex-col gap-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="h-16 rounded-xl bg-[var(--bg-surface)] border border-[var(--border)] animate-pulse" />
              ))}
            </div>
          ) : recentPapers.length > 0 ? (
            <div className="flex flex-col gap-2.5">
              {recentPapers.map(paper => (
                <button
                  key={paper.id}
                  type="button"
                  onClick={() => navigate(`/reader/${paper.id}`)}
                  className="w-full text-left p-4 rounded-xl bg-[var(--bg-surface)] border border-[var(--border)] hover:border-blue-500/50 hover:shadow-sm transition-all group flex items-center justify-between gap-4 cursor-pointer"
                >
                  <div className="flex flex-col gap-1 min-w-0 flex-1">
                    <h3 className="text-sm font-semibold text-[var(--text-primary)] group-hover:text-blue-600 dark:group-hover:text-blue-400 transition-colors truncate">
                      {paper.title || paper.filename}
                    </h3>
                    <div className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
                      <span>{paper.venue || '本地文档'}</span>
                      {paper.year && <span>· {paper.year}</span>}
                      <span>· {new Date(paper.created_at).toLocaleDateString('zh-CN')}</span>
                    </div>
                  </div>

                  <div className="flex items-center gap-3 shrink-0">
                    <StatusBadge status={paper.status} />
                    <svg className="w-4 h-4 text-[var(--text-muted)] group-hover:text-blue-600 group-hover:translate-x-0.5 transition-all" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                    </svg>
                  </div>
                </button>
              ))}
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg-surface)] p-8 text-center flex flex-col items-center justify-center gap-3">
              <div className="w-10 h-10 rounded-full bg-blue-50 dark:bg-blue-950/50 flex items-center justify-center text-blue-600 dark:text-blue-400">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
              </div>
              <h3 className="text-sm font-semibold text-[var(--text-primary)]">暂无精读记录</h3>
              <p className="text-xs text-[var(--text-muted)] max-w-sm">在上方区域上传首篇 PDF 论文，系统将自动开始解析并生成精读报告。</p>
            </div>
          )}
        </section>

        {/* 右栏：快捷入口与最近工作区 (占 1/3) */}
        <aside className="flex flex-col gap-6" aria-label="快捷入口与课题集合">
          {/* 快捷通道 */}
          <div className="flex flex-col gap-3">
            <h2 className="text-base font-semibold text-[var(--text-primary)]">快速通道</h2>
            <div className="flex flex-col gap-2">
              <button
                type="button"
                onClick={() => navigate('/library')}
                className="w-full text-left p-3.5 rounded-xl bg-[var(--bg-surface)] border border-[var(--border)] hover:border-blue-500/50 hover:bg-[var(--bg-elevated)] transition-all flex items-center justify-between group cursor-pointer"
              >
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-indigo-50 dark:bg-indigo-950/60 text-indigo-600 dark:text-indigo-400 flex items-center justify-center">
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
                    </svg>
                  </div>
                  <div>
                    <strong className="text-xs font-semibold text-[var(--text-primary)] block group-hover:text-blue-600 transition-colors">论文库检索</strong>
                    <span className="text-[11px] text-[var(--text-muted)]">全文多维智能搜索</span>
                  </div>
                </div>
                <svg className="w-4 h-4 text-[var(--text-muted)] group-hover:text-blue-600 group-hover:translate-x-0.5 transition-all" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              </button>

              <button
                type="button"
                onClick={() => navigate('/workspace')}
                className="w-full text-left p-3.5 rounded-xl bg-[var(--bg-surface)] border border-[var(--border)] hover:border-blue-500/50 hover:bg-[var(--bg-elevated)] transition-all flex items-center justify-between group cursor-pointer"
              >
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-emerald-50 dark:bg-emerald-950/60 text-emerald-600 dark:text-emerald-400 flex items-center justify-center">
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                    </svg>
                  </div>
                  <div>
                    <strong className="text-xs font-semibold text-[var(--text-primary)] block group-hover:text-blue-600 transition-colors">研究工作区</strong>
                    <span className="text-[11px] text-[var(--text-muted)]">组织主题与引文图谱</span>
                  </div>
                </div>
                <svg className="w-4 h-4 text-[var(--text-muted)] group-hover:text-blue-600 group-hover:translate-x-0.5 transition-all" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              </button>

              <button
                type="button"
                onClick={() => navigate('/explore')}
                className="w-full text-left p-3.5 rounded-xl bg-[var(--bg-surface)] border border-[var(--border)] hover:border-blue-500/50 hover:bg-[var(--bg-elevated)] transition-all flex items-center justify-between group cursor-pointer"
              >
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-amber-50 dark:bg-amber-950/60 text-amber-600 dark:text-amber-400 flex items-center justify-center">
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                    </svg>
                  </div>
                  <div>
                    <strong className="text-xs font-semibold text-[var(--text-primary)] block group-hover:text-blue-600 transition-colors">领域全景探索</strong>
                    <span className="text-[11px] text-[var(--text-muted)]">问句驱动与综述调研</span>
                  </div>
                </div>
                <svg className="w-4 h-4 text-[var(--text-muted)] group-hover:text-blue-600 group-hover:translate-x-0.5 transition-all" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              </button>
            </div>
          </div>

          {/* 最近工作区 */}
          {recentWorkspaces.length > 0 && (
            <div className="flex flex-col gap-3">
              <div className="flex items-center justify-between">
                <h2 className="text-base font-semibold text-[var(--text-primary)]">最近工作区</h2>
                <button
                  type="button"
                  onClick={() => navigate('/workspace')}
                  className="text-xs font-medium text-blue-600 dark:text-blue-400 hover:underline cursor-pointer"
                >
                  全部
                </button>
              </div>

              <div className="flex flex-col gap-2">
                {recentWorkspaces.map(workspace => (
                  <button
                    key={workspace.id}
                    type="button"
                    onClick={() => navigate(`/workspace/${workspace.id}`)}
                    className="w-full text-left p-3 rounded-xl bg-[var(--bg-surface)] border border-[var(--border)] hover:border-blue-500/50 hover:bg-[var(--bg-elevated)] transition-all flex items-center justify-between group cursor-pointer"
                  >
                    <div className="flex flex-col min-w-0 pr-2">
                      <span className="text-xs font-semibold text-[var(--text-primary)] group-hover:text-blue-600 transition-colors truncate">
                        {workspace.name}
                      </span>
                      <span className="text-[11px] text-[var(--text-muted)] mt-0.5">
                        {workspace.paper_count} 篇文献 · {new Date(workspace.updated_at).toLocaleDateString('zh-CN')}
                      </span>
                    </div>
                    <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-[var(--bg-elevated)] text-[var(--text-muted)] border border-[var(--border)] shrink-0">
                      {workspace.paper_count} 篇
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </aside>
      </div>
    </div>
  )
}
