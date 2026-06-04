/**
 * ExperimentsTab 组件
 *
 * 实验 Tab：展示实验设置和结果对比。
 *
 * Props:
 *   result: AnalysisResult - 解析结果
 *
 * 展示内容：
 *   1. 数据集列表（标签样式）
 *   2. 基线方法列表
 *   3. 主要实验结论（文字描述）
 *   4. 实验结果对比表（基于 result_table 结构化数据）
 *      - result_table: ResultTableRow[] — 每行 {method, dataset, metric, value}
 *      - 使用 Recharts BarChart 可视化
 *      - 支持按 metric 排序
 *      - 高亮本文方法的数据行（method 包含 "Ours" 或 "本文"）
 *      - 如果 result_table 为空，仅展示 main_results 文字
 *   5. 消融实验说明
 *
 * 数据来源：
 *   result.experiments.result_table — AI 从论文中提取的结构化数值数据
 *   每行对应一个方法在某数据集上的某个指标值
 *
 * 依赖：
 *   - recharts (BarChart, Bar, XAxis, YAxis, Tooltip, Legend)
 *
 * 样式：
 *   - 数据集/基线标签：bg-gray-700, rounded-full, px-3, py-1
 *   - 对比表格：带排序功能的表头
 *   - Recharts 图表容器：bg-gray-800, rounded-lg, p-4
 *   - 消融实验：特殊卡片样式
 */

import { useMemo, useState } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import type { AnalysisResult, ResultTableRow } from '@/types'


interface ExperimentsTabProps {
  result: AnalysisResult
}

/**
 * 将 result_table 转换为 Recharts BarChart 数据
 * 按 dataset+metric 分组，每个 method 一个 bar
 */
function buildChartData(rows: ResultTableRow[]) {
  if (rows.length === 0) return { data: [], methods: [] }

  const metrics = [...new Set(rows.map(r => r.metric))]
  const selectedMetric = metrics[0]

  const metricRows = rows.filter(r => r.metric === selectedMetric)

  // 按 dataset 分组
  const grouped = new Map<string, Record<string, unknown>>()
  const methods = new Set<string>()

  metricRows.forEach(row => {
    if (!grouped.has(row.dataset)) {
      grouped.set(row.dataset, { dataset: row.dataset })
    }
    const entry = grouped.get(row.dataset)!
    const numValue = parseFloat(row.value)
    entry[row.method] = isNaN(numValue) ? row.value : numValue
    methods.add(row.method)
  })

  return {
    data: Array.from(grouped.values()),
    methods: Array.from(methods),
    metric: selectedMetric,
    metrics,
  }
}

export function ExperimentsTab({ result }: ExperimentsTabProps) {
  const { experiments } = result
  const [selectedMetric, setSelectedMetric] = useState<string | null>(null)

  const chartData = useMemo(() => {
    const rows = experiments.result_table || []
    if (selectedMetric) {
      return buildChartData(rows.filter(r => r.metric === selectedMetric))
    }
    return buildChartData(rows)
  }, [experiments.result_table, selectedMetric])

  // 判断是否为本文方法
  const isOurMethod = (name: string) =>
    /ours|本文|proposed/i.test(name)

  const colors = [
    '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
    '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#6366f1',
  ]

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

      {/* 主要实验结论 */}
      {experiments.main_results && (
        <section>
          <h2 className="text-lg font-semibold text-gray-200 mb-2">主要结论</h2>
          <p className="text-gray-300 text-sm leading-relaxed">{experiments.main_results}</p>
        </section>
      )}

      {/* 实验结果图表 */}
      {experiments.result_table && experiments.result_table.length > 0 ? (
        <section>
          <h2 className="text-lg font-semibold text-gray-200 mb-3">实验结果对比</h2>

          {/* Metric 选择 */}
          {chartData.metrics && chartData.metrics.length > 1 && (
            <div className="flex gap-2 mb-4">
              {chartData.metrics.map(m => (
                <button
                  key={m}
                  onClick={() => setSelectedMetric(m)}
                  className={`px-3 py-1 text-xs rounded-full transition-colors ${
                    (selectedMetric || chartData.metrics![0]) === m
                      ? 'bg-blue-500 text-white'
                      : 'bg-gray-700 text-gray-400 hover:bg-gray-600'
                  }`}
                >
                  {m}
                </button>
              ))}
            </div>
          )}

          {/* 图表 */}
          <div className="bg-gray-800 rounded-lg p-4">
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={chartData.data}>
                <XAxis
                  dataKey="dataset"
                  tick={{ fill: '#9ca3af', fontSize: 12 }}
                  axisLine={{ stroke: '#4b5563' }}
                />
                <YAxis
                  tick={{ fill: '#9ca3af', fontSize: 12 }}
                  axisLine={{ stroke: '#4b5563' }}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#1f2937',
                    border: '1px solid #374151',
                    borderRadius: '0.5rem',
                    color: '#e5e7eb',
                  }}
                />
                <Legend
                  wrapperStyle={{ color: '#9ca3af', fontSize: 12 }}
                />
                {chartData.methods.map((method, idx) => (
                  <Bar
                    key={method}
                    dataKey={method}
                    fill={isOurMethod(method) ? '#3b82f6' : colors[idx % colors.length]}
                    radius={[2, 2, 0, 0]}
                  />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* 数据表格 */}
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-700">
                  <th className="text-left py-2 px-3 text-gray-400 font-medium">方法</th>
                  <th className="text-left py-2 px-3 text-gray-400 font-medium">数据集</th>
                  <th className="text-left py-2 px-3 text-gray-400 font-medium">指标</th>
                  <th className="text-left py-2 px-3 text-gray-400 font-medium">值</th>
                </tr>
              </thead>
              <tbody>
                {experiments.result_table.map((row, idx) => (
                  <tr
                    key={idx}
                    className={`${idx % 2 === 0 ? 'bg-gray-800/50' : ''} ${
                      isOurMethod(row.method) ? 'text-blue-400 font-medium' : 'text-gray-300'
                    }`}
                  >
                    <td className="py-2 px-3">{row.method}</td>
                    <td className="py-2 px-3">{row.dataset}</td>
                    <td className="py-2 px-3">{row.metric}</td>
                    <td className="py-2 px-3">{row.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : (
        experiments.main_results && null // 已在上方显示
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
