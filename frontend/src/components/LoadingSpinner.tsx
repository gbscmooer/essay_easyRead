const SIZE: Record<'sm' | 'md' | 'lg', string> = {
  sm: '16px',
  md: '24px',
  lg: '32px',
}

/** Axial four-cell register used anywhere a compact loading signal is needed. */
export function LoadingSpinner({ size = 'md', className = '' }: { size?: 'sm' | 'md' | 'lg'; className?: string }) {
  return (
    <span
      className={`er-loader ${className}`.trim()}
      style={{ '--er-loader-size': SIZE[size] } as React.CSSProperties}
      role="status"
      aria-label="加载中"
    >
      <i aria-hidden="true" />
      <i aria-hidden="true" />
      <i aria-hidden="true" />
      <i aria-hidden="true" />
    </span>
  )
}
