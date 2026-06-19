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
        <div className="flex flex-col items-center justify-center h-screen" style={{ backgroundColor: 'var(--bg-base)', color: 'var(--text-primary)' }}>
          <div className="w-16 h-16 rounded-full flex items-center justify-center mb-4" style={{ backgroundColor: 'var(--error-subtle)' }}>
            <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24" style={{ color: 'var(--error)' }}>
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
            </svg>
          </div>
          <h1 className="text-display text-xl mb-2">页面出现错误</h1>
          <p className="text-sm mb-6 max-w-md text-center" style={{ color: 'var(--text-muted)' }}>
            {this.state.error?.message || '渲染过程中发生未知错误'}
          </p>
          <div className="flex items-center gap-3">
            <button
              onClick={this.handleRetry}
              className="px-4 py-2 text-sm rounded-lg transition-colors"
              style={{ backgroundColor: 'var(--accent)', color: 'var(--bg-base)' }}
            >
              重试
            </button>
            <button
              onClick={this.handleGoHome}
              className="px-4 py-2 text-sm rounded-lg border transition-colors"
              style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
            >
              返回首页
            </button>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
