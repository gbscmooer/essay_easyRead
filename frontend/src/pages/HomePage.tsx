/**
 * HomePage — 首页
 *
 * 学术阅读室仪表盘:粒子 Hero + 统计带 + 上传 + 最近解析网格 + 快捷入口磁贴。
 * 暖纸主题 + 衬线大数字,信息密度高、构图有节奏。
 */

import { useEffect, useState, useRef, useCallback, type CSSProperties, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Paper } from '@/types'
import { UploadZone } from '@/features/upload/UploadZone'
import { listPapers, uploadPaperWithProgress } from '@/api/papers'
import { listWorkspaces, type WorkspaceItem } from '@/api/workspaces'
import { toast } from '@/hooks/useToast'
import { SkeletonCard } from '@/ds-entry'


/* ── 粒子背景 ─────────────────────────────────────── */

interface Particle {
  x: number
  y: number
  vx: number
  vy: number
  r: number
}

function ParticleCanvas() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const particlesRef = useRef<Particle[]>([])
  const rafRef = useRef<number>(0)

  const init = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const count = Math.min(40, Math.floor((canvas.width * canvas.height) / 25000))
    particlesRef.current = Array.from({ length: count }, () => ({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      vx: (Math.random() - 0.5) * 0.3,
      vy: (Math.random() - 0.5) * 0.3,
      r: Math.random() * 1.5 + 0.5,
    }))
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const resize = () => {
      const parent = canvas.parentElement
      if (!parent) return
      canvas.width = parent.clientWidth
      canvas.height = parent.clientHeight
      init()
    }
    resize()
    window.addEventListener('resize', resize)

    const draw = () => {
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      const w = canvas.width
      const h = canvas.height
      ctx.clearRect(0, 0, w, h)

      const particles = particlesRef.current
      const accent = getComputedStyle(document.documentElement)
        .getPropertyValue('--accent').trim() || '#60a5fa'

      for (const p of particles) {
        p.x += p.vx
        p.y += p.vy
        if (p.x < 0 || p.x > w) p.vx *= -1
        if (p.y < 0 || p.y > h) p.vy *= -1
      }

      // Lines
      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const dx = particles[i].x - particles[j].x
          const dy = particles[i].y - particles[j].y
          const dist = Math.sqrt(dx * dx + dy * dy)
          if (dist < 140) {
            ctx.beginPath()
            ctx.moveTo(particles[i].x, particles[i].y)
            ctx.lineTo(particles[j].x, particles[j].y)
            ctx.strokeStyle = accent
            ctx.globalAlpha = (1 - dist / 140) * 0.12
            ctx.lineWidth = 0.5
            ctx.stroke()
          }
        }
      }

      // Dots
      ctx.globalAlpha = 0.25
      ctx.fillStyle = accent
      for (const p of particles) {
        ctx.beginPath()
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2)
        ctx.fill()
      }

      ctx.globalAlpha = 1
      rafRef.current = requestAnimationFrame(draw)
    }

    rafRef.current = requestAnimationFrame(draw)

    return () => {
      cancelAnimationFrame(rafRef.current)
      window.removeEventListener('resize', resize)
    }
  }, [init])

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 pointer-events-none"
      style={{ zIndex: 0 }}
    />
  )
}


/* ── 打字机 ────────────────────────────────────────── */

function TypewriterText({
  lines,
  speed = 45,
  pauseBetween = 600,
}: {
  lines: string[]
  speed?: number
  pauseBetween?: number
}) {
  const [displayText, setDisplayText] = useState('')
  const [lineIdx, setLineIdx] = useState(0)
  const [charIdx, setCharIdx] = useState(0)
  const [phase, setPhase] = useState<'typing' | 'pausing' | 'done'>('typing')

  useEffect(() => {
    if (phase === 'done') return

    if (phase === 'typing') {
      if (charIdx < lines[lineIdx].length) {
        const t = setTimeout(() => {
          setDisplayText(prev => prev + lines[lineIdx][charIdx])
          setCharIdx(c => c + 1)
        }, speed)
        return () => clearTimeout(t)
      } else {
        // Line finished
        if (lineIdx < lines.length - 1) {
          setPhase('pausing')
        } else {
          setPhase('done')
        }
      }
    }

    if (phase === 'pausing') {
      const t = setTimeout(() => {
        setDisplayText(prev => prev + '\n')
        setLineIdx(l => l + 1)
        setCharIdx(0)
        setPhase('typing')
      }, pauseBetween)
      return () => clearTimeout(t)
    }
  }, [phase, charIdx, lineIdx, lines, speed, pauseBetween])

  return (
    <span>
      {displayText}
      {phase !== 'done' && (
        <span
          className="inline-block w-[2px] h-[1em] align-middle ml-0.5"
          style={{
            backgroundColor: 'var(--accent)',
            animation: 'skeleton-breathe 1s step-end infinite',
          }}
        />
      )}
    </span>
  )
}


/* ── 统计磁贴 ───────────────────────────────────────── */

function StatTile({ label, value, hint, i }: { label: string; value: number; hint?: string; i: number }) {
  return (
    <div
      className="relative p-4 spotlight card-hover overflow-hidden"
      style={{
        backgroundColor: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--seed-radius, 12px)',
        '--i': i,
      } as CSSProperties}
    >
      <div className="text-display text-[30px] leading-none tabular-nums" style={{ color: 'var(--text-primary)' }}>
        {value}
      </div>
      <div className="mt-2 text-[12px] font-medium" style={{ color: 'var(--text-secondary)' }}>{label}</div>
      {hint && <div className="text-[11px] mt-0.5" style={{ color: 'var(--text-muted)' }}>{hint}</div>}
    </div>
  )
}


/* ── 快捷入口磁贴 ───────────────────────────────────── */

function QuickTile({ icon, title, desc, onClick, i }: { icon: ReactNode; title: string; desc: string; onClick: () => void; i: number }) {
  return (
    <button
      onClick={onClick}
      className="group w-full flex items-center gap-3.5 p-4 text-left spotlight card-hover press focus-glow"
      style={{
        backgroundColor: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--seed-radius, 12px)',
        '--i': i,
      } as CSSProperties}
    >
      <span className="shrink-0 flex items-center justify-center w-10 h-10 rounded-xl" style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }}>
        {icon}
      </span>
      <span className="flex-1 min-w-0">
        <span className="block text-[13px] font-medium" style={{ color: 'var(--text-primary)' }}>{title}</span>
        <span className="block text-[12px] mt-0.5" style={{ color: 'var(--text-muted)' }}>{desc}</span>
      </span>
      <span className="shrink-0 text-[15px] transition-transform duration-200 group-hover:translate-x-1" style={{ color: 'var(--text-muted)' }}>&rarr;</span>
    </button>
  )
}

const STATUS_META: Record<string, { label: string; bg: string; color: string }> = {
  done:    { label: '已完成', bg: 'var(--success-subtle)', color: 'var(--success)' },
  failed:  { label: '失败',   bg: 'var(--error-subtle)',   color: 'var(--error)' },
  pending: { label: '尚未解析', bg: 'var(--bg-elevated)',   color: 'var(--text-muted)' },
}
function statusMeta(status: string) {
  return STATUS_META[status] ?? { label: '处理中', bg: 'var(--warning-subtle)', color: 'var(--warning)' }
}


/* ── 主页面 ────────────────────────────────────────── */

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
        .then(res => { setRecentPapers(res.papers); setStats(s => ({ ...s, papers: res.total })) })
        .catch(() => { toast({ type: 'error', message: '加载最近论文失败' }) }),
      listWorkspaces()
        .then(ws => {
          const sorted = ws.sort((a, b) =>
            new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
          )
          setRecentWorkspaces(sorted.slice(0, 6))
          setStats(s => ({ ...s, workspaces: ws.length, wsPapers: ws.reduce((n, w) => n + (w.paper_count || 0), 0) }))
        })
        .catch(() => { toast({ type: 'error', message: '加载工作区失败' }) }),
      listPapers({ per_page: 1, source: 'expanded' })
        .then(res => setStats(s => ({ ...s, expanded: res.total })))
        .catch(() => {}),
    ]).finally(() => { if (!controller.signal.aborted) setLoading(false) })

    return () => { controller.abort() }
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
    <div className="max-w-6xl mx-auto px-8 py-10">
      {/* Hero — 粒子背景 + 不对称布局 + 打字机 */}
      <div className="relative mb-6 overflow-hidden" style={{ borderRadius: 'var(--seed-radius, 12px)' }}>
        <div className="absolute inset-0">
          <ParticleCanvas />
        </div>

        <div className="relative z-10 grid grid-cols-[1fr_auto] gap-10 items-center py-12 px-10">
          {/* 左侧：标题 + 打字机描述 */}
          <div>
            <p className="text-label mb-3" style={{ color: 'var(--accent)' }}>论文精读助手</p>
            <h1 className="text-display text-gradient text-[48px] mb-4">
              Easy Essay
            </h1>
            <p
              className="text-[15px] leading-relaxed max-w-md whitespace-pre-line"
              style={{ color: 'var(--text-muted)', minHeight: '3.2em' }}
            >
              <TypewriterText
                lines={[
                  '上传 PDF，自动提取方法论、实验结果和代码映射。',
                  '为学术阅读而生的智能论文分析工具。',
                ]}
                speed={40}
                pauseBetween={500}
              />
            </p>
          </div>

          {/* 右侧：Hero 图像 */}
          <div className="w-40 h-40 relative group select-none shrink-0">
            <div
              className="absolute inset-0 rounded-[2rem] blur-2xl opacity-15 group-hover:opacity-25 transition-opacity duration-500"
              style={{ background: 'linear-gradient(135deg, var(--accent), var(--accent-subtle))' }}
            />
            <img
              src="/hero.jpg"
              alt="Easy Essay"
              className="w-full h-full object-cover rounded-[2rem] relative z-10 transition-transform duration-500 hover:scale-[1.03]"
              style={{
                border: '1px solid var(--border)',
                boxShadow: '0 8px 32px rgba(0,0,0,0.08)',
              }}
            />
          </div>
        </div>
      </div>

      {/* 统计带 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6 stagger">
        <StatTile i={0} label="论文库" value={stats.papers} hint="已上传精读" />
        <StatTile i={1} label="工作区" value={stats.workspaces} hint={`${stats.wsPapers} 篇藏书`} />
        <StatTile i={2} label="扩展发现" value={stats.expanded} hint="深度研究联网" />
        <StatTile i={3} label="近期条目" value={recentPapers.length + recentWorkspaces.length} hint="最近解析 + 工作区" />
      </div>

      {/* 上传区 */}
      <div className="mb-8">
        <UploadZone onUpload={handleUpload} isUploading={isUploading} uploadProgress={uploadProgress} />
        {uploadError && (
          <p className="mt-4 text-[13px] text-center" style={{ color: 'var(--error)' }}>
            {uploadError}
          </p>
        )}
      </div>

      {/* 主区:左=最近解析,右=快捷入口 + 最近工作区 */}
      <div className="grid lg:grid-cols-[1.5fr_1fr] gap-5 items-start">
        {/* 左列:最近解析 */}
        <section>
          <div className="flex items-center justify-between mb-4">
            <p className="text-label" style={{ color: 'var(--text-muted)' }}>最近解析</p>
            <button onClick={() => navigate('/library')} className="focus-glow rounded px-1 text-[12px] transition-colors hover:opacity-70" style={{ color: 'var(--text-muted)' }}>
              查看全部 &rarr;
            </button>
          </div>

          {loading ? (
            <div className="grid sm:grid-cols-2 gap-3">
              {Array.from({ length: 4 }).map((_, i) => <SkeletonCard key={i} />)}
            </div>
          ) : recentPapers.length > 0 ? (
            <div className="grid sm:grid-cols-2 gap-3 stagger">
              {recentPapers.map((paper, i) => {
                const m = statusMeta(paper.status)
                return (
                  <div
                    key={paper.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => navigate(`/reader/${paper.id}`)}
                    onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate(`/reader/${paper.id}`) } }}
                    className="p-5 cursor-pointer card-hover press focus-glow spotlight transition-all"
                    style={{
                      backgroundColor: 'var(--bg-surface)',
                      border: '1px solid var(--border)',
                      borderRadius: 'var(--seed-radius, 12px)',
                      '--i': i,
                    } as CSSProperties}
                  >
                    <h3 className="text-[13px] font-medium mb-2 line-clamp-2 leading-snug" style={{ color: 'var(--text-primary)' }}>
                      {paper.title || paper.filename}
                    </h3>
                    <div className="flex items-center gap-2 text-[12px]" style={{ color: 'var(--text-muted)' }}>
                      {paper.venue && <span className="truncate max-w-[55%]">{paper.venue}</span>}
                      {paper.venue && paper.year && <span style={{ opacity: 0.4 }}>|</span>}
                      {paper.year && <span>{paper.year}</span>}
                    </div>
                    <div className="mt-3 flex items-center gap-2">
                      <span className="inline-flex items-center gap-1.5 text-[11px] px-2 py-0.5 rounded-full" style={{ backgroundColor: m.bg, color: m.color }}>
                        <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: m.color }} />
                        {m.label}
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            <div
              className="flex flex-col items-center justify-center text-center py-14 px-6"
              style={{ border: '1px dashed var(--border-light)', borderRadius: 'var(--seed-radius, 12px)' }}
            >
              <div className="w-12 h-12 rounded-2xl flex items-center justify-center mb-3" style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }}>
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" />
                </svg>
              </div>
              <p className="text-[13px]" style={{ color: 'var(--text-secondary)' }}>还没有解析记录</p>
              <p className="text-[12px] mt-1" style={{ color: 'var(--text-muted)' }}>把第一篇 PDF 拖到上方,开始精读</p>
            </div>
          )}
        </section>

        {/* 右列:快捷入口 + 最近工作区 */}
        <div className="space-y-6">
          <section>
            <p className="text-label mb-4" style={{ color: 'var(--text-muted)' }}>快捷入口</p>
            <div className="space-y-2.5 stagger">
              <QuickTile
                i={0}
                title="论文库"
                desc="浏览与智能检索全部论文"
                onClick={() => navigate('/library')}
                icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /></svg>}
              />
              <QuickTile
                i={1}
                title="工作区"
                desc="按主题把论文组织在一起"
                onClick={() => navigate('/workspace')}
                icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="2" y="7" width="20" height="14" rx="2" /><path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2" /></svg>}
              />
              <QuickTile
                i={2}
                title="深度研究"
                desc="构建引文图谱 + RAG 问答"
                onClick={() => navigate('/workspace')}
                icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="6" r="2.5" /><circle cx="18" cy="18" r="2.5" /><path d="M8.5 6h7M7.8 7.8l8.4 8.4" /></svg>}
              />
            </div>
          </section>

          {recentWorkspaces.length > 0 && (
            <section>
              <div className="flex items-center justify-between mb-4">
                <p className="text-label" style={{ color: 'var(--text-muted)' }}>最近工作区</p>
                <button onClick={() => navigate('/workspace')} className="focus-glow rounded px-1 text-[12px] transition-colors hover:opacity-70" style={{ color: 'var(--text-muted)' }}>
                  查看全部 &rarr;
                </button>
              </div>
              <div className="space-y-2 stagger">
                {recentWorkspaces.slice(0, 4).map((ws, i) => (
                  <button
                    key={ws.id}
                    onClick={() => navigate(`/workspace/${ws.id}`)}
                    className="group w-full flex items-center gap-3 p-3.5 text-left spotlight card-hover press focus-glow"
                    style={{
                      backgroundColor: 'var(--bg-surface)',
                      border: '1px solid var(--border)',
                      borderRadius: 'var(--seed-radius, 12px)',
                      '--i': i,
                    } as CSSProperties}
                  >
                    <span className="shrink-0 flex items-center justify-center w-9 h-9 rounded-xl text-[13px] font-semibold" style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }}>
                      {ws.name.slice(0, 1).toUpperCase()}
                    </span>
                    <span className="flex-1 min-w-0">
                      <span className="block text-[13px] font-medium truncate" style={{ color: 'var(--text-primary)' }}>{ws.name}</span>
                      <span className="block text-[11px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
                        {ws.paper_count} 篇 · {new Date(ws.updated_at).toLocaleDateString('zh-CN')}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  )
}
