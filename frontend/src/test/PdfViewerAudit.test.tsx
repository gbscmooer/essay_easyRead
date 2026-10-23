import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({ renderPage: new Map<number, () => void>(), scales: new Map<number, number>() }))
vi.mock('react-pdf', async () => {
  const React = await import('react')
  return {
    pdfjs: { GlobalWorkerOptions: {} },
    Document: ({ children, onLoadSuccess }: { children: React.ReactNode; onLoadSuccess: (data: { numPages: number }) => void }) => {
      React.useEffect(() => { const timer = setTimeout(() => onLoadSuccess({ numPages: 2 }), 0); return () => clearTimeout(timer) }, [])
      return <div>{children}</div>
    },
    Page: ({ pageNumber, scale, onLoadSuccess, onRenderSuccess }: { pageNumber: number; scale: number; onLoadSuccess: (page: object) => void; onRenderSuccess: () => void }) => {
      state.scales.set(pageNumber, scale)
      state.renderPage.set(pageNumber, onRenderSuccess)
      React.useEffect(() => onLoadSuccess({ getViewport: () => ({ width: pageNumber === 1 ? 500 : 1000, height: 1000 }) }), [pageNumber])
      return <div data-testid={`pdf-page-${pageNumber}`} style={{ width: (pageNumber === 1 ? 500 : 1000) * scale, height: 1000 * scale }} />
    },
  }
})
vi.mock('@/hooks/useConfig', () => ({ useUIConfig: () => ({ zoom_step: .2, zoom_min: .4, zoom_max: 3, pinch_zoom_step: .02 }) }))
vi.mock('@/api/annotations', () => ({ listAnnotations: vi.fn(async () => []), createAnnotation: vi.fn(), deleteAnnotation: vi.fn(), deleteAnnotationGroup: vi.fn() }))
vi.mock('@/components/SelectionPopup', () => ({ SelectionPopup: () => null }))
vi.mock('@/features/viewer/AnnotationLayer', () => ({ AnnotationLayer: () => null }))
import { PdfViewer, type PdfViewerHandle } from '@/features/viewer/PdfViewer'
import { saveZoom, saveReadingPage } from '@/utils/readerPrefs'

beforeEach(() => {
  vi.useFakeTimers()
  localStorage.clear()
  state.renderPage.clear(); state.scales.clear()
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} })
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 800 })
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => 600 })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get() {
    const number = Number((this as HTMLElement).dataset.page)
    return number ? 1000 * (state.scales.get(number) || 1) : 600
  } })
  Object.defineProperty(HTMLElement.prototype, 'scrollTo', { configurable: true, value: vi.fn() })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const number = Number(this.dataset.page)
    const scale = state.scales.get(number) || 1
    const scroll = number ? this.parentElement?.parentElement?.parentElement?.scrollTop || 0 : 0
    const top = -scroll
    const height = number ? 1000 * scale : 600
    const width = number ? (number === 1 ? 500 : 1000) * scale : 800
    return { x: 0, y: top, left: 0, top, bottom: top + height, right: width, width, height, toJSON: () => ({}) }
  })
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('PDF audit', () => {
  it('引用页码优先于上次阅读页码，等待文档加载和目标页渲染', async () => {
    saveReadingPage(12, 1)
    render(<PdfViewer pdfUrl="/stub.pdf" paperId={12} citationPage={2} onPageChange={() => {}} />)
    await act(async () => vi.advanceTimersByTime(0))
    expect(HTMLElement.prototype.scrollTo).not.toHaveBeenCalled()
    act(() => state.renderPage.get(2)?.())
    await act(async () => vi.advanceTimersByTime(100))
    expect(HTMLElement.prototype.scrollTo).toHaveBeenCalled()
    expect(screen.getByLabelText('跳转到页码')).toHaveAttribute('placeholder', '2')
  })

  it('缩放达到上限不使已渲染的章节定位失效', async () => {
    saveZoom({ scale: 3, fitMode: 'none' })
    const ref = createRef<PdfViewerHandle>()
    render(<PdfViewer ref={ref} pdfUrl="/stub.pdf" activePage={1} headings={[{ page: 2, text: 'Methods', y_percent: .5 }]} onPageChange={() => {}} />)
    await act(async () => vi.advanceTimersByTime(0))
    expect(state.scales.get(2)).toBe(3)
    act(() => state.renderPage.get(2)?.())
    fireEvent.click(screen.getByRole('button', { name: '放大' }))
    expect(state.scales.get(2)).toBe(3)
    act(() => ref.current?.jumpToSection(2, 'Methods'))
    await act(async () => vi.advanceTimersByTime(0))
    expect(HTMLElement.prototype.scrollTo).toHaveBeenCalledWith({ top: 1380, behavior: 'smooth' })
  })

  it('章节定位等待目标页真实渲染，500ms时不会抢先跳页首', async () => {
    const ref = createRef<PdfViewerHandle>()
    render(<PdfViewer ref={ref} pdfUrl="/stub.pdf" activePage={1} headings={[{ page: 2, text: 'Methods', y_percent: .5 }]} onPageChange={() => {}} />)
    await act(async () => vi.advanceTimersByTime(0))
    act(() => ref.current?.jumpToSection(2, 'Methods'))
    await act(async () => vi.advanceTimersByTime(600))
    expect(HTMLElement.prototype.scrollTo).not.toHaveBeenCalled()
    act(() => state.renderPage.get(2)?.())
    await act(async () => vi.advanceTimersByTime(100))
    expect(HTMLElement.prototype.scrollTo).toHaveBeenCalledWith({ top: 380, behavior: 'smooth' })
  })

  it('混合页幅适宽按当前页计算', async () => {
    render(<PdfViewer pdfUrl="/stub.pdf" activePage={2} onPageChange={() => {}} />)
    await act(async () => vi.advanceTimersByTime(0))
    fireEvent.click(screen.getByTitle('适应宽度（随面板宽度自动重算）'))
    expect(state.scales.get(2)).toBeCloseTo((800 - 56) / 1000)
  })

  it('手动缩放保留视口里的相同内容位置', async () => {
    const view = render(<PdfViewer pdfUrl="/stub.pdf" activePage={1} onPageChange={() => {}} />)
    await act(async () => vi.advanceTimersByTime(0))
    act(() => { state.renderPage.get(1)?.(); state.renderPage.get(2)?.() })
    const page = view.container.querySelector('[data-page="1"]')!
    const scroll = page.closest('.overflow-auto') as HTMLElement || page.parentElement!.parentElement!.parentElement!
    fireEvent.click(screen.getByRole('button', { name: '放大' }))
    act(() => state.renderPage.get(1)?.())
    expect(state.scales.get(1)).toBeCloseTo(1.2)
    // Center at 300px in a 1000px page becomes 360px: viewport compensation is 60px.
    expect(scroll.scrollTop).toBeCloseTo(60)
  })
})
