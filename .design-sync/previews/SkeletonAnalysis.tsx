import { SkeletonAnalysis } from 'essay-read-frontend'

// Full analysis-panel placeholder — title, tag row, paragraphs, and a block,
// shown while a paper is being parsed.
export const Default = () => (
  <div style={{ width: 480 }}>
    <SkeletonAnalysis />
  </div>
)
