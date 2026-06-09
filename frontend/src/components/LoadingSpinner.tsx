export function LoadingSpinner({ size = 'md', className = '' }: { size?: 'sm' | 'md' | 'lg'; className?: string }) {
  const sizeClasses = { sm: 'w-4 h-4', md: 'w-6 h-6', lg: 'w-8 h-8' }
  return (
    <div
      className={`${sizeClasses[size]} border-2 rounded-full animate-spin ${className}`}
      style={{ borderColor: 'var(--border)', borderTopColor: 'var(--accent)' }}
      role="status"
      aria-label="加载中"
    />
  )
}
