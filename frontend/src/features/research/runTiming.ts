/**
 * 探索研究计时：把持久化的阶段时间线（result.sources.stage_timeline）
 * 与实时 SSE 事件时间戳（useExplorationStream.stageTimings）合并成
 * 「总耗时 + 各阶段耗时分配」，供 /explorer/:id 计时面板展示。
 *
 * 合并规则：实时事件优先（含运行中阶段的走秒），落库时间线兜底
 * （覆盖刷新后历史回放被截断、或旧 run 只有落库数据的情形）。
 */
import type { ExplorationStageTiming } from '@/types'
import type { StageTimingEntry } from '@/hooks/useExplorationStream'
import { parseServerDate } from '@/utils/datetime'

/** 与后端 explorer.py 的阶段执行顺序一致（v8 八阶段）
 *  旧 run 的时间线含 decompose/enrich/metrics/persist——它们不在新顺序表里，
 *  stageSortIndex 会排到尾部、label 回退原始字符串，历史数据仍可读。 */
export const TIMING_STAGE_ORDER = [
  'preflight', 'protocol', 'recall', 'screen', 'select', 'extract', 'synthesize', 'report',
]

const STAGE_LABELS: Record<string, string> = {
  preflight: '⓪ 数据源预检',
  protocol: '① 研究协议',
  recall: '② 多源召回',
  screen: '③ 相关性裁决与计量',
  select: '④ 分层选材与全文',
  extract: '⑤ 证据提取',
  synthesize: '⑥ 证据综合与写作',
  report: '⑦ 报告保存',
  // 旧 run 时间线兼容（v7 及之前）
  decompose: '① 拆解检索式（旧）',
  enrich: '指标富化（旧）',
  metrics: '计量与聚类（旧）',
  persist: '落盘（旧）',
}

export type StageTimeStatus = 'completed' | 'running' | 'paused' | 'failed'

export interface StageDurationRow {
  stage: string
  label: string
  /** 已耗时秒数（运行中阶段为开始至今） */
  seconds: number
  status: StageTimeStatus
}

export interface RunTiming {
  stages: StageDurationRow[]
  /** 各阶段耗时合计（秒） */
  stageSeconds: number
  /** 墙钟总耗时（秒）：创建 → 结束/当前 */
  wallSeconds: number
}

/**
 * 从不可变的阶段时间线求本次研究真正结束的时刻。
 *
 * ExplorationRun.updated_at 还会被报告重生成、revision 选择等后续操作更新，
 * 因而不能作为已完成研究的结束时间。只有旧 run 没有可用时间线时，页面
 * 才应回退 updated_at。
 */
export function resolveTimelineEndMs(
  persisted?: ExplorationStageTiming[],
  live?: Record<string, StageTimingEntry>,
): number | null {
  let latest = 0
  for (const row of persisted ?? []) {
    if (!row?.started_at || row.duration_s == null || row.duration_s < 0) continue
    const start = parseServerDate(row.started_at).getTime()
    if (!Number.isFinite(start)) continue
    latest = Math.max(latest, start + row.duration_s * 1000)
  }
  for (const entry of Object.values(live ?? {})) {
    if (entry?.endTs != null && Number.isFinite(entry.endTs)) {
      latest = Math.max(latest, entry.endTs)
    }
  }
  return latest > 0 ? latest : null
}

/** 42 秒 → 「42 秒」；3671 秒 → 「1 时 1 分 11 秒」 */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '0 秒'
  const total = Math.floor(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) return `${h} 时 ${m} 分 ${s} 秒`
  if (m > 0) return `${m} 分 ${s} 秒`
  return `${total} 秒`
}

function stageSortIndex(stage: string): number {
  const idx = TIMING_STAGE_ORDER.indexOf(stage)
  return idx >= 0 ? idx : TIMING_STAGE_ORDER.length
}

function stageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? stage
}

export function mergeRunTiming(opts: {
  createdAtMs: number
  /** 结束时刻；运行中传 null（按 nowMs 计墙钟） */
  endedAtMs: number | null
  /** 当前时刻（运行中面板每秒更新） */
  nowMs: number
  persisted?: ExplorationStageTiming[]
  live?: Record<string, StageTimingEntry>
}): RunTiming {
  const { createdAtMs, endedAtMs, nowMs, persisted, live } = opts
  const rows = new Map<string, StageDurationRow>()

  // 1) 落库时间线兜底。暂停→续跑会让同一阶段产生多段（status=paused 的
  //    闭合段 + 重入后的新段）：按阶段**求和**，最新一段的状态决定展示状态。
  //    duration_s 为 null 的进行中段不计秒（与实时走秒互斥，防双计），但
  //    其状态仍参与「冻结时沿用落库状态」的判定。
  const closed = new Map<string, {
    seconds: number
    lastStartedAtMs?: number
    lastSeconds: number
  }>()
  const lastRowStatus = new Map<string, StageTimeStatus>()
  for (const row of persisted ?? []) {
    if (!row || typeof row.stage !== 'string') continue
    const status: StageTimeStatus = row.status === 'paused'
      ? 'paused'
      : row.status === 'failed' ? 'failed' : 'completed'
    lastRowStatus.set(row.stage, status)
    if (row.duration_s == null) continue
    const prev = closed.get(row.stage)
    closed.set(row.stage, {
      seconds: (prev?.seconds ?? 0) + row.duration_s,
      lastStartedAtMs: row.started_at ? parseServerDate(row.started_at).getTime() : prev?.lastStartedAtMs,
      lastSeconds: row.duration_s,
    })
  }
  for (const [stage, agg] of closed) {
    rows.set(stage, { stage, label: stageLabel(stage), seconds: agg.seconds, status: lastRowStatus.get(stage) ?? 'completed' })
  }

  // 2) 实时事件优先：对应落库**最新一段**——started_at 与 startTs 对得上
  //    （±2s）说明该段已落库，从合计中让位给实时值避免双计；对不上（落库
  //    还没写，或本段就是暂停后的新段）则在已闭合段之和中叠加。
  //    运行中阶段按 startTs 走秒；run 已结束（暂停/失败）但该阶段没有
  //    stage_done 时，冻结在结束时刻，状态沿用落库最新段，不误标「进行中」。
  for (const [stage, entry] of Object.entries(live ?? {})) {
    if (!entry || typeof entry.startTs !== 'number') continue
    const endMs = entry.endTs ?? endedAtMs ?? nowMs
    const seconds = (endMs - entry.startTs) / 1000
    const agg = closed.get(stage)
    if (!(seconds > 0) && !agg) continue
    const matched = agg?.lastStartedAtMs != null
      && Math.abs(agg.lastStartedAtMs - entry.startTs) <= 2000
    const base = agg ? (matched ? agg.seconds - agg.lastSeconds : agg.seconds) : 0
    let status: StageTimeStatus = 'running'
    if (entry.endTs) {
      status = 'completed'
    } else if (endedAtMs != null) {
      status = lastRowStatus.get(stage) ?? 'completed'
    }
    rows.set(stage, {
      stage, label: stageLabel(stage),
      seconds: base + Math.max(seconds, 0),
      status,
    })
  }

  const stages = [...rows.values()].sort((a, b) => stageSortIndex(a.stage) - stageSortIndex(b.stage))
  const stageSeconds = stages.reduce((acc, row) => acc + row.seconds, 0)
  const wallMs = (endedAtMs ?? nowMs) - createdAtMs
  return {
    stages,
    stageSeconds,
    wallSeconds: wallMs > 0 ? wallMs / 1000 : 0,
  }
}
