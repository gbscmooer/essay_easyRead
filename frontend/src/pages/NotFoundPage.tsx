import { Link } from 'react-router-dom'

export default function NotFoundPage() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4">
      <p className="text-display text-6xl" style={{ color: 'var(--text-muted)', opacity: 0.3 }}>404</p>
      <p className="text-sm" style={{ color: 'var(--text-muted)' }}>页面不存在</p>
      <Link
        to="/"
        className="px-4 py-2 text-sm rounded-lg transition-colors"
        style={{ backgroundColor: 'var(--accent)', color: 'var(--bg-base)' }}
      >
        返回首页
      </Link>
    </div>
  )
}
