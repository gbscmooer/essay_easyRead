export function LoadingSpinner({ size = 'md', className = '' }: { size?: 'sm' | 'md' | 'lg'; className?: string }) {
  // 尺寸与描边粗细成比例，保证环形在各尺寸下视觉一致
  const sizeMap = {
    sm: { box: 'w-4 h-4', border: 2 },
    md: { box: 'w-6 h-6', border: 2.5 },
    lg: { box: 'w-8 h-8', border: 3 },
  } as const
  const { box, border } = sizeMap[size]
  return (
    <div
      className={`${box} rounded-full animate-spin ${className}`}
      style={{
        borderStyle: 'solid',
        borderWidth: border,
        borderColor: 'var(--border)',
        borderTopColor: 'var(--accent)',
      }}
      role="status"
      aria-label="加载中"
    />
  )
}
