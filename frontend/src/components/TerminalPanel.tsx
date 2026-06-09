import { useState, useEffect, useRef, useCallback, lazy, Suspense } from 'react'
import { APP_VERSION } from './VersionPopup'

const RealTerminal = lazy(() =>
  import('@/features/code/Terminal').then(m => ({ default: m.Terminal }))
)


interface LogEntry {
  id: string
  time: string
  level: string
  source: string
  message: string
}


type TabKey = 'backend' | 'frontend' | 'bash'


// 前端日志捕获
const frontendLogs: LogEntry[] = []

let frontendListeners: Array<(entry: LogEntry) => void> = []

let logIdCounter = 0
function nextLogId(): string {
  return `${Date.now()}-${++logIdCounter}`
}


function LogLine({ entry }: { entry: LogEntry }) {
  const levelColor = {
    DEBUG: 'var(--text-muted)',
    INFO: 'var(--text-secondary)',
    WARNING: 'var(--warning)',
    WARN: 'var(--warning)',
    ERROR: 'var(--error)',
    CRITICAL: 'var(--error)',
  }[entry.level] || 'var(--text-secondary)'

  return (
    <div className="flex gap-2 px-3 py-0.5 text-xs font-mono leading-5"
      style={{}} onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--bg-elevated)' }} onMouseLeave={e => { e.currentTarget.style.backgroundColor = '' }}>
      <span className="shrink-0" style={{ color: 'var(--text-muted)' }}>{entry.time}</span>
      <span className="shrink-0 w-14" style={{ color: levelColor }}>{entry.level}</span>
      <span className="shrink-0 w-16" style={{ color: 'var(--text-muted)' }}>[{entry.source}]</span>
      <span className="break-all" style={{ color: 'var(--text-secondary)' }}>{entry.message}</span>
    </div>
  )
}


function BackendTab() {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const endRef = useRef<HTMLDivElement>(null)
  const autoScroll = useRef(true)

  useEffect(() => {
    const es = new EventSource('/api/logs/stream')

    es.onmessage = (event) => {
      try {
        const entry = { ...JSON.parse(event.data) as LogEntry, id: nextLogId() }
        setLogs(prev => [...prev.slice(-499), entry])
      } catch {
        // 忽略解析错误
      }
    }

    es.onerror = () => {
      // 自动重连由浏览器处理
    }

    return () => es.close()
  }, [])

  useEffect(() => {
    if (autoScroll.current) {
      endRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logs])

  return (
    <div
      className="overflow-auto h-full"
      onScroll={(e) => {
        const el = e.currentTarget
        autoScroll.current = el.scrollHeight - el.scrollTop - el.clientHeight < 50
      }}
    >
      {logs.length === 0 ? (
        <div className="p-4 text-xs text-center" style={{ color: 'var(--text-muted)' }}>等待日志...</div>
      ) : (
        logs.map((entry) => <LogLine key={entry.id} entry={entry} />)
      )}
      <div ref={endRef} />
    </div>
  )
}


function FrontendTab() {
  const [logs, setLogs] = useState<LogEntry[]>([...frontendLogs])
  const endRef = useRef<HTMLDivElement>(null)
  const autoScroll = useRef(true)

  useEffect(() => {
    const listener = (entry: LogEntry) => {
      setLogs(prev => [...prev.slice(-499), entry])
    }
    frontendListeners.push(listener)
    return () => {
      frontendListeners = frontendListeners.filter(l => l !== listener)
    }
  }, [])

  useEffect(() => {
    if (autoScroll.current) {
      endRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logs])

  return (
    <div
      className="overflow-auto h-full"
      onScroll={(e) => {
        const el = e.currentTarget
        autoScroll.current = el.scrollHeight - el.scrollTop - el.clientHeight < 50
      }}
    >
      {logs.length === 0 ? (
        <div className="p-4 text-xs text-center" style={{ color: 'var(--text-muted)' }}>暂无前端日志</div>
      ) : (
        logs.map((entry) => <LogLine key={entry.id} entry={entry} />)
      )}
      <div ref={endRef} />
    </div>
  )
}


function BashTab() {
  return (
    <Suspense fallback={<div className="p-4 text-xs text-center" style={{ color: 'var(--text-muted)' }}>加载终端...</div>}>
      <RealTerminal />
    </Suspense>
  )
}


const tabs = [
  { key: 'backend' as TabKey, label: '后端日志' },
  { key: 'frontend' as TabKey, label: '前端日志' },
  { key: 'bash' as TabKey, label: '控制台' },
]


export function TerminalPanel() {
  const [activeTab, setActiveTab] = useState<TabKey>('backend')
  const [height, setHeight] = useState(200)
  const [collapsed, setCollapsed] = useState(true)
  const dragRef = useRef(false)
  const startY = useRef(0)
  const startHeight = useRef(0)

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    dragRef.current = true
    startY.current = e.clientY
    startHeight.current = height
    document.body.style.cursor = 'row-resize'
    document.body.style.userSelect = 'none'
  }, [height])

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!dragRef.current) return
      const delta = startY.current - e.clientY
      setHeight(Math.max(100, Math.min(500, startHeight.current + delta)))
    }

    const handleMouseUp = () => {
      if (dragRef.current) {
        dragRef.current = false
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
      }
    }

    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [])

  // 前端日志捕获（组件内启动，卸载时恢复）
  useEffect(() => {
    const originals = {
      log: console.log,
      warn: console.warn,
      error: console.error,
    }

    const capture = (level: string) => (...args: unknown[]) => {
      const entry: LogEntry = {
        id: nextLogId(),
        time: new Date().toLocaleTimeString('en-US', { hour12: false }),
        level,
        source: 'frontend',
        message: args.map(a => typeof a === 'string' ? a : JSON.stringify(a)).join(' '),
      }
      frontendLogs.push(entry)
      if (frontendLogs.length > 500) frontendLogs.shift()
      frontendListeners.forEach(fn => fn(entry))
      if (level === 'ERROR') originals.error(...args)
      else if (level === 'WARN') originals.warn(...args)
      else originals.log(...args)
    }

    console.log = capture('INFO')
    console.warn = capture('WARN')
    console.error = capture('ERROR')

    return () => {
      console.log = originals.log
      console.warn = originals.warn
      console.error = originals.error
    }
  }, [])

  return (
    <div
      data-terminal-panel
      className="flex flex-col border-t"
      style={{ backgroundColor: 'var(--bg-base)', borderColor: 'var(--border)', height: collapsed ? 32 : height }}
    >
      {/* 标题栏 */}
      <button
        className="flex items-center justify-between px-3 h-8 shrink-0 cursor-pointer select-none w-full text-left"
        aria-expanded={!collapsed}
        onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--bg-surface)' }}
        onMouseLeave={e => { e.currentTarget.style.backgroundColor = '' }}
        onClick={() => setCollapsed(v => !v)}
      >
        <div className="flex items-center gap-3">
          <svg
            className={`w-3 h-3 transition-transform ${collapsed ? '' : 'rotate-90'}`}
            style={{ color: 'var(--text-muted)' }}
            fill="none" stroke="currentColor" viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
          <span className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>终端</span>
          <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>v{APP_VERSION}</span>
          {!collapsed && (
            <div className="flex items-center gap-1">
              {tabs.map(tab => (
                <button
                  key={tab.key}
                  onClick={(e) => { e.stopPropagation(); setActiveTab(tab.key) }}
                  className="px-2 py-0.5 text-xs rounded transition-colors"
                    style={activeTab === tab.key ? { backgroundColor: 'var(--bg-elevated)', color: 'var(--text-primary)' } : { color: 'var(--text-muted)' }}
                    onMouseEnter={e => { if (activeTab !== tab.key) (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)' }}
                    onMouseLeave={e => { if (activeTab !== tab.key) (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)' }}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </button>

      {/* 拖拽条 */}
      {!collapsed && (
        <div
          onMouseDown={handleMouseDown}
          className="h-2 shrink-0 cursor-row-resize flex items-center justify-center group transition-colors"
        >
          <div className="w-8 h-1 rounded-full transition-colors" style={{ backgroundColor: 'var(--border)' }} />
        </div>
      )}

      {/* 内容区 */}
      {!collapsed && (
        <div className="flex-1 overflow-hidden">
          {activeTab === 'backend' && <BackendTab />}
          {activeTab === 'frontend' && <FrontendTab />}
          {activeTab === 'bash' && <BashTab />}
        </div>
      )}
    </div>
  )
}
