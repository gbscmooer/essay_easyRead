import { SkeletonCircle } from 'essay-read-frontend'

export const Avatar = () => <SkeletonCircle />

export const Sizes = () => (
  <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
    <SkeletonCircle size={24} />
    <SkeletonCircle size={40} />
    <SkeletonCircle size={64} />
  </div>
)
