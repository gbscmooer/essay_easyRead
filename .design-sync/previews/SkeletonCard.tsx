import { SkeletonCard } from 'essay-read-frontend'

export const Default = () => (
  <div style={{ width: 320 }}>
    <SkeletonCard />
  </div>
)

export const Grid = () => (
  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, width: 520 }}>
    <SkeletonCard />
    <SkeletonCard />
    <SkeletonCard />
    <SkeletonCard />
  </div>
)
