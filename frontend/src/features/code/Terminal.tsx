import { useEffect, useRef } from 'react'
import { Terminal as XTerminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { useThemeMode } from '@/theme/useTheme'


interface TerminalProps {
  cwd?: string
}

const MAX_RETRIES = 5
const PING_INTERVAL = 30000

/** 按明暗模式返回 xterm 调色板 */
function termTheme(mode: 'light' | 'dark') {
  if (mode === 'light') {
    return {
      background: '#f6f8fa', foreground: '#1f2328', cursor: '#1f2328', cursorAccent: '#f6f8fa',
      selectionBackground: '#b6d3fb',
      black: '#24292f', red: '#cf222e', green: '#116329', yellow: '#7d4e00', blue: '#0969da',
      magenta: '#8250df', cyan: '#1b7c83', white: '#6e7781',
      brightBlack: '#57606a', brightRed: '#a40e26', brightGreen: '#1a7f37', brightYellow: '#633c01',
      brightBlue: '#218bff', brightMagenta: '#a475f9', brightCyan: '#3192aa', brightWhite: '#8c959f',
    }
  }
  return {
    background: '#1e1e1e', foreground: '#cccccc', cursor: '#ffffff', cursorAccent: '#1e1e1e',
    selectionBackground: '#264f78',
    black: '#1e1e1e', red: '#f44747', green: '#6a9955', yellow: '#d7ba7d', blue: '#569cd6',
    magenta: '#c586c0', cyan: '#4ec9b0', white: '#d4d4d4',
    brightBlack: '#808080', brightRed: '#f44747', brightGreen: '#6a9955', brightYellow: '#d7ba7d',
    brightBlue: '#569cd6', brightMagenta: '#c586c0', brightCyan: '#4ec9b0', brightWhite: '#d4d4d4',
  }
}

export function Terminal({ cwd }: TerminalProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<XTerminal | null>(null)
  const mode = useThemeMode()
  const modeRef = useRef(mode)
  modeRef.current = mode

  // 主题明暗切换时更新已存在终端的调色板
  useEffect(() => {
    if (termRef.current) {
      termRef.current.options.theme = termTheme(mode)
    }
  }, [mode])

  useEffect(() => {
    if (!containerRef.current) return
    const container = containerRef.current

    let ws: WebSocket | null = null
    let term: XTerminal | null = null
    let fitAddon: FitAddon | null = null
    let pingTimer: ReturnType<typeof setInterval> | null = null
    let retryTimer: ReturnType<typeof setTimeout> | null = null
    let retryCount = 0
    let disposed = false
    let onDataDisposable: { dispose: () => void } | null = null

    function createTerminal() {
      term = new XTerminal({
        theme: termTheme(modeRef.current),
        fontSize: 13,
        fontFamily: 'Menlo, Monaco, "Courier New", monospace',
        cursorBlink: true,
        allowTransparency: true,
      })
      termRef.current = term
      fitAddon = new FitAddon()
      term.loadAddon(fitAddon)
      term.open(container)
      fitAddon.fit()
    }

    function connect() {
      if (disposed) return

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const host = window.location.host
      const wsUrl = `${protocol}//${host}/api/terminal/ws${cwd ? `?cwd=${encodeURIComponent(cwd)}` : ''}`

      ws = new WebSocket(wsUrl)

      ws.onopen = () => {
        retryCount = 0
        const dims = fitAddon?.proposeDimensions()
        if (dims && ws) {
          ws.send(`\x1b[8;${dims.rows};${dims.cols}`)
        }
        if (pingTimer) clearInterval(pingTimer)
        pingTimer = setInterval(() => {
          if (ws?.readyState === WebSocket.OPEN) {
            ws.send('ping')
          }
        }, PING_INTERVAL)
      }

      ws.onmessage = (event) => {
        if (disposed) return
        if (event.data === 'pong') return
        if (event.data instanceof Blob) {
          event.data.text().then(text => { if (!disposed && term) term.write(text) })
        } else {
          if (term) term.write(event.data)
        }
      }

      ws.onclose = () => {
        if (pingTimer) { clearInterval(pingTimer); pingTimer = null }
        if (disposed) return
        scheduleReconnect()
      }

      ws.onerror = () => {
        if (pingTimer) { clearInterval(pingTimer); pingTimer = null }
      }

      if (onDataDisposable) { onDataDisposable.dispose(); onDataDisposable = null }
      if (term) {
        onDataDisposable = term.onData((data) => {
          if (ws?.readyState === WebSocket.OPEN) {
            ws.send(data)
          }
        })
      }
    }

    function scheduleReconnect() {
      if (disposed) return
      if (retryCount >= MAX_RETRIES) {
        if (term) term.write('\r\n\x1b[31m[连接失败，请刷新页面]\x1b[0m\r\n')
        return
      }
      const delay = Math.min(1000 * Math.pow(2, retryCount), 16000)
      retryCount++
      if (term) term.write(`\r\n\x1b[33m[连接断开，${delay / 1000}秒后重新连接...]\x1b[0m\r\n`)
      retryTimer = setTimeout(() => {
        if (disposed) return
        if (term) term.write('\r\n\x1b[33m[正在重新连接...]\x1b[0m\r\n')
        connect()
      }, delay)
    }

    createTerminal()
    connect()

    const handleResize = () => {
      fitAddon?.fit()
      const dims = fitAddon?.proposeDimensions()
      if (dims && ws?.readyState === WebSocket.OPEN) {
        ws.send(`\x1b[8;${dims.rows};${dims.cols}`)
      }
    }
    const observer = new ResizeObserver(handleResize)
    observer.observe(container)

    return () => {
      disposed = true
      observer.disconnect()
      if (pingTimer) clearInterval(pingTimer)
      if (retryTimer) clearTimeout(retryTimer)
      if (onDataDisposable) { onDataDisposable.dispose(); onDataDisposable = null }
      if (ws) { try { ws.close() } catch {} }
      if (term) { try { term.dispose() } catch {} }
      termRef.current = null
      ws = null
      term = null
      fitAddon = null
    }
  }, [cwd])

  return (
    <div
      ref={containerRef}
      className="h-full w-full"
      style={{ padding: '4px', backgroundColor: 'var(--code-bg)' }}
    />
  )
}
