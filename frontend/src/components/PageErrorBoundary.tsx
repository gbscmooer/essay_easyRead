import React from 'react'

interface Props {
  children: React.ReactNode
  pageName: string
}

interface State {
  hasError: boolean
  error: Error | null
}

export class PageErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error(`[${this.props.pageName}] Error:`, error, errorInfo)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex flex-col items-center justify-center h-full p-8" style={{ color: 'var(--text-primary)' }}>
          <div
            className="flex flex-col items-center text-center max-w-sm w-full animate-reveal-up px-7 py-9"
            style={{
              backgroundColor: 'var(--error-subtle)',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--seed-radius, 12px)',
            }}
          >
            <div
              className="w-14 h-14 flex items-center justify-center mb-4"
              style={{
                backgroundColor: 'var(--bg-surface)',
                border: '1px solid var(--border)',
                boxShadow: '0 6px 18px -8px rgba(0,0,0,0.15)',
              }}
            >
              <svg className="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24" style={{ color: 'var(--error)' }}>
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
              </svg>
            </div>
            <h2 className="text-heading text-xl mb-2">页面加载出错</h2>
            <p className="text-sm mb-6 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
              {this.state.error?.message || '未知错误'}
            </p>
            <button
              onClick={() => this.setState({ hasError: false, error: null })}
              className="px-5 py-2 text-sm font-medium press focus-glow"
              style={{
                backgroundColor: 'var(--accent)',
                color: 'var(--bg-base)',
                transition: 'filter 0.2s var(--ease-press)',
              }}
              onMouseEnter={e => { e.currentTarget.style.filter = 'brightness(1.06)' }}
              onMouseLeave={e => { e.currentTarget.style.filter = 'none' }}
            >
              重试
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
