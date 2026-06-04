import { useMemo, useState } from 'react'
import {
  BarChart, Bar, LineChart, Line, ScatterChart, Scatter,
  XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, CartesianGrid,
} from 'recharts'
import type { AnalysisResult, FigureAnalysis } from '@/types'


interface ExperimentsTabProps {
  result: AnalysisResult
}


const CHART_COLORS = [
  '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
  '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#6366f1',
]


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


function FigureChart({ fig }: { fig: FigureAnalysis }) {
  const data = useMemo(() => buildRechartsData(fig), [fig])
  const cols = fig.columns || []

  if (!data || cols.length < 2) {
    return (
      <div className="text-gray-500 text-xs py-4 text-center">
        数据不足以生成图表
      </div>
    )
  }

  const xKey = cols[0]
  const valueKeys = cols.slice(1)

  const tooltipStyle = {
    backgroundColor: '#1f2937',
    border: '1px solid #374151',
    borderRadius: '0.5rem',
    color: '#e5e7eb',
    fontSize: 12,
  }

  if (fig.chart_type === 'line') {
    return (
      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis dataKey={xKey} tick={{ fill: '#9ca3af', fontSize: 11 }} />
          <YAxis tick={{ fill: '#9ca3af', fontSize: 11 }} />
          <Tooltip contentStyle={tooltipStyle} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
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
    return (
      <ResponsiveContainer width="100%" height={280}>
        <ScatterChart>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis dataKey={xVal} name={xVal} tick={{ fill: '#9ca3af', fontSize: 11 }} />
          <YAxis dataKey={yVal} name={yVal} tick={{ fill: '#9ca3af', fontSize: 11 }} />
          <Tooltip contentStyle={tooltipStyle} />
          <Scatter name="数据" data={data} fill="#3b82f6" />
        </ScatterChart>
      </ResponsiveContainer>
    )
  }

  // 默认柱状图
  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
        <XAxis dataKey={xKey} tick={{ fill: '#9ca3af', fontSize: 11 }} />
        <YAxis tick={{ fill: '#9ca3af', fontSize: 11 }} />
        <Tooltip contentStyle={tooltipStyle} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
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


function FigureCard({ fig }: { fig: FigureAnalysis }) {
  const [showTable, setShowTable] = useState(false)
  const cols = fig.columns || []

  return (
    <div className="bg-gray-800/50 rounded-lg border border-gray-700/50 overflow-hidden">
      {/* 图表标题 */}
      <div className="px-4 py-3 border-b border-gray-700/50">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium text-blue-300">{fig.name}</h3>
          <span className="text-xs text-gray-500 bg-gray-800 px-2 py-0.5 rounded">
            {fig.chart_type === 'bar' ? '柱状图' : fig.chart_type === 'line' ? '折线图' : fig.chart_type === 'scatter' ? '散点图' : '表格'}
          </span>
        </div>
        {fig.caption && (
          <p className="text-xs text-gray-400 mt-1">{fig.caption}</p>
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
            className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
          >
            {showTable ? '收起数据 ▲' : '查看数据 ▼'}
          </button>

          {showTable && (
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-gray-700">
                    {cols.map((col, i) => (
                      <th key={i} className="text-left py-1.5 px-2 text-gray-400 font-medium">
                        {col}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {fig.rows.map((row, ri) => (
                    <tr key={ri} className={ri % 2 === 0 ? 'bg-gray-800/30' : ''}>
                      {row.map((cell, ci) => (
                        <td key={ci} className="py-1.5 px-2 text-gray-300">
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

      {/* AI 解读 */}
      {fig.interpretation && (
        <div className="px-4 py-3 bg-blue-500/5 border-t border-gray-700/50">
          <p className="text-xs text-gray-400 mb-1 font-medium">AI 解读</p>
          <p className="text-xs text-gray-300 leading-relaxed">{fig.interpretation}</p>
        </div>
      )}
    </div>
  )
}


export function ExperimentsTab({ result }: ExperimentsTabProps) {
  const { experiments } = result

  const figures = experiments.figures || []

  return (
    <div className="space-y-6">
      {/* 数据集标签 */}
      {experiments.datasets && experiments.datasets.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold text-gray-200 mb-3">数据集</h2>
          <div className="flex flex-wrap gap-2">
            {experiments.datasets.map((ds, idx) => (
              <span key={idx} className="px-3 py-1 text-sm bg-gray-700 text-gray-300 rounded-full">
                {ds}
              </span>
            ))}
          </div>
        </section>
      )}

      {/* 基线方法 */}
      {experiments.baselines && experiments.baselines.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold text-gray-200 mb-3">基线方法</h2>
          <div className="flex flex-wrap gap-2">
            {experiments.baselines.map((bl, idx) => (
              <span key={idx} className="px-3 py-1 text-sm bg-gray-700 text-gray-300 rounded-full">
                {bl}
              </span>
            ))}
          </div>
        </section>
      )}

      {/* 主要结论 */}
      {experiments.main_results && (
        <section>
          <h2 className="text-lg font-semibold text-gray-200 mb-2">主要结论</h2>
          <p className="text-gray-300 text-sm leading-relaxed">{experiments.main_results}</p>
        </section>
      )}

      {/* 逐图表分析 */}
      {figures.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold text-gray-200 mb-3">
            图表分析
            <span className="text-xs text-gray-500 font-normal ml-2">共 {figures.length} 项</span>
          </h2>
          <div className="space-y-4">
            {figures.map((fig, idx) => (
              <FigureCard key={idx} fig={fig} />
            ))}
          </div>
        </section>
      )}

      {/* 消融实验 */}
      {experiments.ablation && (
        <section>
          <h2 className="text-lg font-semibold text-gray-200 mb-2">消融实验</h2>
          <div className="bg-gray-800/60 border border-gray-700 rounded-lg p-4">
            <p className="text-gray-300 text-sm leading-relaxed">{experiments.ablation}</p>
          </div>
        </section>
      )}
    </div>
  )
}
