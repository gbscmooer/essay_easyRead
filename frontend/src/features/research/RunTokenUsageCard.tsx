/**
 * RunTokenUsageCard — 单次探索 run 的 LLM token 用量
 *
 * 报告生成后展示本次研究的输入/输出/缓存命中与调用次数，可展开按阶段明细
 * （拆解/召回/报告提取/追问…）。数据来自持久用量记录（含追问与报告重生成），
 * 旧 run 回退 result 内的台账快照。
 */
import { useEffect, useState } from 'react'
import { getRunTokenUsage } from '@/api/exploration'
import type { RunTokenUsage } from '@/types'

const STAGE_LABELS: Record<string, string> = {
  preflight: '数据源预检',
  decompose: '问题拆解',
  recall: '目标召回',
  metrics: '文献计量',
  synthesis: '综合分析',
  report_extract: '报告提取',
  report_synthesize: '报告撰写',
  report_relation_verify: '关系核验',
  report_final_audit: '旧版终稿核验',
  report_regen: '报告重生成',
  chat: '连续追问',
  answer_draft: '作答起草',
  answer_followup: '作答补检索',
  answer_paper_read: '逐篇精读',
  answer_write: '作答定稿',
  answer_rewrite: '定稿重写',
  answer_supplement: '漏项补写',
  unlabeled: '未标注',
}

function fmt(value: number): string {
  if (!Number.isFinite(value)) return '0'
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}k`
  return String(value)
}

function fmtTps(tps: number | null | undefined): string {
  return typeof tps === 'number' && tps > 0 ? tps.toFixed(1) : '—'
}

export function RunTokenUsageCard({ runId, refreshKey }: { runId: number; refreshKey?: string | number }) {
  const [data, setData] = useState<RunTokenUsage | null>(null)
  const [open, setOpen] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    getRunTokenUsage(runId, controller.signal)
      .then(usage => {
        setData(usage)
        setFailed(false)
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true)
      })
    return () => controller.abort()
  }, [runId, refreshKey])

  if (failed || !data || !data.recorded) return null
  const totals = data.totals

  return (
    <section className="ex-tokens" aria-label="本次研究 Token 用量">
      <div className="ex-tokens__head">
        <h3 className="ex-tokens__title">本次研究 Token 用量</h3>
        <span className="ex-tokens__badge">
          {data.source === 'legacy_result' ? '旧版快照' : '含追问与报告更新'}
        </span>
        <button type="button" className="ex-tokens__toggle" onClick={() => setOpen(value => !value)}>
          {open ? '收起明细' : '按阶段明细'}
        </button>
      </div>
      <dl className="ex-tokens__grid">
        <div><dt>总消耗</dt><dd>{fmt(totals.total_tokens)}</dd></div>
        <div><dt>输入</dt><dd>{fmt(totals.input_tokens)}</dd></div>
        <div><dt>输出</dt><dd>{fmt(totals.output_tokens)}</dd></div>
        <div><dt>缓存命中</dt><dd>{fmt(totals.cached_input_tokens)}</dd></div>
        <div>
          <dt>调用次数</dt>
          <dd>
            {totals.calls}
            {typeof totals.failed_calls === 'number' && totals.failed_calls > 0 && (
              <span className="ex-tokens__failed" title="失败尝试（含重试中的失败，token 不计）">
                {' '}（失败 {totals.failed_calls}）
              </span>
            )}
          </dd>
        </div>
        <div><dt>平均 TPS</dt><dd>{fmtTps(totals.tps)}</dd></div>
      </dl>
      {open && (
        <table className="ex-tokens__table">
          <thead>
            <tr>
              <th scope="col">阶段</th><th scope="col">调用</th><th scope="col">失败</th>
              <th scope="col">输入</th>
              <th scope="col">输出</th><th scope="col">缓存命中</th><th scope="col">平均 TPS</th>
            </tr>
          </thead>
          <tbody>
            {data.by_stage.map(row => (
              <tr key={row.stage || 'unlabeled'}>
                <td>{STAGE_LABELS[row.stage] ?? row.stage ?? '未标注'}</td>
                <td>{row.calls}</td>
                <td title="失败尝试次数（token 不计；旧快照无此数据）">
                  {typeof row.failed_calls === 'number' && row.failed_calls > 0 ? row.failed_calls : '—'}
                </td>
                <td>{fmt(row.input_tokens)}</td>
                <td>{fmt(row.output_tokens)}</td>
                <td>{fmt(row.cached_input_tokens)}</td>
                <td title="输出 token/秒（不含重试等待；旧记录未计耗时显示 —）">{fmtTps(row.tps)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
