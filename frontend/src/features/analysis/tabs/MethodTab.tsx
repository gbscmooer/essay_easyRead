/**
 * MethodTab 组件
 *
 * 方法 Tab：展示方法论详情和流程图。
 *
 * Props:
 *   result: AnalysisResult - 解析结果
 *
 * 展示内容：
 *   1. 方法总览（文字描述）
 *   2. Mermaid 流程图（基于 pipeline 生成）
 *      - 使用 mermaid 库渲染
 *      - 深色主题适配
 *      - 支持缩放和拖拽
 *   3. Pipeline 详细步骤（逐步展示）
 *   4. 关键模块表格
 *      - 表头：模块名 | 作用 | 创新点
 *      - 高亮本文方法的行
 *
 * 依赖：
 *   - mermaid (用于渲染流程图)
 *   - FlowChart 子组件
 *
 * 样式：
 *   - 流程图容器：bg-gray-800, 圆角, p-4, 居中
 *   - 模块表格：table-auto, 条纹背景
 *   - 步骤列表：竖向时间线样式
 */

import type { AnalysisResult } from '@/types'
import { FlowChart } from '../FlowChart'


interface MethodTabProps {
  result: AnalysisResult
}

export function MethodTab({ result }: MethodTabProps) {
  const { method, mermaid_chart } = result
  if (!method) {
    return <div className="text-sm text-center py-8" style={{ color: 'var(--text-muted)' }}>暂无方法论分析</div>
  }
  const pipelineSteps = method.pipeline
    ? method.pipeline.split('\n').filter(s => s.trim())
    : []

  return (
    <div className="space-y-6">
      {/* 方法总览 */}
      {method.overview && (
        <section>
          <h2 className="text-lg font-semibold mb-2" style={{ color: 'var(--text-primary)' }}>方法总览</h2>
          <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{method.overview}</p>
        </section>
      )}

      {/* Mermaid 流程图 */}
      {mermaid_chart && (
        <section>
          <h2 className="text-lg font-semibold mb-3" style={{ color: 'var(--text-primary)' }}>流程图</h2>
          <FlowChart chart={mermaid_chart} />
        </section>
      )}

      {/* Pipeline 详细步骤 */}
      {pipelineSteps.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3" style={{ color: 'var(--text-primary)' }}>Pipeline 步骤</h2>
          <div className="relative pl-6">
            {/* 竖线 */}
            <div className="absolute left-2 top-2 bottom-2 w-px" style={{ backgroundColor: 'var(--border-light)' }} />

            <div className="space-y-4">
              {pipelineSteps.map((step, idx) => (
                <div key={idx} className="relative flex items-start gap-3">
                  {/* 节点 */}
                  <div className="absolute -left-4 top-1 w-3 h-3 rounded-full z-10" style={{ backgroundColor: 'var(--accent)', border: '2px solid var(--bg-base)' }} />
                  <div className="ml-2">
                    <span className="text-xs block mb-0.5" style={{ color: 'var(--text-muted)' }}>步骤 {idx + 1}</span>
                    <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>{step}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      {/* 关键模块表格 */}
      {method.key_modules && method.key_modules.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3" style={{ color: 'var(--text-primary)' }}>关键模块</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b" style={{ borderColor: 'var(--border-light)' }}>
                  <th className="text-left py-2 px-3 font-medium" style={{ color: 'var(--text-secondary)' }}>模块名</th>
                  <th className="text-left py-2 px-3 font-medium" style={{ color: 'var(--text-secondary)' }}>作用</th>
                  <th className="text-left py-2 px-3 font-medium" style={{ color: 'var(--text-secondary)' }}>创新点</th>
                </tr>
              </thead>
              <tbody>
                {method.key_modules.map((mod, idx) => (
                  <tr
                    key={idx}
                    style={idx % 2 === 0 ? { backgroundColor: 'var(--bg-elevated)' } : undefined}
                  >
                    <td className="py-2 px-3 text-blue-400 font-medium">{mod.name}</td>
                    <td className="py-2 px-3" style={{ color: 'var(--text-secondary)' }}>{mod.role}</td>
                    <td className="py-2 px-3" style={{ color: 'var(--text-secondary)' }}>{mod.innovation}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  )
}
