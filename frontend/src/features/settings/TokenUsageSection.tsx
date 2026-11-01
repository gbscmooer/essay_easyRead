/**
 * TokenUsageSection — LLM token 消耗统计
 *
 * 读取 /analysis/token-usage：合计卡片（总消耗/输入/输出/缓存命中/调用次数）+
 * 按业务类目的占比饼图（解读文献、翻译/总结、文献研究…）+ 按日消耗趋势。
 * 时间范围切换：近1天 / 近7天 / 近30天 / 全部。「查看详细信息」展开逐次调用
 * 明细表（/analysis/token-usage/records，时间倒序分页）。后端未上报 usage 的调用
 * （个别流式网关）不计入，不做估算。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Area, AreaChart, CartesianGrid, Cell, Legend, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { getTokenUsageRecords, getTokenUsageSummary } from '@/api/settings'
import { parseServerDate } from '@/utils/datetime'
import type {
  TokenUsageCategoryRow, TokenUsageDayRow, TokenUsageRecord, TokenUsageSummary,
} from '@/types'
import { LoadingSpinner } from '@/ds-entry'

const CATEGORY_LABELS: Record<string, string> = {
  paper_analysis: '解读文献',
  text_action: '翻译/总结',
  exploration: '文献研究',
  code_analysis: '代码分析',
  workspace_research: '工作区研究',
  connection_test: '连接测试',
  other: '其他',
}

// 分类色取自版面配色（朱砂、靛青、石绿、赭石、紫褐、青灰），与纸面协调
const CATEGORY_COLORS: Record<string, string> = {
  paper_analysis: '#3a5679',
  text_action: '#3d7457',
  exploration: '#b5361c',
  code_analysis: '#a8741b',
  workspace_research: '#6b4c7a',
  connection_test: '#8d877b',
  other: '#9b3d5a',
}

const RANGES = [
  { key: 'day', label: '近1天', days: 1 },
  { key: 'week', label: '近7天', days: 7 },
  { key: 'month', label: '近30天', days: 30 },
  { key: 'all', label: '全部', days: 0 },
] as const
type RangeKey = (typeof RANGES)[number]['key']

function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category
}

function categoryColor(category: string): string {
  return CATEGORY_COLORS[category] ?? '#8d877b'
}

function formatTokens(value: number): string {
  if (!Number.isFinite(value)) return '0'
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}k`
  return String(value)
}

const PAGE_SIZE = 50

function formatRecordTime(iso: string): string {
  const d = parseServerDate(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function isValidSummary(value: unknown): value is TokenUsageSummary {
  if (!value || typeof value !== 'object') return false
  const summary = value as TokenUsageSummary
  return !!summary.totals && Array.isArray(summary.by_category) && Array.isArray(summary.by_day)
}

interface PieDatum {
  name: string
  value: number
  category: string
}

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="set-usage-card">
      <p className="set-usage-card__label">{label}</p>
      <p className="set-usage-card__value">{value}</p>
      {sub && <p className="set-usage-card__sub">{sub}</p>}
    </div>
  )
}

export default function TokenUsageSection() {
  const [range, setRange] = useState<RangeKey>('month')
  const [summary, setSummary] = useState<TokenUsageSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [records, setRecords] = useState<TokenUsageRecord[]>([])
  const [recordsTotal, setRecordsTotal] = useState(0)
  const [recordsLoading, setRecordsLoading] = useState(false)
  const [recordsFailed, setRecordsFailed] = useState(false)
  const recordsOffsetRef = useRef(0)

  const days = RANGES.find(item => item.key === range)?.days ?? 30

  const load = useCallback((signal?: AbortSignal) => {
    setLoading(true)
    setFailed(false)
    return getTokenUsageSummary(days, signal)
      .then(data => {
        if (isValidSummary(data)) setSummary(data)
        else setSummary(null)
      })
      .catch((error: unknown) => {
        if (signal?.aborted) return
        setSummary(null)
        setFailed(error instanceof Error)
      })
      .finally(() => {
        if (!signal?.aborted) setLoading(false)
      })
  }, [days])

  useEffect(() => {
    const controller = new AbortController()
    load(controller.signal)
    return () => controller.abort()
  }, [load])

  // 明细表：展开或切换时间范围时拉取第一页；「加载更多」按当前 offset 追加
  const loadRecords = useCallback((offset: number, signal?: AbortSignal) => {
    setRecordsLoading(true)
    setRecordsFailed(false)
    return getTokenUsageRecords(days, PAGE_SIZE, offset, signal)
      .then(page => {
        if (signal?.aborted) return
        setRecordsTotal(page.total)
        setRecords(prev => offset === 0 ? page.records : [...prev, ...page.records])
        recordsOffsetRef.current = offset + page.records.length
      })
      .catch((error: unknown) => {
        if (signal?.aborted) return
        setRecordsFailed(error instanceof Error)
      })
      .finally(() => {
        if (!signal?.aborted) setRecordsLoading(false)
      })
  }, [days])

  useEffect(() => {
    if (!detailsOpen) return
    const controller = new AbortController()
    setRecords([])
    setRecordsTotal(0)
    recordsOffsetRef.current = 0
    loadRecords(0, controller.signal)
    return () => controller.abort()
  }, [detailsOpen, loadRecords])

  const toggleDetails = () => {
    setDetailsOpen(v => !v)
  }

  const totals = summary?.totals
  const cacheRate = useMemo(() => {
    const cached = totals?.cached_input_tokens ?? 0
    const input = totals?.input_tokens ?? 0
    return input > 0 ? `${Math.round((cached / input) * 100)}%` : '—'
  }, [totals])

  const pieData: PieDatum[] = useMemo(() => {
    const rows: TokenUsageCategoryRow[] = summary?.by_category ?? []
    return rows
      .filter(row => row.total_tokens > 0)
      .map(row => ({ name: categoryLabel(row.category), value: row.total_tokens, category: row.category }))
  }, [summary])

  const trendData: TokenUsageDayRow[] = useMemo(() => {
    const rows = summary?.by_day ?? []
    return rows.map(row => ({
      ...row,
      date: row.date?.length >= 10 ? row.date.slice(5, 10) : row.date,
    }))
  }, [summary])

  const hasData = (totals?.total_tokens ?? 0) > 0

  return (
    <section className="set-group" aria-label="Token 消耗统计">
      <header className="set-group__head">
        <div className="set-group__identity">
          <h2 className="set-group__title">Token 消耗统计</h2>
          {hasData && (
            <span className="er-tag er-tag--mute">{formatTokens(totals!.total_tokens)} tokens</span>
          )}
        </div>
        <div className="set-usage-actions">
          <div className="er-btn-group" role="group" aria-label="统计时间范围">
            {RANGES.map(item => (
              <button
                key={item.key}
                type="button"
                aria-pressed={range === item.key}
                data-active={range === item.key || undefined}
                onClick={() => setRange(item.key)}
                className="er-btn er-btn--ghost er-btn--sm"
              >
                {item.label}
              </button>
            ))}
          </div>
          {hasData && (
            <button
              type="button"
              className="er-btn er-btn--ghost er-btn--sm"
              aria-expanded={detailsOpen}
              onClick={toggleDetails}
            >
              {detailsOpen ? '收起明细' : '查看详细信息'}
            </button>
          )}
        </div>
        <p className="set-group__hint">
          所有走全局 LLM 的调用（解读文献、划词翻译/总结、文献研究与追问、代码分析等）按
          上游返回的 usage 计量；缓存命中为输入侧折扣量。个别网关流式不回传 usage 时该次不计入，不做估算。
        </p>
      </header>

      {loading ? (
        <div className="set-usage-state"><LoadingSpinner size="sm" /></div>
      ) : failed ? (
        <p className="set-usage-state">用量统计暂不可用，请稍后重试。</p>
      ) : !hasData ? (
        <p className="set-usage-state">当前时间范围内还没有记录到 token 消耗。运行一次论文解读、翻译或文献研究后，这里会自动出现统计。</p>
      ) : (
        <div className="set-usage">
          <div className="set-usage-cards">
            <StatCard label="总消耗" value={formatTokens(totals!.total_tokens)} sub={`共 ${totals!.calls} 次调用`} />
            <StatCard label="输入" value={formatTokens(totals!.input_tokens)} sub={totals!.reasoning_tokens > 0 ? `含思考 ${formatTokens(totals!.reasoning_tokens)}` : undefined} />
            <StatCard label="输出" value={formatTokens(totals!.output_tokens)} />
            <StatCard label="缓存命中" value={formatTokens(totals!.cached_input_tokens)} sub={`命中率 ${cacheRate}`} />
            <StatCard label="缓存写入" value={formatTokens(totals!.cache_write_tokens)} />
          </div>

          <div className="set-usage-charts">
            <div className="set-usage-chart">
              <h3 className="set-usage-chart__title">各业务消耗占比</h3>
              <ResponsiveContainer width="100%" height={260}>
                <PieChart>
                  <Pie
                    data={pieData}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    innerRadius={55}
                    outerRadius={90}
                    paddingAngle={2}
                    stroke="var(--bg-surface)"
                    strokeWidth={2}
                  >
                    {pieData.map(entry => (
                      <Cell key={entry.category} fill={categoryColor(entry.category)} />
                    ))}
                  </Pie>
                  <Tooltip
                    formatter={(value: number | string) => [`${formatTokens(Number(value))} tokens`, undefined]}
                  />
                  <Legend
                    formatter={(value: string) => {
                      const total = pieData.reduce((acc, item) => acc + item.value, 0) || 1
                      const hit = pieData.find(item => item.name === value)
                      const percent = hit ? Math.round((hit.value / total) * 100) : 0
                      return `${value} ${percent}%`
                    }}
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>

            <div className="set-usage-chart">
              <h3 className="set-usage-chart__title">每日消耗趋势</h3>
              <ResponsiveContainer width="100%" height={260}>
                <AreaChart data={trendData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                  <defs>
                    <linearGradient id="usageInput" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#3a5679" stopOpacity={0.7} />
                      <stop offset="95%" stopColor="#3a5679" stopOpacity={0.1} />
                    </linearGradient>
                    <linearGradient id="usageOutput" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#b5361c" stopOpacity={0.7} />
                      <stop offset="95%" stopColor="#b5361c" stopOpacity={0.1} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} stroke="var(--text-secondary)" />
                  <YAxis tickFormatter={(value: number) => formatTokens(value)} tick={{ fontSize: 11 }} width={48} stroke="var(--text-secondary)" />
                  <Tooltip formatter={(value: number | string, name: string) => [`${formatTokens(Number(value))} tokens`, name]} />
                  <Area type="monotone" dataKey="input_tokens" name="输入" stackId="tokens" stroke="#3a5679" fill="url(#usageInput)" />
                  <Area type="monotone" dataKey="output_tokens" name="输出" stackId="tokens" stroke="#b5361c" fill="url(#usageOutput)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          {(summary?.models?.length ?? 0) > 0 && (
            <p className="set-usage-models">
              模型消耗 Top：
              {summary!.models.map((row, index) => (
                <span key={`${row.model}-${index}`}>
                  {index > 0 && ' · '}
                  <strong title={row.model}>{row.model || '未知模型'}</strong> {formatTokens(row.total_tokens)}
                </span>
              ))}
            </p>
          )}
        </div>
      )}

      {detailsOpen && hasData && !loading && (
        <div className="set-usage-details" aria-label="逐次调用明细">
          <table className="set-usage-table">
            <thead>
              <tr>
                <th scope="col">时间</th>
                <th scope="col">业务类目</th>
                <th scope="col">模型</th>
                <th scope="col" className="is-num">输入</th>
                <th scope="col" className="is-num">输出</th>
                <th scope="col" className="is-num">缓存命中</th>
                <th scope="col" className="is-num">合计</th>
              </tr>
            </thead>
            <tbody>
              {records.map(r => (
                <tr key={r.id}>
                  <td className="is-num">{formatRecordTime(r.created_at)}</td>
                  <td>
                    {categoryLabel(r.category)}
                    {r.stage && <span className="set-usage-table__stage">{r.stage}</span>}
                  </td>
                  <td className="set-usage-table__model" title={r.model ? `${r.model} · ${r.api_format}` : undefined}>
                    {r.model || '未知模型'}
                  </td>
                  <td className="is-num">{r.input_tokens.toLocaleString()}</td>
                  <td className="is-num">{r.output_tokens.toLocaleString()}</td>
                  <td className="is-num">{r.cached_input_tokens.toLocaleString()}</td>
                  <td className="is-num">{r.total_tokens.toLocaleString()}</td>
                </tr>
              ))}
              {records.length === 0 && !recordsLoading && !recordsFailed && (
                <tr><td colSpan={7} className="set-usage-table__empty">当前时间范围内没有逐次调用记录。</td></tr>
              )}
              {recordsFailed && (
                <tr><td colSpan={7} className="set-usage-table__empty">明细加载失败，请展开重试。</td></tr>
              )}
            </tbody>
          </table>
          <div className="set-usage-details__foot">
            <span>已显示 {records.length} / 共 {recordsTotal} 次成功调用（按时间倒序）</span>
            {records.length < recordsTotal && (
              <button
                type="button"
                className="er-btn er-btn--ghost er-btn--sm"
                disabled={recordsLoading}
                onClick={() => void loadRecords(recordsOffsetRef.current)}
              >
                {recordsLoading ? '加载中…' : '加载更多'}
              </button>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
