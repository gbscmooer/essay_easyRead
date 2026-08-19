import { afterEach, describe, expect, it, vi } from 'vitest'
import { downloadText, safeFilename } from '@/utils/download'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('safeFilename', () => {
  it('去掉文件系统保留字符', () => {
    expect(safeFilename('A/B:C*D?E"F<G>H|I')).toBe('ABCDEFGHI')
  })

  it('保留中文与常见标点', () => {
    expect(safeFilename('注意力机制 (Transformer) — 综述')).toBe('注意力机制 (Transformer) — 综述')
  })

  it('空标题回落到默认名', () => {
    expect(safeFilename('')).toBe('paper')
    expect(safeFilename(null)).toBe('paper')
    expect(safeFilename('   ')).toBe('paper')
  })

  it('过长标题被截断', () => {
    expect(safeFilename('x'.repeat(200))).toHaveLength(80)
  })
})

describe('downloadText', () => {
  it('用完即释放 object URL，不把内容漏在内存里', () => {
    const createObjectURL = vi.fn(() => 'blob:fake')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    downloadText('paper.md', '# 标题', 'text/markdown')

    expect(createObjectURL).toHaveBeenCalledTimes(1)
    expect(click).toHaveBeenCalledTimes(1)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:fake')
  })
})
