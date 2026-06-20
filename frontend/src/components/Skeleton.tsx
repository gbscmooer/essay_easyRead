/**
 * Skeleton 骨架屏组件
 *
 * 呼吸灯 + Shimmer 动画，替代普通转圈 Loading。
 * 提供 Line / Block / Circle / Card 四种形态。
 */

interface SkeletonBase {
  className?: string
  style?: React.CSSProperties
}

/* 单行文字骨架 */
export function SkeletonLine({
  width = '100%',
  height = 14,
  radius,
  className = '',
  style,
}: SkeletonBase & { width?: string | number; height?: number; radius?: string | number }) {
  return (
    <div
      className={`skeleton ${className}`}
      style={{ width, height, ...(radius != null ? { borderRadius: radius } : {}), ...style }}
    />
  )
}

/* 矩形块骨架 */
export function SkeletonBlock({
  width = '100%',
  height = 80,
  radius,
  className = '',
  style,
}: SkeletonBase & { width?: string | number; height?: number; radius?: string }) {
  return (
    <div
      className={`skeleton ${className}`}
      style={{
        width,
        height,
        borderRadius: radius ?? 'var(--seed-radius, 12px)',
        ...style,
      }}
    />
  )
}

/* 圆形骨架 */
export function SkeletonCircle({
  size = 40,
  className = '',
  style,
}: SkeletonBase & { size?: number }) {
  return (
    <div
      className={`skeleton ${className}`}
      style={{ width: size, height: size, borderRadius: '50%', ...style }}
    />
  )
}

/* 卡片骨架 — 模拟论文卡片（贴合 HomePage 最近解析卡片布局） */
export function SkeletonCard({ className = '', style }: SkeletonBase) {
  return (
    <div
      className={`skeleton-pulse p-5 ${className}`}
      style={{
        border: '1px solid var(--border)',
        borderRadius: 'var(--seed-radius, 12px)',
        ...style,
      }}
    >
      {/* 标题（两行） */}
      <SkeletonLine width="92%" height={13} style={{ marginBottom: 8 }} />
      <SkeletonLine width="64%" height={13} style={{ marginBottom: 12 }} />
      {/* 会议 / 年份 元信息行 */}
      <SkeletonLine width="46%" height={11} style={{ marginBottom: 16 }} />
      {/* 状态徽章 */}
      <SkeletonLine width={52} height={18} radius="999px" />
    </div>
  )
}

/* 分析面板骨架 — 模拟 AnalysisPanel 加载态 */
export function SkeletonAnalysis({ className = '', style }: SkeletonBase) {
  return (
    <div className={`p-6 space-y-5 ${className}`} style={style}>
      {/* 标题 */}
      <SkeletonLine width="60%" height={20} />
      {/* 标签行 */}
      <div className="flex gap-2">
        <SkeletonLine width={64} height={22} radius="999px" />
        <SkeletonLine width={48} height={22} radius="999px" />
        <SkeletonLine width={72} height={22} radius="999px" />
      </div>
      {/* 段落 */}
      <div className="space-y-2.5">
        <SkeletonLine height={12} />
        <SkeletonLine height={12} />
        <SkeletonLine width="85%" height={12} />
      </div>
      {/* 子标题 */}
      <SkeletonLine width="40%" height={16} />
      <div className="space-y-2.5">
        <SkeletonLine height={12} />
        <SkeletonLine width="92%" height={12} />
        <SkeletonLine width="70%" height={12} />
      </div>
      {/* 块 */}
      <SkeletonBlock height={100} />
    </div>
  )
}
