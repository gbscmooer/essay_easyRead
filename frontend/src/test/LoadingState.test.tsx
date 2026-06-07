import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { LoadingState } from '@/components/LoadingState'
import type { AnalysisStatus } from '@/types'

describe('LoadingState', () => {
  it('shows progress percentage when analyzing', () => {
    render(
      <LoadingState
        status={'analyzing' as AnalysisStatus}
        message="正在分析..."
        partialResult={{ title: 'Test Paper' }}
        progress={42}
      />
    )
    expect(screen.getByText('42%')).toBeInTheDocument()
    expect(screen.getByText('正在分析...')).toBeInTheDocument()
  })

  it('shows title when available', () => {
    render(
      <LoadingState
        status={'analyzing' as AnalysisStatus}
        message=""
        partialResult={{ title: 'Attention Is All You Need' }}
      />
    )
    expect(screen.getByText('Attention Is All You Need')).toBeInTheDocument()
  })

  it('shows error state when failed', () => {
    render(
      <LoadingState
        status={'failed' as AnalysisStatus}
        message="API error"
        partialResult={{}}
      />
    )
    expect(screen.getByText('解析失败')).toBeInTheDocument()
  })

  it('shows extracting step indicator', () => {
    render(
      <LoadingState
        status={'extracting' as AnalysisStatus}
        message="提取文本中..."
        partialResult={{}}
      />
    )
    expect(screen.getByText('提取文本')).toBeInTheDocument()
  })
})
