import React from 'react'


interface Props {
  children: React.ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}


export class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('[ErrorBoundary]', error, errorInfo.componentStack)
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null })
  }

  handleGoHome = () => {
    this.setState({ hasError: false, error: null })
    window.location.href = '/'
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          className="flex flex-col items-center justify-center h-screen p-6"
          style={{ backgroundColor: 'var(--bg-base)', color: 'var(--text-primary)' }}
        >
          <div
            className="flex flex-col items-center text-center max-w-md w-full animate-reveal-up rounded-2xl px-8 py-10"
            style={{
              backgroundColor: 'var(--error-subtle)',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--seed-radius, 12px)',
            }}
          >
            <div
              className="w-16 h-16 rounded-full flex items-center justify-center mb-5"
              style={{
                backgroundColor: 'var(--bg-surface)',
                border: '1px solid var(--border)',
                boxShadow: '0 6px 18px -8px rgba(0,0,0,0.15)',
              }}
            >
              <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24" style={{ color: 'var(--error)' }}>
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
              </svg>
            </div>
            <h1 className="text-display text-xl mb-2">页面出现错误</h1>
            <p className="text-sm mb-7 max-w-sm leading-relaxed" style={{ color: 'var(--text-muted)' }}>
              {this.state.error?.message || '渲染过程中发生未知错误'}
            </p>
            <div className="flex items-center gap-3">
              <button
                onClick={this.handleRetry}
                className="px-5 py-2 text-sm font-medium rounded-lg press focus-glow"
                style={{
                  backgroundColor: 'var(--accent)',
                  color: 'var(--bg-base)',
                  transition: 'filter 0.2s var(--ease-press), box-shadow 0.2s var(--ease-press)',
                }}
                onMouseEnter={e => { e.currentTarget.style.filter = 'brightness(1.06)' }}
                onMouseLeave={e => { e.currentTarget.style.filter = 'none' }}
              >
                重试
              </button>
              <button
                onClick={this.handleGoHome}
                className="px-5 py-2 text-sm font-medium rounded-lg border press focus-glow"
                style={{
                  backgroundColor: 'var(--bg-surface)',
                  borderColor: 'var(--border)',
                  color: 'var(--text-secondary)',
                  transition: 'border-color 0.2s var(--ease-press), color 0.2s var(--ease-press)',
                }}
                onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--border-light)'; e.currentTarget.style.color = 'var(--text-primary)' }}
                onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.color = 'var(--text-secondary)' }}
              >
                返回首页
              </button>
            </div>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
