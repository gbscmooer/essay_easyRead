import { useState } from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RelationsSection } from '@/features/settings/DeepResearchSettings'
import { DEFAULT_CONFIG, type AppConfig } from '@/features/settings/config'

vi.mock('@/api/client', () => ({ get: vi.fn().mockRejectedValue(new Error('offline')), post: vi.fn() }))

afterEach(cleanup)

function Form({ initial = DEFAULT_CONFIG }: { initial?: AppConfig }) {
  const [config, setConfig] = useState(() => structuredClone(initial))
  return <RelationsSection config={config} setConfig={setConfig} />
}

describe('relation preferences', () => {
  it('shows equal global preferences with no calibration or algorithm paths', async () => {
    render(<Form />)
    await waitFor(() => expect(screen.getAllByText('偏好 12.5%')).toHaveLength(8))
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.queryByText(/AUC|backend\/services|组内归一/)).not.toBeInTheDocument()
    expect(screen.getByRole('spinbutton', { name: 'Disparity 标记阈值 α' })).toHaveValue(0.05)
    expect(screen.getByText(/标记不代表关联正确率/)).toBeInTheDocument()
  })

  it('renormalizes across semantic and evidence groups together', async () => {
    render(<Form />)
    fireEvent.change(screen.getByRole('spinbutton', { name: '主题 权重数值' }), { target: { value: '0.875' } })
    await waitFor(() => expect(screen.getByText('偏好 50.0%')).toBeInTheDocument())
    expect(screen.getAllByText('偏好 7.1%')).toHaveLength(7)
    fireEvent.click(screen.getByRole('button', { name: '恢复默认权重' }))
    expect(screen.getAllByText('偏好 12.5%')).toHaveLength(8)
  })

  it('shows the equal fallback when all user weights are zero', async () => {
    const initial = structuredClone(DEFAULT_CONFIG)
    for (const name of Object.keys(initial.relations.weights) as (keyof typeof initial.relations.weights)[]) {
      initial.relations.weights[name] = 0
    }
    render(<Form initial={initial} />)
    await waitFor(() => expect(screen.getAllByText('偏好 12.5%')).toHaveLength(8))
    expect(screen.getByText(/全部权重为 0 时使用等权默认值/)).toBeInTheDocument()
  })
})
