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

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex flex-col items-center justify-center h-screen bg-gray-950 text-gray-100">
          <h1 className="text-xl font-bold mb-2">页面出现错误</h1>
          <p className="text-sm text-gray-400 mb-4 max-w-md text-center">
            {this.state.error?.message || '渲染过程中发生未知错误'}
          </p>
          <button
            onClick={() => {
              this.setState({ hasError: false, error: null })
              window.location.href = '/'
            }}
            className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-500 transition-colors"
          >
            返回首页
          </button>
        </div>
      )
    }

    return this.props.children
  }
}
