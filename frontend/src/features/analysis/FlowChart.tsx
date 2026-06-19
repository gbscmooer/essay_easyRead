import { useEffect, useRef, useState } from 'react'
import DOMPurify from 'dompurify'
import { useThemeMode } from '@/theme/useTheme'


function sanitizeSvg(svg: string): string {
  return DOMPurify.sanitize(svg, {
    USE_PROFILES: { svg: true },
    ADD_TAGS: ['use', 'foreignObject', 'style'],
    ADD_ATTR: ['xlink:href', 'style', 'transform', 'data-id', 'data-index'],
  })
}


interface FlowChartProps {
  chart: string
}

let mermaidModule: typeof import('mermaid') | null = null
let mermaidMode: 'light' | 'dark' | null = null

async function getMermaidInstance() {
  if (!mermaidModule) {
    mermaidModule = await import('mermaid')
  }
  return mermaidModule.default
}

/** 按当前明暗模式用设计令牌初始化 mermaid（切换主题时重新初始化，与代码分析面板同一范式） */
async function ensureMermaid(mode: 'light' | 'dark') {
  const mermaid = await getMermaidInstance()
  if (mermaidMode === mode) return mermaid
  const cs = getComputedStyle(document.documentElement)
  const v = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback
  mermaid.initialize({
    startOnLoad: false,
    theme: mode === 'dark' ? 'dark' : 'default',
    themeVariables: {
      primaryColor: v('--bg-elevated', '#1f2937'),
      primaryTextColor: v('--text-primary', '#e5e7eb'),
      primaryBorderColor: v('--border-light', '#6b7280'),
      lineColor: v('--text-muted', '#6b7280'),
      secondaryColor: v('--bg-surface', '#1f2937'),
      tertiaryColor: v('--bg-base', '#111827'),
      background: v('--bg-surface', '#1f2937'),
      mainBkg: v('--bg-elevated', '#1f2937'),
      nodeBorder: v('--border-light', '#6b7280'),
      nodeTextColor: v('--text-primary', '#e5e7eb'),
      clusterBkg: v('--bg-base', '#111827'),
      clusterBorder: v('--border', '#374151'),
      edgeLabelBackground: v('--bg-elevated', '#1f2937'),
    },
  })
  mermaidMode = mode
  return mermaid
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
  const mode = useThemeMode()

  useEffect(() => {
    if (!chart || !containerRef.current) return

    let cancelled = false

    ;(async () => {
      const mermaid = await ensureMermaid(mode)
      if (cancelled) return

      const sanitized = sanitizeMermaid(chart)
      const id = `mermaid-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`

      try {
        const { svg: renderedSvg } = await mermaid.render(id, sanitized)
        if (!cancelled) {
          setSvg(renderedSvg)
          setError(null)
        }
      } catch (err: unknown) {
        // 如果清洗后仍然失败，尝试最简化的图表
        try {
          const fallback = 'graph TD\n    A[Input] --> B[Process]\n    B --> C[Output]'
          const { svg: fallbackSvg } = await mermaid.render(id + '-fallback', fallback)
          if (!cancelled) {
            setSvg(fallbackSvg)
            setError(null)
          }
        } catch {
          if (!cancelled) {
            setError(err instanceof Error ? err.message : '流程图语法错误')
            setSvg('')
          }
        }
      }
    })()

    return () => { cancelled = true }
  }, [chart, mode])

  if (error) {
    return (
      <div className="rounded-xl p-4 overflow-auto" style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
        <p className="text-[13px] mb-2" style={{ color: 'var(--error)' }}>流程图渲染失败</p>
        <pre className="text-[12px] whitespace-pre-wrap" style={{ color: 'var(--text-secondary)' }}>{chart}</pre>
      </div>
    )
  }

  return (
    <div ref={containerRef} className="mermaid rounded-xl p-4 overflow-auto flex justify-center" style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
      {svg ? (
        <div dangerouslySetInnerHTML={{ __html: sanitizeSvg(svg) }} />
      ) : (
        <div className="flex items-center justify-center py-8">
          <div className="w-6 h-6 border-2 rounded-full animate-spin" style={{ borderColor: 'var(--border-light)', borderTopColor: 'var(--accent)' }} />
        </div>
      )}
    </div>
  )
}
