/**
 * 首页：PDF 上传、论文与工作区摘要、快捷入口。
 * 使用 Tailwind 响应式布局；上传成功后进入论文阅读页。
 */

import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { listPapers, uploadPaperWithProgress } from '@/api/papers'
import { listWorkspaces, type WorkspaceItem } from '@/api/workspaces'
import { parseServerDate } from '@/utils/datetime'
import { UploadZone } from '@/features/upload/UploadZone'
import { APP_VERSION } from '@/components/VersionPopup'
import { toast } from '@/hooks/useToast'
import type { Paper } from '@/types'
const STATUS_MAP: Record<string, { label: string; tone: 'done' | 'busy' | 'wait' | 'fail' | 'idle' }> = {
  done: { label: '已完成', tone: 'done' },
  extracting: { label: '提取中', tone: 'busy' },
  analyzing: { label: '分析中', tone: 'busy' },
  indexed: { label: '待解析', tone: 'wait' },
  failed: { label: '解析失败', tone: 'fail' },
  pending: { label: '待处理', tone: 'idle' },
}

function StatusBadge({ status }: { status: string }) {
  const conf = STATUS_MAP[status] ?? STATUS_MAP.pending
  return <span className="mg-mark" data-tone={conf.tone}>{conf.label}</span>
}

/** 头版日期行：卷次（版本号）· 日期 */
function frontDate(): string {
  return new Date().toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' })
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
            (a, b) => parseServerDate(b.updated_at).getTime() - parseServerDate(a.updated_at).getTime(),
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

  const venueOf = (paper: Paper) => paper.venue || (paper.source === 'uploaded' ? '本地文档' : paper.source) || '本地文档'

  return (
    <div className="mg-page">
      <div className="mg-folio-line">
        <span>研究者的阅读台 · 第 {APP_VERSION} 版</span>
        <span>{frontDate()}</span>
      </div>
      <div className="mg-rule-double" />

      {/* 头版：标题 + 投稿框 | 馆藏 */}
      <div className="mg-front">
        <section className="mg-front__lead" aria-labelledby="home-title">
          <span className="mg-kicker">面向研究者的学术文献精读</span>
          <h1 id="home-title" className="mg-headline">论文精读与深度分析</h1>
          <p className="mg-deck">
            上传 PDF，系统提炼核心贡献、方法细节、实验评估，并把论文映射到开源代码；
            文献研究从一个问题出发，检索、筛选、精读，写出每句都可追溯到原文的答案。
          </p>
          <UploadZone onUpload={handleUpload} isUploading={isUploading} uploadProgress={uploadProgress} />
          {uploadError && <p className="mg-drop__error" role="alert">{uploadError}</p>}
        </section>

        <aside className="mg-holdings" aria-label="数据概览">
          <span className="mg-holdings__head">馆藏</span>
          <div className="mg-holding">
            <span className="mg-holding__num">{stats.papers}</span>
            <span className="mg-holding__label">精读论文<small>已收录并解析</small></span>
          </div>
          <div className="mg-holding">
            <span className="mg-holding__num">{stats.workspaces}</span>
            <span className="mg-holding__label">课题工作区<small>关联 {stats.wsPapers} 篇</small></span>
          </div>
          <div className="mg-holding">
            <span className="mg-holding__num">{stats.expanded}</span>
            <span className="mg-holding__label">联网扩展文献<small>由图谱挖掘补入</small></span>
          </div>
          <div className="mg-holding" data-key>
            <span className="mg-holding__num">{stats.papers + stats.expanded}</span>
            <span className="mg-holding__label">文献总沉淀<small>可检索、可引用</small></span>
          </div>
        </aside>
      </div>

      <div className="mg-front-cols">
        {/* 最近解析论文 */}
        <section aria-labelledby="recent-papers-title">
          <div className="mg-section-head">
            <h2 id="recent-papers-title"><span className="mg-section-no">§ 1</span>最近解析</h2>
            <button type="button" onClick={() => navigate('/library')} className="mg-more">进入论文库</button>
          </div>

          {loading ? (
            <ul className="mg-ledger" aria-busy="true">
              {[0, 1, 2].map(i => (
                <li key={i} className="mg-ledger__row skeleton-pulse" style={{ height: 64, cursor: 'default' }} />
              ))}
            </ul>
          ) : recentPapers.length > 0 ? (
            <ul className="mg-ledger">
              {recentPapers.map((paper, i) => (
                <li key={paper.id}>
                  <button type="button" className="mg-ledger__row" onClick={() => navigate(`/reader/${paper.id}`)}>
                    <span className="mg-ledger__no">{String(i + 1).padStart(2, '0')}</span>
                    <span className="mg-ledger__title">{paper.title || '未命名论文'}</span>
                    <span className="mg-ledger__aside"><StatusBadge status={paper.status} /></span>
                    <span className="mg-ledger__meta">
                      {venueOf(paper)}{paper.year ? ` · ${paper.year}` : ''} · {parseServerDate(paper.created_at).toLocaleDateString('zh-CN')}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="mg-empty-note">
              <strong>暂无精读记录</strong>
              在上方投稿框上传第一篇 PDF 论文，系统会自动解析并生成精读报告。
            </div>
          )}
        </section>

        {/* 去处与最近工作区 */}
        <aside aria-label="快捷入口与课题集合">
          <div className="mg-section-head">
            <h2><span className="mg-section-no">§ 2</span>去处</h2>
          </div>
          <ul className="mg-index">
            <li>
              <button type="button" className="mg-index__link" onClick={() => navigate('/library')}>
                <div><strong>论文库检索</strong><span>按题名、作者、刊物与解读内容检索</span></div>
                <em>→</em>
              </button>
            </li>
            <li>
              <button type="button" className="mg-index__link" onClick={() => navigate('/workspace')}>
                <div><strong>研究工作区</strong><span>按课题组织论文与引文图谱</span></div>
                <em>→</em>
              </button>
            </li>
            <li>
              <button type="button" className="mg-index__link" onClick={() => navigate('/explore')}>
                <div><strong>文献研究</strong><span>从一个问题出发的领域调研</span></div>
                <em>→</em>
              </button>
            </li>
          </ul>

          {recentWorkspaces.length > 0 && (
            <>
              <div className="mg-section-head">
                <h2><span className="mg-section-no">§ 3</span>最近工作区</h2>
                <button type="button" onClick={() => navigate('/workspace')} className="mg-more">全部</button>
              </div>
              <ul className="mg-index">
                {recentWorkspaces.map(workspace => (
                  <li key={workspace.id}>
                    <button type="button" className="mg-index__link" onClick={() => navigate(`/workspace/${workspace.id}`)}>
                      <div>
                        <strong>{workspace.name}</strong>
                        <span>更新于 {parseServerDate(workspace.updated_at).toLocaleDateString('zh-CN')}</span>
                      </div>
                      <em>{workspace.paper_count} 篇</em>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </aside>
      </div>
    </div>
  )
}
