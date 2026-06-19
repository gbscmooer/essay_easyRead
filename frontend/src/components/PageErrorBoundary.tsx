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
          <h2 className="text-heading text-xl mb-4">页面加载出错</h2>
          <p className="text-sm mb-4" style={{ color: 'var(--text-muted)' }}>
            {this.state.error?.message || '未知错误'}
          </p>
          <button
            onClick={() => this.setState({ hasError: false, error: null })}
            className="px-4 py-2 rounded-lg text-sm"
            style={{ backgroundColor: 'var(--accent)', color: 'var(--bg-base)' }}
          >
            重试
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
