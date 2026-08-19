import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  POSITION_LIMIT,
  forgetReadingPage,
  loadLayout,
  loadReadingPage,
  loadZoom,
  saveLayout,
  saveReadingPage,
  saveZoom,
} from '@/utils/readerPrefs'

const store = new Map<string, string>()
const storageMock: Storage = {
  get length() { return store.size },
  clear: () => store.clear(),
  getItem: key => store.get(key) ?? null,
  key: index => Array.from(store.keys())[index] ?? null,
  removeItem: key => { store.delete(key) },
  setItem: (key, value) => { store.set(key, String(value)) },
}

beforeEach(() => {
  store.clear()
  Object.defineProperty(window, 'localStorage', { configurable: true, value: storageMock })
})

afterEach(() => {
  vi.restoreAllMocks()
})

const LAYOUT_FALLBACK = { panelPct: 35, panelOpen: true }

describe('面板布局', () => {
  it('没有存档时返回默认值', () => {
    expect(loadLayout(LAYOUT_FALLBACK, 20, 60)).toEqual(LAYOUT_FALLBACK)
  })

  it('存档能读回来', () => {
    saveLayout({ panelPct: 48, panelOpen: false })
    expect(loadLayout(LAYOUT_FALLBACK, 20, 60)).toEqual({ panelPct: 48, panelOpen: false })
  })

  it('越界宽度被夹回当前版本的取值范围', () => {
    saveLayout({ panelPct: 95, panelOpen: true })
    expect(loadLayout(LAYOUT_FALLBACK, 20, 60).panelPct).toBe(60)
    saveLayout({ panelPct: 2, panelOpen: true })
    expect(loadLayout(LAYOUT_FALLBACK, 20, 60).panelPct).toBe(20)
  })

  it('损坏的存档回落到默认值而不是抛错', () => {
    store.set('essay_reader_layout', '{ not json')
    expect(loadLayout(LAYOUT_FALLBACK, 20, 60)).toEqual(LAYOUT_FALLBACK)
    store.set('essay_reader_layout', '{"panelPct":"wide","panelOpen":"yes"}')
    expect(loadLayout(LAYOUT_FALLBACK, 20, 60)).toEqual(LAYOUT_FALLBACK)
  })
})

describe('缩放偏好', () => {
  const ZOOM_FALLBACK = { scale: 1, fitMode: 'none' as const }

  it('存档能读回来', () => {
    saveZoom({ scale: 1.4, fitMode: 'width' })
    expect(loadZoom(ZOOM_FALLBACK, 0.2, 5)).toEqual({ scale: 1.4, fitMode: 'width' })
  })

  it('未知的适配模式回落到默认值', () => {
    store.set('essay_reader_zoom', '{"scale":1.2,"fitMode":"cover"}')
    expect(loadZoom(ZOOM_FALLBACK, 0.2, 5)).toEqual({ scale: 1.2, fitMode: 'none' })
  })

  it('越界缩放被夹住', () => {
    saveZoom({ scale: 99, fitMode: 'none' })
    expect(loadZoom(ZOOM_FALLBACK, 0.2, 5).scale).toBe(5)
  })
})

describe('阅读位置', () => {
  it('按论文分别记忆', () => {
    saveReadingPage(7, 12)
    saveReadingPage(8, 3)
    expect(loadReadingPage(7)).toBe(12)
    expect(loadReadingPage(8)).toBe(3)
    expect(loadReadingPage(9)).toBeNull()
  })

  it('忽略非法页码', () => {
    saveReadingPage(1, 0)
    saveReadingPage(2, Number.NaN)
    expect(loadReadingPage(1)).toBeNull()
    expect(loadReadingPage(2)).toBeNull()
  })

  it('超过上限时淘汰最旧的记录，保留最近读的', () => {
    let now = 1_700_000_000_000
    vi.spyOn(Date, 'now').mockImplementation(() => (now += 1000))

    for (let id = 1; id <= POSITION_LIMIT + 5; id += 1) saveReadingPage(id, id)

    expect(loadReadingPage(1)).toBeNull()
    expect(loadReadingPage(POSITION_LIMIT + 5)).toBe(POSITION_LIMIT + 5)
    expect(Object.keys(JSON.parse(store.get('essay_reader_positions')!))).toHaveLength(POSITION_LIMIT)
  })

  it('删除论文后不再返回它的页码', () => {
    saveReadingPage(42, 9)
    forgetReadingPage(42)
    expect(loadReadingPage(42)).toBeNull()
  })
})

describe('storage 不可用', () => {
  it('读写都静默回落，不向上抛', () => {
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() { throw new Error('storage disabled') },
    })

    expect(() => saveReadingPage(1, 2)).not.toThrow()
    expect(() => saveLayout({ panelPct: 30, panelOpen: true })).not.toThrow()
    expect(loadLayout(LAYOUT_FALLBACK, 20, 60)).toEqual(LAYOUT_FALLBACK)
    expect(loadReadingPage(1)).toBeNull()
  })
})
