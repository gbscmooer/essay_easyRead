import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { VersionPopup } from '@/components/VersionPopup'
import { ShortcutHelp } from '@/components/ShortcutHelp'
import { ToastContainer } from '@/components/ToastContainer'
import { Tooltip } from '@/components/Tooltip'
import { dismiss, toast } from '@/hooks/useToast'

afterEach(() => {
  vi.useRealTimers()
})

describe('constructivist overlays', () => {
  it('uses the original SVG register mark and exposes a close action', () => {
    const onClose = vi.fn()
    render(<VersionPopup onClose={onClose} />)

    expect(screen.getByRole('dialog', { name: 'Easy Essay' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Easy Essay 几何标记' }))
      .toHaveAttribute('src', '/essay-register.svg')

    fireEvent.click(screen.getByRole('button', { name: '关闭版本信息' }))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('keeps shortcut help modal and keyboard close behavior', () => {
    const onClose = vi.fn()
    const { rerender } = render(<ShortcutHelp open={false} onClose={onClose} />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    rerender(<ShortcutHelp open onClose={onClose} />)
    expect(screen.getByRole('dialog', { name: '键盘快捷键' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '关闭快捷键帮助' })).toHaveFocus()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('renders and dismisses a registered toast', () => {
    render(<ToastContainer />)
    let toastId = 0
    act(() => {
      toastId = toast({ type: 'info', message: '索引已更新', duration: 0 })
    })

    expect(screen.getByRole('status')).toHaveTextContent('索引已更新')
    fireEvent.click(screen.getByRole('button', { name: '关闭通知' }))
    expect(screen.queryByText('索引已更新')).not.toBeInTheDocument()

    act(() => dismiss(toastId))
  })

  it('preserves delayed tooltip interaction in the portal', () => {
    vi.useFakeTimers()
    render(
      <Tooltip content="图谱质量说明" delay={120}>
        <button type="button">质量</button>
      </Tooltip>,
    )

    fireEvent.mouseEnter(screen.getByRole('button', { name: '质量' }))
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()

    act(() => vi.advanceTimersByTime(120))
    expect(screen.getByRole('tooltip')).toHaveTextContent('图谱质量说明')

    fireEvent.mouseLeave(screen.getByRole('button', { name: '质量' }))
    act(() => vi.advanceTimersByTime(150))
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  })
})
