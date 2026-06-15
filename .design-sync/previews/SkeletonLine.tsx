import { SkeletonLine } from 'essay-read-frontend'

export const Default = () => <SkeletonLine width={280} />

export const TextBlock = () => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 10, width: 320 }}>
    <SkeletonLine width="70%" height={16} />
    <SkeletonLine />
    <SkeletonLine />
    <SkeletonLine width="45%" />
  </div>
)

export const Pill = () => <SkeletonLine width={72} height={22} radius="999px" />
