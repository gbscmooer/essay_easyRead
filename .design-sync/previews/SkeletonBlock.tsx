import { SkeletonBlock } from 'essay-read-frontend'

export const Default = () => (
  <div style={{ width: 320 }}>
    <SkeletonBlock />
  </div>
)

export const Thumbnail = () => <SkeletonBlock width={120} height={120} />

export const Banner = () => (
  <div style={{ width: 320 }}>
    <SkeletonBlock height={160} radius="16px" />
  </div>
)
