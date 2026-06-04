/**
 * FlowChart 组件
 *
 * Mermaid 流程图渲染。
 *
 * Props:
 *   chart: string - Mermaid 语法字符串
 *
 * 功能：
 *   - 使用 mermaid 库渲染流程图
 *   - 深色主题适配（mermaid theme: dark）
 *   - 渲染为 SVG，嵌入页面
 *   - 错误处理：如果语法错误，显示原始文本
 *
 * 依赖：
 *   - mermaid (npm package)
 *
 * 实现要点：
 *   1. mermaid.initialize({ theme: 'dark', ... })
 *   2. mermaid.render(id, chart) 获取 SVG
 *   3. 使用 dangerouslySetInnerHTML 插入 SVG
 *   4. useEffect 在 chart 变化时重新渲染
 *
 * 样式：
 *   - 容器：bg-gray-800, rounded-lg, p-4, overflow-auto
 *   - SVG 居中显示
 */

import { useEffect, useRef, useState } from 'react'
import mermaid from 'mermaid'


interface FlowChartProps {
  chart: string
}

let mermaidInitialized = false

function initMermaid() {
  if (!mermaidInitialized) {
    mermaid.initialize({
      startOnLoad: false,
      theme: 'dark',
      themeVariables: {
        primaryColor: '#3b82f6',
        primaryTextColor: '#e5e7eb',
        primaryBorderColor: '#6b7280',
        lineColor: '#6b7280',
        secondaryColor: '#1f2937',
        tertiaryColor: '#111827',
        background: '#1f2937',
        mainBkg: '#1f2937',
        nodeBorder: '#6b7280',
        clusterBkg: '#111827',
        clusterBorder: '#374151',
        edgeLabelBackground: '#1f2937',
      },
    })
    mermaidInitialized = true
  }
}

export function FlowChart({ chart }: FlowChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [svg, setSvg] = useState<string>('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!chart || !containerRef.current) return

    initMermaid()

    const id = `mermaid-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`

    mermaid.render(id, chart).then(
      ({ svg: renderedSvg }) => {
        setSvg(renderedSvg)
        setError(null)
      },
      (err) => {
        setError(err?.message || '流程图语法错误')
        setSvg('')
      }
    )
  }, [chart])

  if (error) {
    return (
      <div className="bg-gray-800 rounded-lg p-4 overflow-auto">
        <p className="text-red-400 text-sm mb-2">流程图渲染失败</p>
        <pre className="text-gray-400 text-xs whitespace-pre-wrap">{chart}</pre>
      </div>
    )
  }

  return (
    <div ref={containerRef} className="mermaid bg-gray-800 rounded-lg p-4 overflow-auto flex justify-center">
      {svg ? (
        <div dangerouslySetInnerHTML={{ __html: svg }} />
      ) : (
        <div className="flex items-center justify-center py-8">
          <div className="w-6 h-6 border-2 border-gray-600 border-t-blue-500 rounded-full animate-spin" />
        </div>
      )}
    </div>
  )
}
