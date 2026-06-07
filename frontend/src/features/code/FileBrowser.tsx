import { useState } from 'react'
import type { FileNode } from '@/types'


interface FileBrowserProps {
  tree: FileNode | null
  selected: string | null
  onSelect: (path: string) => void
}


const FILE_ICONS: Record<string, { icon: string; color: string }> = {
  // Python
  py: { icon: '🐍', color: '#3572A5' },
  // JavaScript/TypeScript
  js: { icon: 'JS', color: '#f1e05a' },
  jsx: { icon: 'JS', color: '#f1e05a' },
  ts: { icon: 'TS', color: '#3178c6' },
  tsx: { icon: 'TS', color: '#3178c6' },
  // Web
  html: { icon: '<>', color: '#e34c26' },
  css: { icon: '#', color: '#563d7c' },
  scss: { icon: '#', color: '#c6538c' },
  // Config
  json: { icon: '{ }', color: '#40b5a4' },
  yaml: { icon: '⚙', color: '#cb171e' },
  yml: { icon: '⚙', color: '#cb171e' },
  toml: { icon: '⚙', color: '#9c4121' },
  // Docs
  md: { icon: 'M↓', color: '#083fa1' },
  txt: { icon: 'T', color: '#89e051' },
  // Build
  sh: { icon: '$_', color: '#89e051' },
  bash: { icon: '$_', color: '#89e051' },
  // Systems
  cpp: { icon: 'C+', color: '#f34b7d' },
  c: { icon: 'C', color: '#555555' },
  h: { icon: 'H', color: '#555555' },
  hpp: { icon: 'H+', color: '#555555' },
  rs: { icon: '🦀', color: '#dea584' },
  go: { icon: 'Go', color: '#00ADD8' },
  java: { icon: 'J', color: '#b07219' },
  // Images
  png: { icon: '🖼', color: '#a074c4' },
  jpg: { icon: '🖼', color: '#a074c4' },
  svg: { icon: '🖼', color: '#a074c4' },
  // Other
  ipynb: { icon: '📓', color: '#DA5B0B' },
  dockerfile: { icon: '🐳', color: '#384d54' },
  makefile: { icon: 'M', color: '#427819' },
}


function getFileIcon(name: string): { icon: string; color: string } {
  const ext = name.split('.').pop()?.toLowerCase() || ''
  const lower = name.toLowerCase()

  if (lower === 'dockerfile') return FILE_ICONS['dockerfile']
  if (lower === 'makefile') return FILE_ICONS['makefile']
  if (lower === 'readme' || lower.startsWith('readme.')) return FILE_ICONS['md']
  if (lower === 'license' || lower === 'licence') return { icon: '©', color: 'var(--code-text-muted)' }
  if (lower === '.gitignore') return { icon: 'G', color: '#f05032' }
  if (lower === 'requirements.txt') return { icon: '📦', color: '#3572A5' }
  if (lower === 'package.json') return { icon: '📦', color: '#cb3837' }

  return FILE_ICONS[ext] || { icon: '📄', color: 'var(--code-text-muted)' }
}


function TreeNode({
  node,
  depth,
  selected,
  onSelect,
}: {
  node: FileNode
  depth: number
  selected: string | null
  onSelect: (path: string) => void
}) {
  const [expanded, setExpanded] = useState(depth < 2)

  if (node.type === 'file') {
    const isSelected = selected === node.path
    const { icon, color } = getFileIcon(node.name)
    return (
      <button
        onClick={() => onSelect(node.path)}
        className="w-full text-left flex items-center gap-1.5 py-[2px] text-[13px] group transition-colors"
        style={{
          paddingLeft: `${depth * 16 + 8}px`,
          paddingRight: 8,
          color: isSelected ? 'var(--text-primary)' : 'var(--code-text)',
          backgroundColor: isSelected ? 'var(--code-selection)' : undefined,
        }}
        onMouseEnter={(e) => {
          if (!isSelected) e.currentTarget.style.backgroundColor = 'var(--code-hover)'
        }}
        onMouseLeave={(e) => {
          if (!isSelected) e.currentTarget.style.backgroundColor = ''
        }}
      >
        {/* 缩进引导线 */}
        {depth > 0 && (
          <span
            className="absolute left-0 top-0 bottom-0 border-l"
            style={{ marginLeft: `${(depth - 1) * 16 + 20}px`, borderColor: 'var(--code-border)' }}
          />
        )}
        <span
          className="shrink-0 text-[11px] font-bold w-4 text-center"
          style={{ color }}
        >
          {icon}
        </span>
        <span className="truncate">{node.name}</span>
        {node.size !== undefined && node.size > 100000 && (
          <span className="text-[10px] ml-auto shrink-0" style={{ color: 'var(--code-text-muted)' }}>
            {(node.size / 1024).toFixed(0)}KB
          </span>
        )}
      </button>
    )
  }

  // Directory
  return (
    <div>
      <button
        onClick={() => setExpanded(v => !v)}
        className="w-full text-left flex items-center gap-1 py-[2px] text-[13px] transition-colors"
        style={{ paddingLeft: `${depth * 16 + 8}px`, paddingRight: 8, color: 'var(--code-text)' }}
        onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = 'var(--code-hover)' }}
        onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = '' }}
      >
        <svg
          className={`w-3 h-3 shrink-0 transition-transform ${expanded ? 'rotate-90' : ''}`}
          fill="currentColor"
          viewBox="0 0 16 16"
        >
          <path d="M6 4l4 4-4 4V4z" />
        </svg>
        <span className="shrink-0">
          {expanded ? (
            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 16 16" style={{ color: 'var(--code-folder)' }}>
              <path d="M1.5 3A1.5 1.5 0 013 1.5h3.146a.5.5 0 01.354.146L7.5 2.646h5A1.5 1.5 0 0114 4.146v7.354A1.5 1.5 0 0112.5 13h-10A1.5 1.5 0 011 11.5v-8A1.5 1.5 0 012.5 2H3v1.5a.5.5 0 00.5.5h.5V3H3z" />
            </svg>
          ) : (
            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 16 16" style={{ color: 'var(--code-folder)' }}>
              <path d="M1 3.5A1.5 1.5 0 012.5 2h2.764a1.5 1.5 0 011.06.44l1.122 1.12A1.5 1.5 0 008.564 4H13.5A1.5 1.5 0 0115 5.5v7a1.5 1.5 0 01-1.5 1.5h-10A1.5 1.5 0 012 12.5v-9z" />
            </svg>
          )}
        </span>
        <span className="truncate font-medium" style={{ color: 'var(--code-text-bright)' }}>{node.name}</span>
      </button>
      {expanded && node.children && (
        <div className="relative">
          {[...node.children]
            .sort((a, b) => {
              if (a.type !== b.type) return a.type === 'dir' ? -1 : 1
              return a.name.localeCompare(b.name)
            })
            .map((child, idx) => (
              <TreeNode
                key={idx}
                node={child}
                depth={depth + 1}
                selected={selected}
                onSelect={onSelect}
              />
            ))}
        </div>
      )}
    </div>
  )
}


export function FileBrowser({ tree, selected, onSelect }: FileBrowserProps) {
  if (!tree) {
    return (
      <div className="p-4 text-center text-sm" style={{ color: 'var(--code-text-muted)' }}>
        暂无文件
      </div>
    )
  }

  return (
    <div className="overflow-auto h-full py-1" style={{ backgroundColor: 'var(--code-bg)' }}>
      {/* 标题 */}
      <div className="px-3 py-1.5 flex items-center justify-between text-[11px] uppercase tracking-wider font-medium" style={{ color: 'var(--code-text)' }}>
        <span>资源管理器</span>
        <button className="transition-colors" style={{ color: 'var(--code-text-muted)' }}
          onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--code-text-bright)' }}
          onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--code-text-muted)' }}
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
        </button>
      </div>
      <TreeNode node={tree} depth={0} selected={selected} onSelect={onSelect} />
    </div>
  )
}
