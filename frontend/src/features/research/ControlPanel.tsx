import { useState, useEffect, useMemo } from 'react'
import type { ResearchRunCreate, ResearchMode } from '@/types'
import { getWorkspace, type WorkspacePaper } from '@/api/workspaces'
import { Tooltip } from '@/components/Tooltip'

interface ControlPanelProps {
  wsId: number
  onStart: (body: ResearchRunCreate) => void
  onClear: () => void
  disabled: boolean
}

const STATUS_LABEL: Record<string, string> = {
  done:       '已解析',
  indexed:    '已索引',
  pending:    '待解析',
  failed:     '解析失败',
  extracting: '提取中',
  analyzing:  '分析中',
}

export function ControlPanel({ wsId, onStart, onClear, disabled }: ControlPanelProps) {
  const [mode, setMode] = useState<ResearchMode>('local')
  const [topK, setTopK] = useState(5)
  const [deep, setDeep] = useState(1)
  const [rebuild, setRebuild] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [threshold, setThreshold] = useState(0.78)
  const [papers, setPapers] = useState<WorkspacePaper[]>([])
  const [expandedCount, setExpandedCount] = useState(0)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [showPicker, setShowPicker] = useState(false)

  useEffect(() => {
    getWorkspace(wsId)
      .then(ws => {
        const l1 = ws.papers.filter(p => (p.level ?? 1) === 1)
        const l2plus = ws.papers.filter(p => (p.level ?? 1) >= 2)
        setPapers(l1)
        setExpandedCount(l2plus.length)
        const usable = new Set(
          l1.filter(p => p.status === 'done' || p.status === 'indexed' || p.status === 'pending').map(p => p.id)
        )
        setSelectedIds(usable)
      })
      .catch(() => {})
  }, [wsId])

  const isUsable = (status: string) =>
    status === 'done' || status === 'indexed' || status === 'pending'

  const usableCount = useMemo(() =>
    papers.filter(p => isUsable(p.status)).length,
  [papers])

  const selectedUsableCount = useMemo(() =>
    papers.filter(p => selectedIds.has(p.id) && isUsable(p.status)).length,
  [papers, selectedIds])

  const toggleSelect = (id: number) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  const selectAll = () => {
    setSelectedIds(new Set(
      papers.filter(p => isUsable(p.status)).map(p => p.id)
    ))
  }

  const selectNone = () => setSelectedIds(new Set())

  const handleStart = () => {
    onStart({
      mode,
      paper_ids: Array.from(selectedIds),
      expand_top_k: topK,
      deep,
      rebuild,
      similarity_threshold: threshold,
    })
  }

  // 开始按钮文字：区分本地/联网，并说明论文来源
  const startLabel = (() => {
    if (disabled) return '运行中...'
    if (mode === 'local') {
      return `开始研究（${selectedUsableCount} 篇）`
    }
    const expandedPart = expandedCount > 0
      ? `+ ${expandedCount} 篇已扩展`
      : ''
    return expandedPart
      ? `开始研究（${selectedUsableCount} 篇本地 ${expandedPart}）`
      : `开始研究（${selectedUsableCount} 篇本地）`
  })()

  // 选择器按钮文字
  const pickerLabel = (() => {
    const base = `起始论文 ${selectedUsableCount}/${usableCount} 已选`
    if (expandedCount > 0) return `${base}　· 另含 ${expandedCount} 篇扩展`
    return base
  })()

  return (
    <div className="px-6 py-4 border-b" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-surface)' }}>
      <div className="flex items-center gap-4 flex-wrap">

        {/* 构建范围 */}
        <div className="flex items-center gap-2">
          <span className="text-xs" style={{ color: 'var(--text-muted)' }}>构建范围</span>
          <div className="flex rounded-lg overflow-hidden border" style={{ borderColor: 'var(--border)' }}>
            {([
              {
                key: 'local' as ResearchMode,
                label: '仅本地',
                tip: '只用工作区已选论文构建知识图谱，不联网。速度快，不消耗下载配额。',
              },
              {
                key: 'online' as ResearchMode,
                label: '联网扩展',
                tip: '在本地论文基础上，通过 OpenAlex / Semantic Scholar 以及 arXiv 引用列表，下载被引用的外部论文，一起纳入图谱与检索。',
              },
            ]).map(({ key, label, tip }) => (
              <Tooltip key={key} content={tip}>
                <button
                  onClick={() => setMode(key)}
                  disabled={disabled}
                  className={`px-3 py-1 text-xs transition-colors ${mode === key ? 'font-medium' : ''}`}
                  style={
                    mode === key
                      ? { backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }
                      : { color: 'var(--text-muted)' }
                  }
                >
                  {label}
                </button>
              </Tooltip>
            ))}
          </div>
        </div>

        {/* 联网扩展参数 */}
        {mode === 'online' && (
          <>
            <Tooltip content="联网扩展时，每篇起始论文最多从引用列表中下载多少篇引用数最高的外部论文。数字越大下载越多、耗时越长。">
              <div className="flex items-center gap-1.5">
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>每篇最多下载</span>
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
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>篇</span>
              </div>
            </Tooltip>

            <Tooltip content="扩展层数：1 = 只扩展起始论文的引用（一跳）；2 = 继续扩展第一跳结果的引用（两跳）；以此类推。层数越多论文数量指数增长，谨慎调高。">
              <div className="flex items-center gap-1.5">
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>扩展层数</span>
                <input
                  type="number"
                  min={1}
                  max={3}
                  value={deep}
                  onChange={e => setDeep(Math.max(1, Math.min(3, Number(e.target.value) || 1)))}
                  disabled={disabled}
                  className="w-14 px-2 py-1 text-xs rounded border text-center"
                  style={{
                    backgroundColor: 'var(--bg-base)',
                    borderColor: 'var(--border)',
                    color: 'var(--text-primary)',
                  }}
                />
              </div>
            </Tooltip>
          </>
        )}

        {/* 起始论文选择器 */}
        <Tooltip
          content={
            <>
              展开/折叠起始论文列表。
              <br />起始论文：研究的出发点，图谱从这些论文的引用关系展开。
              {expandedCount > 0 && (
                <>
                  <br />另有 {expandedCount} 篇上次联网扩展到的论文，会自动纳入本次图谱，无需手动勾选。
                </>
              )}
            </>
          }
        >
          <button
            onClick={() => setShowPicker(v => !v)}
            disabled={disabled}
            className="px-3 py-1 text-xs rounded-md border transition-colors"
            style={{
              borderColor: 'var(--border)',
              color: 'var(--text-secondary)',
              backgroundColor: showPicker ? 'var(--accent-subtle)' : 'transparent',
            }}
          >
            {pickerLabel}
          </button>
        </Tooltip>

        {/* 重建索引 */}
        <Tooltip content="强制清空并重建该工作区的向量索引和图谱。仅在切换了向量模型（维度变化）或图谱出现异常数据时才需要。日常使用保持关闭即可（默认为增量更新）。">
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
        </Tooltip>

        <Tooltip content="调整图谱相似度阈值（控制相似边稀密程度）等高级参数。">
          <button
            onClick={() => setShowAdvanced(v => !v)}
            className="text-xs transition-colors"
            style={{ color: 'var(--text-muted)' }}
          >
            {showAdvanced ? '收起高级设置' : '高级设置'}
          </button>
        </Tooltip>

        <div className="flex-1" />

        <Tooltip content={'清空该工作区的向量索引和图谱缓存（已上传的 PDF 文件不会删除）。下次点「开始研究」会从零重建。'}>
          <button
            onClick={onClear}
            disabled={disabled}
            className="px-3 py-1.5 text-xs rounded-lg border transition-colors disabled:opacity-50"
            style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
          >
            清空图谱
          </button>
        </Tooltip>

        <Tooltip content={
          mode === 'local'
            ? '对已选论文执行：文本切片 → 向量嵌入 → 构建知识图谱。仅使用向量模型，不调用 LLM，不消耗 Token。'
            : '在本地论文基础上联网下载被引用的论文，再执行：文本切片 → 向量嵌入 → 构建知识图谱。联网部分不消耗 LLM Token，但会调用 arXiv / Semantic Scholar / OpenAlex 接口。'
        }>
          <button
            onClick={handleStart}
            disabled={disabled || selectedUsableCount === 0}
            className="px-4 py-1.5 text-xs font-medium rounded-lg transition-colors disabled:opacity-50"
            style={{
              backgroundColor: disabled ? 'var(--border)' : 'var(--accent)',
              color: 'var(--bg-base)',
            }}
          >
            {startLabel}
          </button>
        </Tooltip>
      </div>

      {/* 起始论文选择面板 */}
      {showPicker && (
        <div className="mt-3 pt-3 border-t" style={{ borderColor: 'var(--border)' }}>
          <div className="flex items-center justify-between mb-2">
            <div>
              <span className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
                起始论文（共 {papers.length} 篇 · 可用 {usableCount} 篇）
              </span>
              {expandedCount > 0 && (
                <span className="ml-2 text-xs" style={{ color: 'var(--text-muted)' }}>
                  · 另有 {expandedCount} 篇联网扩展到的论文自动纳入，无需勾选
                </span>
              )}
            </div>
            <div className="flex gap-3">
              <button onClick={selectAll} className="text-xs underline" style={{ color: 'var(--text-secondary)' }}>全选可用</button>
              <button onClick={selectNone} className="text-xs underline" style={{ color: 'var(--text-muted)' }}>清空勾选</button>
            </div>
          </div>
          <div className="max-h-48 overflow-auto rounded border" style={{ borderColor: 'var(--border)' }}>
            {papers.length === 0 ? (
              <p className="text-xs px-3 py-4 text-center" style={{ color: 'var(--text-muted)' }}>工作区暂无论文</p>
            ) : (
              papers.map(p => {
                const usable = isUsable(p.status)
                const checked = selectedIds.has(p.id)
                return (
                  <label
                    key={p.id}
                    className={`flex items-center gap-2 px-3 py-1.5 text-xs cursor-pointer transition-colors ${
                      usable ? '' : 'opacity-40 cursor-not-allowed'
                    }`}
                    style={{ color: 'var(--text-secondary)' }}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={!usable || disabled}
                      onChange={() => toggleSelect(p.id)}
                      className="accent-blue-500 shrink-0"
                    />
                    <span className="truncate flex-1">{p.title || p.filename}</span>
                    <span
                      className="shrink-0 text-[10px] px-1.5 py-0.5 rounded-full"
                      style={{
                        backgroundColor: p.status === 'failed' ? 'rgba(239,68,68,0.12)' : 'var(--bg-base)',
                        color: p.status === 'failed' ? '#ef4444' : 'var(--text-muted)',
                      }}
                    >
                      {STATUS_LABEL[p.status] ?? p.status}
                    </span>
                  </label>
                )
              })
            )}
          </div>
        </div>
      )}

      {/* 高级设置面板 */}
      {showAdvanced && (
        <div className="mt-3 pt-3 border-t flex items-center gap-4" style={{ borderColor: 'var(--border)' }}>
          <Tooltip content={'两篇论文向量余弦相似度超过此值时，图谱里会连一条「相似」边。值越大边越少越精炼；值越小边越多越密。默认 0.78。'}>
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
          </Tooltip>
        </div>
      )}
    </div>
  )
}
