import { LoadingSpinner } from 'essay-read-frontend'

export const Sizes = () => (
  <div style={{ display: 'flex', gap: 24, alignItems: 'center' }}>
    <LoadingSpinner size="sm" />
    <LoadingSpinner size="md" />
    <LoadingSpinner size="lg" />
  </div>
)

export const Inline = () => (
  <div style={{ display: 'flex', gap: 10, alignItems: 'center', color: 'var(--text-secondary)', fontSize: 14 }}>
    <LoadingSpinner size="sm" />
    <span>加载中…</span>
  </div>
)
