import { useState, useEffect } from 'react'
import type { ResearchRunCreate, ResearchMode } from '@/types'
import { getWorkspace } from '@/api/workspaces'

interface ControlPanelProps {
  wsId: number
  onStart: (body: ResearchRunCreate) => void
  onClear: () => void
  disabled: boolean
}

export function ControlPanel({ wsId, onStart, onClear, disabled }: ControlPanelProps) {
  const [mode, setMode] = useState<ResearchMode>('local')
  const [topK, setTopK] = useState(5)
  const [rebuild, setRebuild] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [threshold, setThreshold] = useState(0.78)
  const [paperCount, setPaperCount] = useState(0)

  useEffect(() => {
    getWorkspace(wsId).then(ws => setPaperCount(ws.papers.filter(p => p.status === 'done').length)).catch(() => {})
  }, [wsId])

  const handleStart = () => {
    onStart({
      mode,
      expand_top_k: topK,
      rebuild,
      similarity_threshold: threshold,
    })
  }

  return (
    <div className="px-6 py-4 border-b" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-surface)' }}>
      <div className="flex items-center gap-4 flex-wrap">
        {/* 模式选择 */}
        <div className="flex items-center gap-2">
          <span className="text-xs" style={{ color: 'var(--text-muted)' }}>模式</span>
          <div className="flex rounded-lg overflow-hidden border" style={{ borderColor: 'var(--border)' }}>
            {(['local', 'online'] as ResearchMode[]).map(m => (
              <button
                key={m}
                onClick={() => setMode(m)}
                disabled={disabled}
                className={`px-3 py-1 text-xs transition-colors ${
                  mode === m ? 'font-medium' : ''
                }`}
                style={
                  mode === m
                    ? { backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }
                    : { color: 'var(--text-muted)' }
                }
              >
                {m === 'local' ? '仅本地' : '联网扩展'}
              </button>
            ))}
          </div>
        </div>

        {/* Online 模式的 Top-K */}
        {mode === 'online' && (
          <div className="flex items-center gap-2">
            <span className="text-xs" style={{ color: 'var(--text-muted)' }}>每篇 Top-K</span>
            <input
              type="number"
              min={1}
              max={20}
              value={topK}
              onChange={e => setTopK(Number(e.target.value))}
              disabled={disabled}
              className="w-14 px-2 py-1 text-xs rounded border text-center"
              style={{
                backgroundColor: 'var(--bg-base)',
                borderColor: 'var(--border)',
                color: 'var(--text-primary)',
              }}
            />
          </div>
        )}

        {/* Rebuild */}
        <label className="flex items-center gap-1.5 text-xs cursor-pointer" style={{ color: 'var(--text-muted)' }}>
          <input
            type="checkbox"
            checked={rebuild}
            onChange={e => setRebuild(e.target.checked)}
            disabled={disabled}
            className="rounded accent-blue-500"
          />
          重建索引
        </label>

        {/* 高级设置 */}
        <button
          onClick={() => setShowAdvanced(v => !v)}
          className="text-xs transition-colors"
          style={{ color: 'var(--text-muted)' }}
        >
          {showAdvanced ? '收起' : '高级设置'}
        </button>

        <div className="flex-1" />

        {/* 操作按钮 */}
        <button
          onClick={onClear}
          disabled={disabled}
          className="px-3 py-1.5 text-xs rounded-lg border transition-colors disabled:opacity-50"
          style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
        >
          清空数据
        </button>
        <button
          onClick={handleStart}
          disabled={disabled || paperCount === 0}
          className="px-4 py-1.5 text-xs font-medium rounded-lg transition-colors disabled:opacity-50"
          style={{
            backgroundColor: disabled ? 'var(--border)' : 'var(--accent)',
            color: 'var(--bg-base)',
          }}
        >
          {disabled ? '运行中...' : `开始研究（${paperCount} 篇）`}
        </button>
      </div>

      {/* 高级设置面板 */}
      {showAdvanced && (
        <div className="mt-3 pt-3 border-t flex items-center gap-4" style={{ borderColor: 'var(--border)' }}>
          <div className="flex items-center gap-2">
            <span className="text-xs" style={{ color: 'var(--text-muted)' }}>相似度阈值</span>
            <input
              type="range"
              min={0.5}
              max={0.95}
              step={0.01}
              value={threshold}
              onChange={e => setThreshold(Number(e.target.value))}
              className="w-24 h-1 accent-blue-500"
            />
            <span className="text-xs font-mono" style={{ color: 'var(--text-secondary)' }}>{threshold}</span>
          </div>
        </div>
      )}
    </div>
  )
}
