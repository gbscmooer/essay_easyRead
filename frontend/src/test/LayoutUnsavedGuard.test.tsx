import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Layout } from '@/components/Layout'
import { setSettingsFormDirty } from '@/hooks/useConfig'

vi.mock('@/components/TerminalPanel', () => ({ TerminalPanel: () => null }))
vi.mock('@/components/VersionPopup', () => ({ VersionPopup: () => null }))
vi.mock('@/components/ShortcutHelp', () => ({ ShortcutHelp: () => null }))
vi.mock('@/components/ThemeProvider', () => ({ ThemeProvider: ({ children }: { children: React.ReactNode }) => children }))
vi.mock('@/hooks/useKeyboardShortcuts', () => ({
  setHelpCallback: vi.fn(),
  useGlobalShortcuts: vi.fn(),
}))

beforeEach(() => {
  setSettingsFormDirty(false)
  vi.spyOn(window, 'matchMedia').mockImplementation(() => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  } as unknown as MediaQueryList))
})

afterEach(() => {
  cleanup()
  setSettingsFormDirty(false)
  vi.restoreAllMocks()
})

describe('Layout unsaved settings guard', () => {
  it('blocks navigation until the user explicitly confirms leaving', async () => {
    setSettingsFormDirty(true)
    const router = createMemoryRouter([
      { path: '*', element: <Layout><p>当前内容</p></Layout> },
    ], { initialEntries: ['/settings'] })
    const user = userEvent.setup()
    render(<RouterProvider router={router} />)

    await user.click(screen.getByRole('link', { name: /论文库/ }))
    expect(await screen.findByRole('dialog', { name: '未保存的更改' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/settings')

    await user.click(screen.getByRole('button', { name: '留下' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(router.state.location.pathname).toBe('/settings')

    await user.click(screen.getByRole('link', { name: /论文库/ }))
    await user.click(await screen.findByRole('button', { name: '离开' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/library'))
  })
})
