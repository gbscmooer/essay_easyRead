import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { EdgeFilter } from '@/features/research/EdgeFilter'
import type { GraphEdgeKind } from '@/types'

describe('研究图谱控件', () => {
  it('为每种选中边色配置符合可读性要求的前景色', () => {
    const selected = new Set<GraphEdgeKind>(['cites', 'related', 'bibcoupling', 'cocitation'])
    const onChange = vi.fn()
    render(<EdgeFilter selected={selected} onChange={onChange} />)

    expect(screen.getByRole('button', { name: '引用' }).style.getPropertyValue('--btn-on-accent')).toBe('#000000')
    expect(screen.getByRole('button', { name: '关联' }).style.getPropertyValue('--btn-on-accent')).toBe('#ffffff')
    expect(screen.getByRole('button', { name: '文献耦合' }).style.getPropertyValue('--btn-on-accent')).toBe('#000000')
    expect(screen.getByRole('button', { name: '共引' }).style.getPropertyValue('--btn-on-accent')).toBe('#ffffff')

    fireEvent.click(screen.getByRole('button', { name: '引用' }))
    expect(onChange).toHaveBeenCalledOnce()
    expect(onChange.mock.calls[0][0].has('cites')).toBe(false)
  })
})
