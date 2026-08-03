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
import '@/styles/home.css'

interface StatTileProps {
  index: string
  label: string
  value: number
  hint: string
}

function StatTile({ index, label, value, hint }: StatTileProps) {
  return (
    <div className="origin-stat">
      <span className="origin-stat__index" aria-hidden="true">{index}</span>
      <strong className="origin-stat__value">{String(value).padStart(2, '0')}</strong>
      <span className="origin-stat__label">{label}</span>
      <small>{hint}</small>
    </div>
  )
}

interface QuickEntryProps {
  index: string
  title: string
  description: string
  onClick: () => void
}

function QuickEntry({ index, title, description, onClick }: QuickEntryProps) {
  return (
    <button className="origin-quick-entry" type="button" onClick={onClick}>
      <span className="origin-quick-entry__index" aria-hidden="true">{index}</span>
      <span className="origin-quick-entry__copy">
        <strong>{title}</strong>
        <small>{description}</small>
      </span>
      <span className="origin-quick-entry__arrow" aria-hidden="true">&#8599;</span>
    </button>
  )
}

const STATUS_LABELS: Record<string, string> = {
  done: '已完成',
  failed: '失败',
  pending: '尚未解析',
}

function statusLabel(status: string) {
  return STATUS_LABELS[status] ?? '处理中'
}

function LoadingLedger() {
  return (
    <div className="origin-ledger-loading" role="status" aria-live="polite">
      <span className="origin-visually-hidden">正在载入最近论文</span>
      {Array.from({ length: 4 }).map((_, index) => (
        <div className="origin-ledger-loading__row" key={index} aria-hidden="true">
          <i />
          <span><i /><i /></span>
          <i />
        </div>
      ))}
    </div>
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
    <div className="origin-home">
      <section className="origin-home__hero" aria-labelledby="origin-home-title">
        <header className="origin-home__registration">
          <span>ER / ORIGIN</span>
          <span>RESEARCH INTAKE—00</span>
          <span>31.2304° N / 121.4737° E</span>
        </header>

        <div className="origin-home__copy">
          <p className="origin-home__scene-index">SCENE—00 / 论文输入协议</p>
          <h1 id="origin-home-title">
            <span>读懂</span>
            <span>一篇论文</span>
          </h1>
          <p className="origin-home__descriptor">
            上传 PDF，自动提取方法、实验与代码映射。所有证据从同一份文档出发，进入可追踪的研究阅读流程。
          </p>
        </div>

        <div className="origin-home__axis" aria-hidden="true">
          <span />
        </div>

        <div className="origin-home__mass" aria-hidden="true">
          <div className="origin-home__paper-object">
            <span>PDF / 01</span>
            <i />
            <i />
            <i />
            <b />
            <small>METHOD / EVIDENCE / CODE</small>
          </div>
        </div>

        <div className="origin-home__intake">
          <UploadZone
            onUpload={handleUpload}
            isUploading={isUploading}
            uploadProgress={uploadProgress}
          />
          {uploadError && (
            <p className="origin-home__upload-error" role="alert">{uploadError}</p>
          )}
        </div>

        <p className="origin-home__note" aria-hidden="true">
          FORM FOLLOWS RELATION / SOURCE—PLANE
        </p>
      </section>

      <section className="origin-home__stats" aria-label="研究资料统计">
        <StatTile index="01" label="论文库" value={stats.papers} hint="已上传精读" />
        <StatTile index="02" label="工作区" value={stats.workspaces} hint={`${stats.wsPapers} 篇藏书`} />
        <StatTile index="03" label="扩展发现" value={stats.expanded} hint="深度研究联网" />
        <StatTile
          index="04"
          label="近期条目"
          value={recentPapers.length + recentWorkspaces.length}
          hint="最近解析 + 工作区"
        />
      </section>

      <div className="origin-home__content-grid">
        <section className="origin-home__papers" aria-labelledby="recent-papers-title">
          <header className="origin-section-heading">
            <div>
              <span>ARCHIVE / RECENT</span>
              <h2 id="recent-papers-title">最近解析</h2>
            </div>
            <button type="button" onClick={() => navigate('/library')}>查看全部 &#8599;</button>
          </header>

          {loading ? (
            <LoadingLedger />
          ) : recentPapers.length > 0 ? (
            <ol className="origin-paper-ledger">
              {recentPapers.map((paper, index) => (
                <li key={paper.id}>
                  <button
                    className="origin-paper-row"
                    type="button"
                    data-status={paper.status}
                    onClick={() => navigate(`/reader/${paper.id}`)}
                  >
                    <span className="origin-paper-row__index" aria-hidden="true">
                      {String(index + 1).padStart(2, '0')}
                    </span>
                    <span className="origin-paper-row__body">
                      <strong>{paper.title || paper.filename}</strong>
                      <small>
                        {paper.venue || '本地论文'}
                        {paper.year ? ` / ${paper.year}` : ''}
                      </small>
                    </span>
                    <span className="origin-paper-row__status">
                      <i aria-hidden="true" />
                      {statusLabel(paper.status)}
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          ) : (
            <div className="origin-empty-state">
              <span aria-hidden="true">00</span>
              <div>
                <strong>还没有解析记录</strong>
                <p>把第一篇 PDF 放入上方输入区，建立你的论文档案。</p>
              </div>
            </div>
          )}
        </section>

        <aside className="origin-home__side" aria-label="快捷入口与最近工作区">
          <section className="origin-quick-panel" aria-labelledby="quick-entry-title">
            <header>
              <span>DIRECT / ROUTES</span>
              <h2 id="quick-entry-title">快捷入口</h2>
            </header>
            <QuickEntry
              index="01"
              title="论文库"
              description="浏览与智能检索全部论文"
              onClick={() => navigate('/library')}
            />
            <QuickEntry
              index="02"
              title="工作区"
              description="按主题组织论文集合"
              onClick={() => navigate('/workspace')}
            />
            <QuickEntry
              index="03"
              title="深度研究"
              description="构建引文图谱与 RAG 问答"
              onClick={() => navigate('/workspace')}
            />
          </section>

          {recentWorkspaces.length > 0 && (
            <section className="origin-workspaces" aria-labelledby="recent-workspaces-title">
              <header className="origin-section-heading">
                <div>
                  <span>FIELD / RECENT</span>
                  <h2 id="recent-workspaces-title">最近工作区</h2>
                </div>
                <button type="button" onClick={() => navigate('/workspace')}>全部 &#8599;</button>
              </header>
              <ol>
                {recentWorkspaces.slice(0, 4).map((workspace, index) => (
                  <li key={workspace.id}>
                    <button
                      className="origin-workspace-row"
                      type="button"
                      onClick={() => navigate(`/workspace/${workspace.id}`)}
                    >
                      <span aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
                      <span>
                        <strong>{workspace.name}</strong>
                        <small>
                          {workspace.paper_count} 篇 / {new Date(workspace.updated_at).toLocaleDateString('zh-CN')}
                        </small>
                      </span>
                      <i aria-hidden="true">&#8594;</i>
                    </button>
                  </li>
                ))}
              </ol>
            </section>
          )}
        </aside>
      </div>

      <footer className="origin-home__footer" aria-hidden="true">
        <span>ER—00 / CURRENT INDEX</span>
        <span>INPUT → ANALYSIS → RELATION</span>
      </footer>
    </div>
  )
}
