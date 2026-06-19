/**
 * MethodTab 组件
 *
 * 方法 Tab：展示方法论详情和流程图。
 * 内容：方法总览 / Mermaid 流程图 / Pipeline 步骤（时间线）/ 关键模块表格。
 */

import type { AnalysisResult } from '@/types'
import { FlowChart } from '../FlowChart'


interface MethodTabProps {
  result: AnalysisResult
}

export function MethodTab({ result }: MethodTabProps) {
  const { method, mermaid_chart } = result
  if (!method) {
    return <div className="text-[13px] text-center py-10" style={{ color: 'var(--text-muted)' }}>暂无方法论分析</div>
  }
  const pipelineSteps = method.pipeline
    ? method.pipeline.split('\n').filter(s => s.trim())
    : []

  return (
    <div className="space-y-7">
      {/* 方法总览 */}
      {method.overview && (
        <section>
          <h2 className="text-heading text-[15px] mb-2.5" style={{ color: 'var(--text-primary)' }}>方法总览</h2>
          <p className="text-[13px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{method.overview}</p>
        </section>
      )}

      {/* Mermaid 流程图 */}
      {mermaid_chart && (
        <section>
          <h2 className="text-heading text-[15px] mb-3" style={{ color: 'var(--text-primary)' }}>流程图</h2>
          <FlowChart chart={mermaid_chart} />
        </section>
      )}

      {/* Pipeline 详细步骤 */}
      {pipelineSteps.length > 0 && (
        <section>
          <h2 className="text-heading text-[15px] mb-3.5" style={{ color: 'var(--text-primary)' }}>Pipeline 步骤</h2>
          <div className="relative pl-6">
            {/* 竖线 */}
            <div className="absolute left-[5px] top-2 bottom-2 w-px" style={{ backgroundColor: 'var(--border)' }} />

            <div className="space-y-4">
              {pipelineSteps.map((step, idx) => (
                <div key={idx} className="relative flex items-start gap-3">
                  {/* 节点 */}
                  <div className="absolute -left-[19px] top-1.5 w-2 h-2 rounded-full z-10" style={{ backgroundColor: 'var(--bg-surface)', border: '1.5px solid var(--border-light)' }} />
                  <div>
                    <span className="text-[11px] font-medium block mb-1" style={{ color: 'var(--text-muted)' }}>步骤 {idx + 1}</span>
                    <p className="text-[13px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{step}</p>
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
          <h2 className="text-heading text-[15px] mb-3.5" style={{ color: 'var(--text-primary)' }}>关键模块</h2>
          <div className="rounded-xl overflow-hidden" style={{ border: '1px solid var(--border)' }}>
            <table className="w-full text-[13px]">
              <thead>
                <tr style={{ backgroundColor: 'var(--bg-surface)', borderBottom: '1px solid var(--border)' }}>
                  <th className="text-left py-2.5 px-3.5 font-medium" style={{ color: 'var(--text-muted)' }}>模块名</th>
                  <th className="text-left py-2.5 px-3.5 font-medium" style={{ color: 'var(--text-muted)' }}>作用</th>
                  <th className="text-left py-2.5 px-3.5 font-medium" style={{ color: 'var(--text-muted)' }}>创新点</th>
                </tr>
              </thead>
              <tbody>
                {method.key_modules.map((mod, idx) => (
                  <tr key={idx} style={{ borderTop: idx > 0 ? '1px solid var(--border)' : undefined }}>
                    <td className="py-2.5 px-3.5 font-medium align-top" style={{ color: 'var(--text-primary)' }}>{mod.name}</td>
                    <td className="py-2.5 px-3.5 align-top leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{mod.role}</td>
                    <td className="py-2.5 px-3.5 align-top leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{mod.innovation}</td>
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
