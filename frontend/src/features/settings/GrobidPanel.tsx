/**
 * GROBID 引用解析后端面板
 *
 * - 解析后端选择：GROBID（结构化，默认）/ 正则（零依赖回退）
 * - GROBID 服务地址配置
 * - 查询服务情况（服务可达 + 镜像是否已下载）
 * - 一键下载镜像（docker pull grobid/grobid:0.9.0-crf）
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { getGrobidStatus, pullGrobidImage, type GrobidStatus } from '@/api/settings'

interface GrobidPanelProps {
  parser: 'grobid' | 'regex'
  grobidUrl: string
  onChangeParser: (p: 'grobid' | 'regex') => void
  onChangeUrl: (u: string) => void
}

function Dot({ color }: { color: string }) {
  return <span className="inline-block w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
}

export function GrobidPanel({ parser, grobidUrl, onChangeParser, onChangeUrl }: GrobidPanelProps) {
  const [status, setStatus] = useState<GrobidStatus | null>(null)
  const [checking, setChecking] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const refresh = useCallback(async () => {
    setChecking(true)
    try {
      setStatus(await getGrobidStatus())
    } catch {
      setStatus(null)
    } finally {
      setChecking(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [refresh])

  // 拉取中时轮询状态
  useEffect(() => {
    if (status?.pulling && !pollRef.current) {
      pollRef.current = setInterval(refresh, 2000)
    } else if (!status?.pulling && pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [status?.pulling, refresh])

  const handlePull = async () => {
    try {
      await pullGrobidImage()
      refresh()
    } catch {
      // ignore
    }
  }

  return (
    <div className="space-y-3 mt-3 pt-3" style={{ borderTop: '1px solid var(--border)' }}>
      {/* 解析后端 */}
      <div>
        <label className="block text-xs mb-1.5" style={{ color: 'var(--text-muted)' }}>引用解析后端</label>
        <div className="flex gap-2">
          {([
            { key: 'grobid' as const, label: 'GROBID（结构化，推荐）' },
            { key: 'regex' as const, label: '正则（零依赖回退）' },
          ]).map(o => (
            <button
              key={o.key}
              onClick={() => onChangeParser(o.key)}
              className="flex-1 px-3 py-1.5 text-sm rounded-lg border transition-colors"
              style={{
                borderColor: parser === o.key ? 'var(--accent)' : 'var(--border)',
                backgroundColor: parser === o.key ? 'var(--accent-subtle)' : 'transparent',
                color: parser === o.key ? 'var(--accent)' : 'var(--text-secondary)',
              }}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>

      {parser === 'grobid' && (
        <>
          {/* 服务地址 */}
          <div>
            <label className="block text-xs mb-1.5" style={{ color: 'var(--text-muted)' }}>GROBID 服务地址</label>
            <input
              value={grobidUrl}
              onChange={e => onChangeUrl(e.target.value)}
              placeholder="http://localhost:8070"
              className="w-full px-3 py-1.5 text-sm rounded border focus:outline-none"
              style={{ backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border)', color: 'var(--text-primary)' }}
            />
          </div>

          {/* 服务情况 */}
          <div className="rounded-lg border p-3 space-y-2" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-elevated)' }}>
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>服务情况</span>
              <button
                onClick={refresh}
                disabled={checking}
                className="text-xs px-2 py-0.5 rounded transition-colors disabled:opacity-50"
                style={{ color: 'var(--accent)', backgroundColor: 'var(--accent-subtle)' }}
              >
                {checking ? '检查中…' : '刷新'}
              </button>
            </div>

            <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-secondary)' }}>
              <Dot color={status?.alive ? '#22c55e' : '#ef4444'} />
              服务{status?.alive ? '已就绪（可达）' : '未运行 / 不可达'}
            </div>
            <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-secondary)' }}>
              <Dot color={status?.image_present ? '#22c55e' : 'var(--text-muted)'} />
              镜像{status?.image_present ? '已下载' : '未下载'}
              <span style={{ color: 'var(--text-muted)' }}>（{status?.image || 'grobid/grobid:0.9.0-crf'}）</span>
            </div>

            {/* 下载 */}
            <div className="flex items-center gap-2 pt-1">
              <button
                onClick={handlePull}
                disabled={status?.pulling}
                className="text-xs px-3 py-1.5 rounded transition-colors disabled:opacity-60"
                style={{ color: 'var(--bg-base)', backgroundColor: 'var(--accent)' }}
              >
                {status?.pulling ? '下载中…' : status?.image_present ? '重新下载镜像' : '下载镜像 (docker pull)'}
              </button>
              {status?.pull_ok === true && !status?.pulling && (
                <span className="text-xs" style={{ color: '#22c55e' }}>下载完成</span>
              )}
              {status?.pull_ok === false && !status?.pulling && (
                <span className="text-xs" style={{ color: 'var(--error)' }}>下载失败，见日志</span>
              )}
            </div>

            {/* 拉取日志 */}
            {(status?.pulling || status?.pull_log) && (
              <pre
                className="text-[10px] font-mono rounded p-2 overflow-auto whitespace-pre-wrap"
                style={{ maxHeight: '8rem', backgroundColor: 'var(--bg-base)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}
              >
                {status?.pull_log || '准备下载…'}
              </pre>
            )}

            <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
              下载完成后需启动容器：<code>docker run --rm -d -p 8070:8070 grobid/grobid:0.9.0-crf</code>
              （start.sh 已含一键启动）。未运行时引用解析会自动回退到正则。
            </p>
          </div>
        </>
      )}
    </div>
  )
}
