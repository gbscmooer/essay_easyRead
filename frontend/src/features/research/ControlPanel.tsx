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

export function ControlPanel({ wsId, onStart, onClear, disabled }: ControlPanelProps) {
  const [mode, setMode] = useState<ResearchMode>('local')
  const [topK, setTopK] = useState(5)
  const [deep, setDeep] = useState(1)
  const [rebuild, setRebuild] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [threshold, setThreshold] = useState(0.78)
  const [papers, setPapers] = useState<WorkspacePaper[]>([])
  const [discoveredCount, setDiscoveredCount] = useState(0)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [showPicker, setShowPicker] = useState(false)

  useEffect(() => {
    getWorkspace(wsId)
      .then(ws => {
        // 只考虑 level=1（用户手动加的或已"移入工作区"的），L2+ 是研究产物，不参与"选 seed"
        const l1 = ws.papers.filter(p => (p.level ?? 1) === 1)
        const l2plus = ws.papers.filter(p => (p.level ?? 1) >= 2)
        setPapers(l1)
        setDiscoveredCount(l2plus.length)
        // 默认勾选所有"可用"的：done / indexed / pending（深度研究只需要 bge 不需要 LLM）
        // 排除 failed 和正在解析中的 (extracting/analyzing)
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

  return (
    <div className="px-6 py-4 border-b" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-surface)' }}>
      <div className="flex items-center gap-4 flex-wrap">
        {/* 模式选择 */}
        <div className="flex items-center gap-2">
          <span className="text-xs" style={{ color: 'var(--text-muted)' }}>模式</span>
          <div className="flex rounded-lg overflow-hidden border" style={{ borderColor: 'var(--border)' }}>
            {(['local', 'online'] as ResearchMode[]).map(m => (
              <Tooltip
                key={m}
                content={
                  m === 'local'
                    ? '只用工作区里已勾选的论文构建知识图谱，不联网。'
                    : '在"仅本地"基础上，再通过深度研究 Provider（OpenAlex / Semantic Scholar）下载本地论文引用到的其他论文，纳入图谱与 RAG 检索。'
                }
              >
                <button
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
              </Tooltip>
            ))}
          </div>
        </div>

        {/* Online 模式：Top-K + Deep */}
        {mode === 'online' && (
          <>
            <Tooltip content="每篇 seed 论文最多下载多少条 references。例如 5 表示每篇本地论文最多扩展 5 篇引用。">
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
            </Tooltip>
            <Tooltip content="联网扩展的跳数。deep=1：只展开本地论文的引用（L1→L2）。deep=2：再继续展开 L2 的引用得到 L3，依此类推。论文数量会指数级增加。">
              <div className="flex items-center gap-2">
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>深度 deep</span>
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

        {/* 论文选择按钮 */}
        <Tooltip
          content={
            <>
              展开/折叠 seed 选择列表。当前勾选 {selectedUsableCount} 篇 L1 论文作为研究起点
              （工作区共 {usableCount} 篇 L1 可用）。
              {discoveredCount > 0 && (
                <>
                  <br />另有 {discoveredCount} 篇"深度研究发现"自动纳入图谱，不需要勾选。
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
            seed {selectedUsableCount}/{usableCount}
            {discoveredCount > 0 && <span className="ml-1 opacity-60">· +{discoveredCount} 发现</span>}
          </button>
        </Tooltip>

        {/* Rebuild */}
        <Tooltip content="勾选后会清空该工作区的向量索引和图谱，再重新从零构建。仅当切换了 embedding 模型 / 维度，或图谱出现脏数据时才需要。日常增量研究保持不勾即可（增量 upsert）。">
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

        <Tooltip content="高级参数：相似度阈值（控制 similar 边的稀疏程度）。">
          <button
            onClick={() => setShowAdvanced(v => !v)}
            className="text-xs transition-colors"
            style={{ color: 'var(--text-muted)' }}
          >
            {showAdvanced ? '收起' : '高级设置'}
          </button>
        </Tooltip>

        <div className="flex-1" />

        <Tooltip content="清空该工作区的图谱和向量索引（不删除已上传的论文文件）。下次开始研究会从零重建。">
          <button
            onClick={onClear}
            disabled={disabled}
            className="px-3 py-1.5 text-xs rounded-lg border transition-colors disabled:opacity-50"
            style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
          >
            清空数据
          </button>
        </Tooltip>
        <Tooltip content="对勾选的论文走 chunk → bge 向量嵌入 → 构建知识图谱。仅使用 embedding 模型，不调用 LLM，不消耗 token。online 模式还会额外下载引用论文。">
          <button
            onClick={handleStart}
            disabled={disabled || selectedUsableCount === 0}
            className="px-4 py-1.5 text-xs font-medium rounded-lg transition-colors disabled:opacity-50"
            style={{
              backgroundColor: disabled ? 'var(--border)' : 'var(--accent)',
              color: 'var(--bg-base)',
            }}
          >
            {disabled
              ? '运行中...'
              : `开始研究（${selectedUsableCount}${discoveredCount > 0 ? ` + ${discoveredCount}` : ''} 篇）`}
          </button>
        </Tooltip>
      </div>

      {/* 论文选择面板 */}
      {showPicker && (
        <div className="mt-3 pt-3 border-t" style={{ borderColor: 'var(--border)' }}>
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
              勾选作为研究 seed 的 L1 论文（共 {papers.length} 篇 · 可用 {usableCount}
              {discoveredCount > 0 && ` · 另有 ${discoveredCount} 篇深度研究发现自动纳入图谱`}）
            </span>
            <div className="flex gap-2">
              <button onClick={selectAll} className="text-xs underline" style={{ color: 'var(--text-secondary)' }}>全选可用</button>
              <button onClick={selectNone} className="text-xs underline" style={{ color: 'var(--text-muted)' }}>清空</button>
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
                      usable ? '' : 'opacity-50 cursor-not-allowed'
                    }`}
                    style={{ color: 'var(--text-secondary)' }}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={!usable || disabled}
                      onChange={() => toggleSelect(p.id)}
                      className="accent-blue-500"
                    />
                    <span className="truncate flex-1">{p.title || p.filename}</span>
                    <span className="shrink-0 text-[10px]" style={{ color: 'var(--text-muted)' }}>
                      {p.status === 'done' ? '已解析' :
                       p.status === 'indexed' ? '已索引' :
                       p.status === 'pending' ? '未解析（仅 bge 索引）' :
                       p.status === 'failed' ? '失败' :
                       p.status}
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
          <Tooltip content="两篇论文的向量余弦相似度高于此值时，图谱里会生成一条 similar 边。值越大边越少越精炼；值越小边越多越密。默认 0.78。">
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
