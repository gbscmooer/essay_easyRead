/**
 * parseServerDate 测试：后端 SQLite 落库后 tzinfo 丢失，API 返回 naive UTC
 * 字符串；必须按 UTC 解析而不是本地时区，否则 UTC+8 下总耗时/创建时间偏差 8 小时。
 */
import { describe, expect, it } from 'vitest'
import { parseServerDate } from '@/utils/datetime'

describe('parseServerDate', () => {
  it('naive ISO 字符串按 UTC 解析（复现 8 小时偏差场景）', () => {
    const d = parseServerDate('2026-09-18T07:23:48')
    expect(d.getTime()).toBe(Date.UTC(2026, 8, 18, 7, 23, 48))
  })

  it('带微秒的 naive 字符串截断到毫秒后按 UTC 解析', () => {
    const d = parseServerDate('2026-09-18T07:23:48.785290')
    expect(d.getTime()).toBe(Date.UTC(2026, 8, 18, 7, 23, 48, 785))
  })

  it('空格分隔的 naive 字符串（SQLite 原始格式）按 UTC 解析', () => {
    const d = parseServerDate('2026-09-18 07:23:48.785290')
    expect(d.getTime()).toBe(Date.UTC(2026, 8, 18, 7, 23, 48, 785))
  })

  it('已带 Z 或时区偏移的原样解析', () => {
    expect(parseServerDate('2026-09-18T07:23:48Z').getTime())
      .toBe(Date.UTC(2026, 8, 18, 7, 23, 48))
    expect(parseServerDate('2026-09-18T15:23:48+08:00').getTime())
      .toBe(Date.UTC(2026, 8, 18, 7, 23, 48))
  })

  it('纯日期字符串按 UTC 午夜解析', () => {
    expect(parseServerDate('2026-09-18').getTime()).toBe(Date.UTC(2026, 8, 18))
  })

  it('非字符串输入原样交给 Date 构造器', () => {
    expect(parseServerDate(0).getTime()).toBe(0)
    const date = new Date('2026-09-18T07:23:48Z')
    expect(parseServerDate(date).getTime()).toBe(date.getTime())
  })
})
