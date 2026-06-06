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
    DEBUG: 'text-gray-600',
    INFO: 'text-gray-400',
    WARNING: 'text-yellow-500',
    WARN: 'text-yellow-500',
    ERROR: 'text-red-400',
    CRITICAL: 'text-red-500',
  }[entry.level] || 'text-gray-400'

  return (
    <div className="flex gap-2 px-3 py-0.5 hover:bg-gray-800/50 text-xs font-mono leading-5">
      <span className="text-gray-600 shrink-0">{entry.time}</span>
      <span className={`${levelColor} shrink-0 w-14`}>{entry.level}</span>
      <span className="text-gray-500 shrink-0 w-16">[{entry.source}]</span>
      <span className="text-gray-300 break-all">{entry.message}</span>
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
        // ignore
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
        <div className="p-4 text-gray-600 text-xs text-center">等待日志...</div>
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
        <div className="p-4 text-gray-600 text-xs text-center">暂无前端日志</div>
      ) : (
        logs.map((entry) => <LogLine key={entry.id} entry={entry} />)
      )}
      <div ref={endRef} />
    </div>
  )
}


function BashTab() {
  return (
    <Suspense fallback={<div className="p-4 text-gray-600 text-xs text-center">加载终端...</div>}>
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
      className="flex flex-col bg-gray-950 border-t border-gray-800"
      style={{ height: collapsed ? 32 : height }}
    >
      {/* 标题栏 */}
      <div
        className="flex items-center justify-between px-3 h-8 shrink-0 cursor-pointer select-none hover:bg-gray-900/50"
        onClick={() => setCollapsed(v => !v)}
      >
        <div className="flex items-center gap-3">
          <svg
            className={`w-3 h-3 text-gray-500 transition-transform ${collapsed ? '' : 'rotate-90'}`}
            fill="none" stroke="currentColor" viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
          <span className="text-xs text-gray-500 font-medium">终端</span>
          <span className="text-[10px] text-gray-600">v{APP_VERSION}</span>
          {!collapsed && (
            <div className="flex items-center gap-1">
              {tabs.map(tab => (
                <button
                  key={tab.key}
                  onClick={(e) => { e.stopPropagation(); setActiveTab(tab.key) }}
                  className={`px-2 py-0.5 text-xs rounded transition-colors ${
                    activeTab === tab.key
                      ? 'bg-gray-800 text-gray-200'
                      : 'text-gray-500 hover:text-gray-300'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 拖拽条 */}
      {!collapsed && (
        <div
          onMouseDown={handleMouseDown}
          className="h-1 shrink-0 cursor-row-resize hover:bg-blue-500/30 transition-colors"
        />
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
