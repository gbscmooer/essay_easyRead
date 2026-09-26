/**
 * 服务端时间戳解析。
 *
 * 后端统一用 `datetime.now(timezone.utc)` 生成时间并存 SQLite，落库后 tzinfo
 * 丢失，API 返回的是不带时区标记的 naive UTC 字符串（如
 * `2026-09-18T07:23:48.785290`）。直接 `new Date()` 会按本地时区解析，
 * 在 UTC+8 下显示慢 8 小时、耗时计算多 8 小时。这里把无时区标记的字符串
 * 按 UTC 解析；已带 Z / 时区偏移的原样交给标准解析器。
 */

/** 已带时区标记（Z 或 ±hh:mm / ±hhmm / ±hh） */
function hasTimezoneMarker(value: string): boolean {
  return /[Zz]$/.test(value) || /[+-]\d{2}:?(\d{2})?$/.test(value)
}

export function parseServerDate(value: string | number | Date): Date {
  if (typeof value !== 'string') return new Date(value)
  if (hasTimezoneMarker(value)) return new Date(value)
  // 纯日期（YYYY-MM-DD）按规范本身就以 UTC 解析
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return new Date(value)
  const withT = value.includes('T') ? value : value.replace(' ', 'T')
  // 部分 JS 引擎对超过 3 位的小数秒解析不一致，截断到毫秒
  const trimmed = withT.replace(/(\.\d{3})\d+$/, '$1')
  return new Date(`${trimmed}Z`)
}
