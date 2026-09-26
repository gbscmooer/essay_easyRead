/**
 * runTiming 纯函数测试：持久化阶段时间线 ⊕ 实时事件时间戳 →
 * 总耗时 + 各阶段耗时分配。覆盖落库兜底、实时覆盖、运行中走秒、
 * 暂停/失败阶段的冻结与状态标注、未知阶段排序。
 */
import { describe, expect, it } from 'vitest'
import { formatDuration, mergeRunTiming, resolveTimelineEndMs } from '@/features/research/runTiming'

describe('formatDuration', () => {
  it('按秒/分/时分层', () => {
    expect(formatDuration(0)).toBe('0 秒')
    expect(formatDuration(-3)).toBe('0 秒')
    expect(formatDuration(42)).toBe('42 秒')
    expect(formatDuration(754)).toBe('12 分 34 秒')
    expect(formatDuration(3671)).toBe('1 时 1 分 11 秒')
  })
})

describe('mergeRunTiming', () => {
  it('只有落库时间线：按阶段顺序输出并合计', () => {
    const timing = mergeRunTiming({
      createdAtMs: 0,
      endedAtMs: 600_000,
      nowMs: 600_000,
      persisted: [
        { stage: 'report', started_at: '', duration_s: 360, status: 'completed' },
        { stage: 'preflight', started_at: '', duration_s: 2.5, status: 'completed' },
        { stage: 'recall', started_at: '', duration_s: 120, status: 'completed' },
      ],
    })
    expect(timing.stages.map(s => s.stage)).toEqual(['preflight', 'recall', 'report'])
    expect(timing.stageSeconds).toBeCloseTo(482.5)
    expect(timing.wallSeconds).toBe(600)
  })

  it('实时事件优先：运行中阶段按 nowMs 走秒并标注 running', () => {
    const timing = mergeRunTiming({
      createdAtMs: 0,
      endedAtMs: null,
      nowMs: 200_000,
      persisted: [
        { stage: 'decompose', started_at: '', duration_s: 10, status: 'completed' },
        { stage: 'recall', started_at: '', duration_s: null, status: 'running' },
      ],
      live: { recall: { startTs: 150_000 } },
    })
    const recall = timing.stages.find(s => s.stage === 'recall')
    expect(recall?.seconds).toBeCloseTo(50)
    expect(recall?.status).toBe('running')
    // 落库的 null duration 行被实时数据补齐，不会输出 0 值占位
    expect(timing.stages).toHaveLength(2)
    expect(timing.wallSeconds).toBe(200) // 运行中：墙钟按当前时刻
  })

  it('阶段结束后实时 endTs 覆盖同段落库值（started_at 对得上）', () => {
    const timing = mergeRunTiming({
      createdAtMs: 0,
      endedAtMs: null,
      nowMs: 500_000,
      // started_at 与 live startTs 一致（±2s）：实时段就是这一行，用实时值
      persisted: [{ stage: 'recall', started_at: '1970-01-01T00:01:40.000Z', duration_s: 99, status: 'completed' }],
      live: { recall: { startTs: 100_000, endTs: 160_000 } },
    })
    expect(timing.stages[0]?.seconds).toBeCloseTo(60)
    expect(timing.stages[0]?.status).toBe('completed')
  })

  it('暂停→续跑：同阶段多段按求和展示，最新段状态决定状态', () => {
    const timing = mergeRunTiming({
      createdAtMs: 0,
      endedAtMs: 700_000,
      nowMs: 700_000,
      persisted: [
        { stage: 'preflight', started_at: '1970-01-01T00:00:00.000Z', duration_s: 2, status: 'completed' },
        { stage: 'report', started_at: '1970-01-01T00:02:00.000Z', duration_s: 120, status: 'paused' },
        { stage: 'report', started_at: '1970-01-01T00:55:00.000Z', duration_s: 60, status: 'completed' },
      ],
    })
    const report = timing.stages.find(s => s.stage === 'report')
    expect(report?.seconds).toBeCloseTo(180) // 暂停前 120s + 续跑后 60s
    expect(report?.status).toBe('completed')
    expect(timing.stageSeconds).toBeCloseTo(182)
  })

  it('续跑后运行中阶段：实时走秒叠加已闭合段，暂停间隔不计入', () => {
    const timing = mergeRunTiming({
      createdAtMs: 0,
      endedAtMs: null,
      nowMs: 560_000,
      persisted: [
        // 暂停前 report 跑了 120s 后暂停；续跑实时段从 500_000 重新开始
        { stage: 'report', started_at: '1970-01-01T00:02:00.000Z', duration_s: 120, status: 'paused' },
      ],
      live: { report: { startTs: 500_000 } },
    })
    const report = timing.stages.find(s => s.stage === 'report')
    expect(report?.seconds).toBeCloseTo(180) // 120 + (560_000-500_000)/1000
    expect(report?.status).toBe('running')
    // 墙钟含暂停间隔（0 → 560_000），阶段合计不含
    expect(timing.wallSeconds).toBe(560)
    expect(timing.stageSeconds).toBeCloseTo(180)
  })

  it('实时段已落库时（started_at 匹配）不双计', () => {
    const timing = mergeRunTiming({
      createdAtMs: 0,
      endedAtMs: null,
      nowMs: 700_000,
      persisted: [
        { stage: 'report', started_at: '1970-01-01T00:02:00.000Z', duration_s: 120, status: 'paused' },
        { stage: 'report', started_at: '1970-01-01T00:05:50.000Z', duration_s: 40, status: 'completed' },
      ],
      // 实时段就是最新落库段（startTs≈3500s），匹配后让位实时值：120 + 40
      live: { report: { startTs: 350_000, endTs: 390_000 } },
    })
    const report = timing.stages.find(s => s.stage === 'report')
    expect(report?.seconds).toBeCloseTo(160)
    expect(report?.status).toBe('completed')
  })

  it('暂停的 run：无 stage_done 的阶段冻结在结束时刻，状态沿用落库', () => {
    const timing = mergeRunTiming({
      createdAtMs: 0,
      endedAtMs: 300_000, // 暂停发生时刻
      nowMs: 999_999_999, // 面板时钟继续走，但已结束的 run 不该跟着走
      persisted: [{ stage: 'recall', started_at: '', duration_s: null, status: 'paused' }],
      live: { recall: { startTs: 120_000 } },
    })
    const recall = timing.stages.find(s => s.stage === 'recall')
    expect(recall?.seconds).toBeCloseTo(180) // 300_000 - 120_000
    expect(recall?.status).toBe('paused')
    expect(timing.wallSeconds).toBe(300)
  })

  it('失败阶段：状态 failed 且不再走秒', () => {
    const timing = mergeRunTiming({
      createdAtMs: 0,
      endedAtMs: 240_000,
      nowMs: 999_999_999,
      persisted: [{ stage: 'enrich', started_at: '', duration_s: null, status: 'failed' }],
      live: { enrich: { startTs: 180_000 } },
    })
    const enrich = timing.stages.find(s => s.stage === 'enrich')
    expect(enrich?.seconds).toBeCloseTo(60)
    expect(enrich?.status).toBe('failed')
  })

  it('未知阶段排在已知阶段之后，顺序稳定', () => {
    const timing = mergeRunTiming({
      createdAtMs: 0,
      endedAtMs: 100_000,
      nowMs: 100_000,
      persisted: [{ stage: 'custom_stage', started_at: '', duration_s: 5, status: 'completed' }],
      live: { report: { startTs: 90_000, endTs: 95_000 } },
    })
    expect(timing.stages.map(s => s.stage)).toEqual(['report', 'custom_stage'])
  })

  it('无任何阶段数据时仍返回墙钟总耗时', () => {
    const timing = mergeRunTiming({ createdAtMs: 0, endedAtMs: 60_000, nowMs: 60_000 })
    expect(timing.stages).toEqual([])
    expect(timing.stageSeconds).toBe(0)
    expect(timing.wallSeconds).toBe(60)
  })
})

describe('resolveTimelineEndMs', () => {
  it('报告重生成更新 run.updated_at 后仍以研究时间线结束点为准', () => {
    const persisted = [
      {
        stage: 'decompose',
        started_at: '2026-09-19T07:36:22.000Z',
        duration_s: 42,
        status: 'completed' as const,
      },
      {
        stage: 'persist',
        started_at: '2026-09-19T07:55:34.456Z',
        duration_s: 0.01,
        status: 'completed' as const,
      },
    ]
    const timelineEnd = resolveTimelineEndMs(persisted)
    expect(timelineEnd).toBe(new Date('2026-09-19T07:55:34.466Z').getTime())

    // run.updated_at=09:06:33 是后续报告重生成时间，不能把这 71 分钟算进研究。
    const timing = mergeRunTiming({
      createdAtMs: new Date('2026-09-19T07:36:21.988Z').getTime(),
      endedAtMs: timelineEnd,
      nowMs: new Date('2026-09-19T09:06:33.848Z').getTime(),
      persisted,
    })
    expect(timing.wallSeconds).toBeCloseTo(1152.478)
    expect(timing.wallSeconds).toBeLessThan(1200)
  })

  it('没有闭合时间线时返回 null，由调用方回退 updated_at', () => {
    expect(resolveTimelineEndMs([
      { stage: 'report', started_at: '2026-09-19T07:44:46Z', duration_s: null, status: 'running' },
    ])).toBeNull()
  })
})
