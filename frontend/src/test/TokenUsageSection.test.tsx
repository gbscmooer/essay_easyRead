/**
 * TokenUsageSection「查看详细信息」明细表测试：
 * 展开 → 拉取逐次调用记录（时间倒序分页）；加载更多追加下一页；
 * 无数据时不出入口。
 */
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const settingsApi = vi.hoisted(() => ({
  getTokenUsageSummary: vi.fn(),
  getTokenUsageRecords: vi.fn(),
}))

vi.mock('@/api/settings', () => settingsApi)
vi.mock('recharts', () => ({
  Area: () => null,
  AreaChart: () => null,
  CartesianGrid: () => null,
  Cell: () => null,
  Legend: () => null,
  Pie: () => null,
  PieChart: () => null,
  ResponsiveContainer: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Tooltip: () => null,
  XAxis: () => null,
  YAxis: () => null,
}))

import TokenUsageSection from '@/features/settings/TokenUsageSection'

function summary(overrides: Record<string, unknown> = {}) {
  return {
    since: null,
    days: 30,
    totals: {
      calls: 3, input_tokens: 300, output_tokens: 120, cached_input_tokens: 50,
      cache_write_tokens: 0, reasoning_tokens: 10, total_tokens: 420,
    },
    by_category: [],
    by_day: [],
    models: [],
    ...overrides,
  }
}

function recordPage(offset: number, ids: number[], total: number) {
  return {
    since: null, days: 30, total, limit: 2, offset,
    records: ids.map(id => ({
      id,
      created_at: '2026-09-16T10:00:00+00:00',
      category: 'exploration',
      stage: 'report_extract',
      run_id: '3',
      model: 'glm-4.6',
      api_format: 'openai_chat',
      input_tokens: 10 * id,
      output_tokens: id,
      cached_input_tokens: 5,
      cache_write_tokens: 0,
      reasoning_tokens: 0,
      total_tokens: 10 * id + id,
    })),
  }
}

beforeEach(() => {
  settingsApi.getTokenUsageSummary.mockResolvedValue(summary())
  settingsApi.getTokenUsageRecords.mockImplementation((_days: number, _limit: number, offset: number) =>
    Promise.resolve(offset === 0 ? recordPage(0, [1, 2], 3) : recordPage(2, [3], 3)))
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('TokenUsageSection 明细表', () => {
  it('展开后展示逐次调用明细（无成本/用时列）', async () => {
    const user = userEvent.setup()
    render(<TokenUsageSection />)

    await screen.findByText('420') // 汇总卡片已加载
    expect(screen.queryByText('业务类目')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '查看详细信息' }))

    expect((await screen.findAllByText('glm-4.6')).length).toBeGreaterThanOrEqual(2)
    expect(settingsApi.getTokenUsageRecords).toHaveBeenCalledWith(30, 50, 0, expect.anything())
    // 明细表列不含成本与用时
    expect(screen.getByText('业务类目')).toBeInTheDocument()
    expect(screen.queryByText('总成本')).not.toBeInTheDocument()
    expect(screen.queryByText('用时/首字')).not.toBeInTheDocument()
    expect(screen.getByText(/已显示 2 \/ 共 3 次成功调用/)).toBeInTheDocument()
  })

  it('加载更多按当前 offset 追加下一页', async () => {
    const user = userEvent.setup()
    render(<TokenUsageSection />)
    await screen.findByText('420')

    await user.click(screen.getByRole('button', { name: '查看详细信息' }))
    await screen.findAllByText('glm-4.6')

    await user.click(screen.getByRole('button', { name: '加载更多' }))

    await waitFor(() => {
      expect(screen.getByText(/已显示 3 \/ 共 3 次成功调用/)).toBeInTheDocument()
    })
    expect(settingsApi.getTokenUsageRecords).toHaveBeenLastCalledWith(30, 50, 2, undefined)
    expect(screen.queryByRole('button', { name: '加载更多' })).not.toBeInTheDocument()
  })

  it('无数据时不显示明细入口', async () => {
    settingsApi.getTokenUsageSummary.mockResolvedValue(summary({
      totals: {
        calls: 0, input_tokens: 0, output_tokens: 0, cached_input_tokens: 0,
        cache_write_tokens: 0, reasoning_tokens: 0, total_tokens: 0,
      },
    }))
    render(<TokenUsageSection />)

    await screen.findByText(/还没有记录到 token 消耗/)
    expect(screen.queryByRole('button', { name: '查看详细信息' })).not.toBeInTheDocument()
  })

  it('缓存命中率以输入 token 总数为分母', async () => {
    settingsApi.getTokenUsageSummary.mockResolvedValue(summary({
      totals: {
        calls: 1, input_tokens: 200, output_tokens: 100, cached_input_tokens: 50,
        cache_write_tokens: 0, reasoning_tokens: 0, total_tokens: 300,
      },
    }))
    render(<TokenUsageSection />)

    expect(await screen.findByText('命中率 25%')).toBeInTheDocument()
  })
})
