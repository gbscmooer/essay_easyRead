import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { BUILTIN_THEMES } from '@/theme/themes'

vi.mock('@/api/client', () => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@/components/TerminalPanel', () => ({ TerminalPanel: () => null }))
vi.mock('@/components/ThemeProvider', () => ({ ThemeProvider: ({ children }: { children: React.ReactNode }) => children }))
vi.mock('@/hooks/useKeyboardShortcuts', () => ({ setHelpCallback: vi.fn(), useGlobalShortcuts: vi.fn() }))
import { get, post } from '@/api/client'
import { saveTheme, refreshConfig, useUIConfig } from '@/hooks/useConfig'
import { Layout } from '@/components/Layout'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('顶栏持久化自定义深色主题，明暗切换可以回到原自定义主题', async () => {
  const custom = { ...BUILTIN_THEMES.find(theme => theme.id === 'dark')!, id: 'mine', name: '我的深色', builtin: false }
  const config = { ui: { theme: 'custom:mine', custom_themes: [custom] } }
  vi.mocked(get).mockImplementation(async () => structuredClone(config))
  vi.mocked(post).mockImplementation(async (_path, data) => {
    config.ui.theme = (data as { theme: string }).theme
    return { ui: structuredClone(config.ui) }
  })
  vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() } as unknown as MediaQueryList)
  const router = createMemoryRouter([{ path: '*', element: <Layout>test</Layout> }], { initialEntries: ['/library'] })
  render(<RouterProvider router={router} />)
  await screen.findByTitle('切换至明亮模式')
  fireEvent.click(screen.getByTitle('切换至明亮模式'))
  await waitFor(() => expect(config.ui.theme).toBe('light'))
  fireEvent.click(await screen.findByTitle('切换至深色模式'))
  await waitFor(() => expect(config.ui.theme).toBe('custom:mine'))
  expect(post).toHaveBeenLastCalledWith('/analysis/config/theme', { theme: 'custom:mine' })
  expect(localStorage.getItem('essay_theme')).toBe('custom:mine')
  act(() => refreshConfig())
  await screen.findByTitle('切换至明亮模式')
  const hook = renderHook(() => useUIConfig())
  await waitFor(() => expect(hook.result.current.theme).toBe('custom:mine'))
  await act(async () => { await Promise.all([saveTheme('dark'), saveTheme('light')]) })
  expect(config.ui.theme).toBe('light')
  expect(hook.result.current.theme).toBe('light')
})
