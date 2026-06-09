import { useMemo, useState } from 'react'
import {
  BarChart, Bar, LineChart, Line, ScatterChart, Scatter,
  XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, CartesianGrid,
} from 'recharts'
import type { AnalysisResult, FigureAnalysis } from '@/types'
import { useUIConfig } from '@/hooks/useConfig'


interface ExperimentsTabProps {
  result: AnalysisResult
  onJumpToPage?: (page: number) => void
}


const CHART_COLORS = [
  '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
  '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#6366f1',
]


/** 读取当前主题的 CSS 变量，主题切换时随 useUIConfig 重新计算，保证图表与主题一致 */
function useThemeColors() {
  const ui = useUIConfig()
  return useMemo(() => {
    const cs = getComputedStyle(document.documentElement)
    const v = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback
    return {
      grid: v('--border', '#374151'),
      tick: v('--text-secondary', '#9ca3af'),
      surface: v('--bg-elevated', '#1f2937'),
      border: v('--border', '#374151'),
      text: v('--text-primary', '#e5e7eb'),
      accent: v('--accent', '#3b82f6'),
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ui.theme])
}


/** 将 FigureAnalysis 的 rows 转换为 Recharts 数据 */
function buildRechartsData(fig: FigureAnalysis) {
  if (!fig.columns || !fig.rows || fig.rows.length === 0) return null
  const cols = fig.columns
  // 第一列作为 x 轴分类
  return fig.rows.map(row => {
    const entry: Record<string, unknown> = {}
    cols.forEach((col, i) => {
      const val = row[i] ?? ''
      const num = parseFloat(val)
      entry[col] = isNaN(num) ? val : num
    })
    return entry
  })
}


/**
 * 计算自适应坐标轴范围：不强制从 0 开始，按数据实际范围 + 10% 余量取整，
 * 避免数值集中在高位（如 99~100）时因总尺度过大而看不出差异。
 */
function computeDomain(
  data: Record<string, unknown>[],
  keys: string[],
): [number, number] | undefined {
  let min = Infinity
  let max = -Infinity
  for (const row of data) {
    for (const k of keys) {
      const val = row[k]
      if (typeof val === 'number' && isFinite(val)) {
        if (val < min) min = val
        if (val > max) max = val
      }
    }
  }
  if (!isFinite(min) || !isFinite(max)) return undefined
  if (min === max) {
    const pad = Math.abs(min) * 0.1 || 1
    return [min - pad, max + pad]
  }
  const pad = (max - min) * 0.12
  let lo = min - pad
  const hi = max + pad
  // 数据非负且不太靠近 0 时，下界不要越过 0
  if (min >= 0 && lo < 0) lo = 0
  return [lo, hi]
}


function FigureChart({ fig }: { fig: FigureAnalysis }) {
  const data = useMemo(() => buildRechartsData(fig), [fig])
  const colors = useThemeColors()
  const cols = fig.columns || []

  if (!data || cols.length < 2) {
    return (
      <div className="text-xs py-4 text-center" style={{ color: 'var(--text-muted)' }}>
        数据不足以生成图表
      </div>
    )
  }

  const xKey = cols[0]
  const valueKeys = cols.slice(1)

  const tooltipStyle = {
    backgroundColor: colors.surface,
    border: `1px solid ${colors.border}`,
    borderRadius: '0.5rem',
    color: colors.text,
    fontSize: 12,
  }
  const tooltipItemStyle = { color: colors.text }
  const tooltipLabelStyle = { color: colors.text }
  const axisTick = { fill: colors.tick, fontSize: 11 }

  const yDomain = computeDomain(data, valueKeys)

  if (fig.chart_type === 'line') {
    return (
      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke={colors.grid} />
          <XAxis dataKey={xKey} tick={axisTick} stroke={colors.grid} />
          <YAxis domain={yDomain ?? ['auto', 'auto']} tick={axisTick} stroke={colors.grid} allowDecimals />
          <Tooltip contentStyle={tooltipStyle} itemStyle={tooltipItemStyle} labelStyle={tooltipLabelStyle} />
          <Legend wrapperStyle={{ fontSize: 12, color: colors.text }} />
          {valueKeys.map((key, idx) => (
            <Line
              key={key}
              type="monotone"
              dataKey={key}
              stroke={CHART_COLORS[idx % CHART_COLORS.length]}
              strokeWidth={2}
              dot={{ r: 3 }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    )
  }

  if (fig.chart_type === 'scatter') {
    // 散点图用前两个数值列
    const xVal = valueKeys[0]
    const yVal = valueKeys[1] || valueKeys[0]
    const xDomain = computeDomain(data, [xVal])
    const scatterYDomain = computeDomain(data, [yVal])
    return (
      <ResponsiveContainer width="100%" height={280}>
        <ScatterChart>
          <CartesianGrid strokeDasharray="3 3" stroke={colors.grid} />
          <XAxis dataKey={xVal} name={xVal} type="number" domain={xDomain ?? ['auto', 'auto']} tick={axisTick} stroke={colors.grid} />
          <YAxis dataKey={yVal} name={yVal} type="number" domain={scatterYDomain ?? ['auto', 'auto']} tick={axisTick} stroke={colors.grid} />
          <Tooltip contentStyle={tooltipStyle} itemStyle={tooltipItemStyle} labelStyle={tooltipLabelStyle} cursor={{ strokeDasharray: '3 3' }} />
          <Scatter name="数据" data={data} fill={colors.accent} />
        </ScatterChart>
      </ResponsiveContainer>
    )
  }

  // 默认柱状图
  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke={colors.grid} />
        <XAxis dataKey={xKey} tick={axisTick} stroke={colors.grid} />
        <YAxis domain={yDomain ?? [0, 'auto']} tick={axisTick} stroke={colors.grid} allowDecimals />
        <Tooltip contentStyle={tooltipStyle} itemStyle={tooltipItemStyle} labelStyle={tooltipLabelStyle} cursor={{ fill: colors.accent, fillOpacity: 0.08 }} />
        <Legend wrapperStyle={{ fontSize: 12, color: colors.text }} />
        {valueKeys.map((key, idx) => (
          <Bar
            key={key}
            dataKey={key}
            fill={CHART_COLORS[idx % CHART_COLORS.length]}
            radius={[2, 2, 0, 0]}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}


function FigureCard({ fig, onJumpToPage }: { fig: FigureAnalysis; onJumpToPage?: (page: number) => void }) {
  const [showTable, setShowTable] = useState(false)
  const cols = fig.columns || []

  const pageNum = useMemo(() => {
    if (!fig.page) return null
    const m = String(fig.page).match(/\d+/)
    return m ? parseInt(m[0], 10) : null
  }, [fig.page])
  const clickable = !!pageNum && !!onJumpToPage

  return (
    <div className="rounded-lg overflow-hidden" style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
      {/* 图表标题 */}
      <div className="px-4 py-3" style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="flex items-center justify-between">
          {clickable ? (
            <button
              onClick={() => pageNum && onJumpToPage?.(pageNum)}
              className="group flex items-center gap-1.5 text-sm font-medium transition-colors"
              style={{ color: 'var(--accent)' }}
              title={`跳转到第 ${pageNum} 页`}
            >
              <span className="group-hover:underline">{fig.name}</span>
              <svg className="w-3.5 h-3.5 opacity-60 group-hover:opacity-100" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
              </svg>
            </button>
          ) : (
            <h3 className="text-sm font-medium" style={{ color: 'var(--accent)' }}>{fig.name}</h3>
          )}
          <span className="text-xs px-2 py-0.5 rounded" style={{ color: 'var(--text-muted)', backgroundColor: 'var(--bg-elevated)' }}>
            {fig.chart_type === 'bar' ? '柱状图' : fig.chart_type === 'line' ? '折线图' : fig.chart_type === 'scatter' ? '散点图' : '表格'}
          </span>
        </div>
        {fig.caption && (
          <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>{fig.caption}</p>
        )}
      </div>

      {/* 图表 */}
      {fig.chart_type !== 'table' && cols.length >= 2 && (
        <div className="px-4 pt-4">
          <FigureChart fig={fig} />
        </div>
      )}

      {/* 数据表格（可折叠） */}
      {fig.rows && fig.rows.length > 0 && (
        <div className="px-4 py-2">
          <button
            onClick={() => setShowTable(v => !v)}
            className="text-xs transition-colors"
            style={{ color: 'var(--text-muted)' }}
          >
            {showTable ? '收起数据 ▲' : '查看数据 ▼'}
          </button>

          {showTable && (
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border)' }}>
                    {cols.map((col, i) => (
                      <th key={i} className="text-left py-1.5 px-2 font-medium" style={{ color: 'var(--text-secondary)' }}>
                        {col}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {fig.rows.map((row, ri) => (
                    <tr key={ri} style={ri % 2 === 0 ? { backgroundColor: 'var(--bg-elevated)' } : undefined}>
                      {row.map((cell, ci) => (
                        <td key={ci} className="py-1.5 px-2" style={{ color: 'var(--text-primary)' }}>
                          {cell}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* 解读 */}
      {fig.interpretation && (
        <div className="px-4 py-3" style={{ backgroundColor: 'var(--accent-subtle)', borderTop: '1px solid var(--border)' }}>
          <p className="text-xs mb-1 font-medium" style={{ color: 'var(--text-secondary)' }}>解读</p>
          <p className="text-xs leading-relaxed" style={{ color: 'var(--text-primary)' }}>{fig.interpretation}</p>
        </div>
      )}
    </div>
  )
}


export function ExperimentsTab({ result, onJumpToPage }: ExperimentsTabProps) {
  const { experiments } = result
  if (!experiments) {
    return <div className="text-sm text-center py-8" style={{ color: 'var(--text-muted)' }}>暂无实验分析</div>
  }

  const figures = experiments.figures || []

  return (
    <div className="space-y-6">
      {/* 数据集标签 */}
      {experiments.datasets && experiments.datasets.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3" style={{ color: 'var(--text-primary)' }}>数据集</h2>
          <div className="flex flex-wrap gap-2">
            {experiments.datasets.map((ds, idx) => (
              <span key={idx} className="px-3 py-1 text-sm rounded-full" style={{ backgroundColor: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}>
                {ds}
              </span>
            ))}
          </div>
        </section>
      )}

      {/* 基线方法 */}
      {experiments.baselines && experiments.baselines.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3" style={{ color: 'var(--text-primary)' }}>基线方法</h2>
          <div className="flex flex-wrap gap-2">
            {experiments.baselines.map((bl, idx) => (
              <span key={idx} className="px-3 py-1 text-sm rounded-full" style={{ backgroundColor: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}>
                {bl}
              </span>
            ))}
          </div>
        </section>
      )}

      {/* 主要结论 */}
      {experiments.main_results && (
        <section>
          <h2 className="text-lg font-semibold mb-2" style={{ color: 'var(--text-primary)' }}>主要结论</h2>
          <p className="text-sm leading-relaxed" style={{ color: 'var(--text-primary)' }}>{experiments.main_results}</p>
        </section>
      )}

      {/* 逐图表分析 */}
      {figures.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3" style={{ color: 'var(--text-primary)' }}>
            图表分析
            <span className="text-xs font-normal ml-2" style={{ color: 'var(--text-muted)' }}>共 {figures.length} 项</span>
          </h2>
          <div className="space-y-4">
            {figures.map((fig, idx) => (
              <FigureCard key={idx} fig={fig} onJumpToPage={onJumpToPage} />
            ))}
          </div>
        </section>
      )}

      {/* 消融实验 */}
      {experiments.ablation && (
        <section>
          <h2 className="text-lg font-semibold mb-2" style={{ color: 'var(--text-primary)' }}>消融实验</h2>
          <div className="rounded-lg p-4" style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
            <p className="text-sm leading-relaxed" style={{ color: 'var(--text-primary)' }}>{experiments.ablation}</p>
          </div>
        </section>
      )}
    </div>
  )
}

export default ExperimentsTab
