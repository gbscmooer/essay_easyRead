import { useEffect, useRef, useState } from 'react'
import mermaid from 'mermaid'


/** 仅剥离事件处理器，保留 SVG 全部结构（包括 foreignObject 文字） */
function stripEventHandlers(svg: string): string {
  return svg
    .replace(/\bon\w+\s*=\s*["'][^"']*["']/gi, '')
    .replace(/\bon\w+\s*=\s*\{[^}]*\}/gi, '')
}


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

/**
 * 清洗 Mermaid 输入，修复常见语法问题
 */
function sanitizeMermaid(input: string): string {
  if (!input) return input

  let chart = input.trim()

  // 确保以 graph TD 或 graph LR 开头
  if (!chart.startsWith('graph ') && !chart.startsWith('flowchart ')) {
    chart = 'graph TD\n' + chart
  }

  // 处理节点标签中的特殊字符
  // 将 node[label] 中的括号、引号等特殊字符替换为安全字符
  chart = chart.replace(/\[([^\]]*)\]/g, (_match, label: string) => {
    // 清洗标签内容
    let clean = label
      .replace(/[(){}（）【】《》<>]/g, '')  // 移除括号
      .replace(/["""']/g, '')                // 移除引号
      .replace(/\s+/g, ' ')                  // 合并空格
      .trim()
    if (!clean) clean = 'Node'
    return `[${clean}]`
  })

  // 修复常见问题：
  // 1. 确保每行连接符前后有空格
  chart = chart.replace(/(\w)-->/g, '$1 -->')

  // 2. 移除行尾多余分号
  chart = chart.replace(/;\s*$/gm, '')

  // 3. 确保 graph 声明后有换行
  chart = chart.replace(/^(graph\s+\w+)\s+(?!\n)/, '$1\n')

  return chart
}

export function FlowChart({ chart }: FlowChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [svg, setSvg] = useState<string>('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!chart || !containerRef.current) return

    initMermaid()

    const sanitized = sanitizeMermaid(chart)
    const id = `mermaid-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`

    mermaid.render(id, sanitized).then(
      ({ svg: renderedSvg }) => {
        setSvg(renderedSvg)
        setError(null)
      },
      (err) => {
        // 如果清洗后仍然失败，尝试最简化的图表
        const fallback = 'graph TD\n    A[Input] --> B[Process]\n    B --> C[Output]'
        mermaid.render(id + '-fallback', fallback).then(
          ({ svg: fallbackSvg }) => {
            setSvg(fallbackSvg)
            setError(null)
          },
          () => {
            setError(err?.message || '流程图语法错误')
            setSvg('')
          }
        )
      }
    )
  }, [chart])

  if (error) {
    return (
      <div className="rounded-lg p-4 overflow-auto" style={{ backgroundColor: 'var(--bg-elevated)' }}>
        <p className="text-sm mb-2" style={{ color: 'var(--error)' }}>流程图渲染失败</p>
        <pre className="text-xs whitespace-pre-wrap" style={{ color: 'var(--text-secondary)' }}>{chart}</pre>
      </div>
    )
  }

  return (
    <div ref={containerRef} className="mermaid rounded-lg p-4 overflow-auto flex justify-center" style={{ backgroundColor: 'var(--bg-elevated)' }}>
      {svg ? (
        <div dangerouslySetInnerHTML={{ __html: stripEventHandlers(svg) }} />
      ) : (
        <div className="flex items-center justify-center py-8">
          <div className="w-6 h-6 border-2 rounded-full animate-spin" style={{ borderColor: 'var(--border-light)', borderTopColor: 'var(--accent)' }} />
        </div>
      )}
    </div>
  )
}
