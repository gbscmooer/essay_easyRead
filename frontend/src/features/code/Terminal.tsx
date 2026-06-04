import { useEffect, useRef } from 'react'
import { Terminal as XTerminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'


interface TerminalProps {
  cwd?: string
}


export function Terminal({ cwd }: TerminalProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const terminalRef = useRef<XTerminal | null>(null)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    // 创建 xterm 实例
    const term = new XTerminal({
      theme: {
        background: '#1e1e1e',
        foreground: '#cccccc',
        cursor: '#ffffff',
        cursorAccent: '#1e1e1e',
        selectionBackground: '#264f78',
        black: '#1e1e1e',
        red: '#f44747',
        green: '#6a9955',
        yellow: '#d7ba7d',
        blue: '#569cd6',
        magenta: '#c586c0',
        cyan: '#4ec9b0',
        white: '#d4d4d4',
        brightBlack: '#808080',
        brightRed: '#f44747',
        brightGreen: '#6a9955',
        brightYellow: '#d7ba7d',
        brightBlue: '#569cd6',
        brightMagenta: '#c586c0',
        brightCyan: '#4ec9b0',
        brightWhite: '#d4d4d4',
      },
      fontSize: 13,
      fontFamily: 'Menlo, Monaco, "Courier New", monospace',
      cursorBlink: true,
      allowTransparency: true,
    })

    const fitAddon = new FitAddon()
    term.loadAddon(fitAddon)
    term.open(containerRef.current)
    fitAddon.fit()
    terminalRef.current = term

    // WebSocket 连接
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    const wsUrl = `${protocol}//${host}/api/terminal/ws${cwd ? `?cwd=${encodeURIComponent(cwd)}` : ''}`

    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      // 发送初始大小
      const dims = fitAddon.proposeDimensions()
      if (dims) {
        ws.send(`\x1b[8;${dims.rows};${dims.cols}`)
      }
    }

    ws.onmessage = (event) => {
      if (event.data instanceof Blob) {
        event.data.text().then(text => term.write(text))
      } else {
        term.write(event.data)
      }
    }

    ws.onclose = () => {
      term.write('\r\n\x1b[33m[连接已断开]\x1b[0m\r\n')
    }

    ws.onerror = () => {
      term.write('\r\n\x1b[31m[连接失败]\x1b[0m\r\n')
    }

    // 用户输入发送到 WebSocket
    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(data)
      }
    })

    // 窗口大小变化时调整
    const handleResize = () => {
      fitAddon.fit()
      const dims = fitAddon.proposeDimensions()
      if (dims && ws.readyState === WebSocket.OPEN) {
        ws.send(`\x1b[8;${dims.rows};${dims.cols}`)
      }
    }

    const observer = new ResizeObserver(handleResize)
    observer.observe(containerRef.current)

    return () => {
      observer.disconnect()
      ws.close()
      term.dispose()
      terminalRef.current = null
      wsRef.current = null
    }
  }, [cwd])

  return (
    <div
      ref={containerRef}
      className="h-full w-full bg-[#1e1e1e]"
      style={{ padding: '4px' }}
    />
  )
}
