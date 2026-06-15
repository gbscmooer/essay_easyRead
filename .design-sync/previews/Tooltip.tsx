import { Tooltip } from 'essay-read-frontend'

// NOTE: the tooltip bubble appears on hover/focus only (portal to <body>), so a
// static screenshot shows the triggers, not the open bubble. These cells show
// the canonical compositions — wrapping an icon or a label as the trigger.

export const IconTrigger = () => (
  <Tooltip content="重新解析这篇论文">
    <button
      aria-label="重新解析"
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        width: 32, height: 32, borderRadius: 8, cursor: 'pointer',
        background: 'var(--bg-surface)', border: '1px solid var(--border)', color: 'var(--text-secondary)',
      }}
    >
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 2v6h-6" /><path d="M3 12a9 9 0 0 1 15-6.7L21 8" /><path d="M3 22v-6h6" /><path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
      </svg>
    </button>
  </Tooltip>
)

export const TextTrigger = () => (
  <Tooltip content="Conference on Computer Vision and Pattern Recognition" placement="top">
    <span style={{ fontSize: 14, color: 'var(--accent)', borderBottom: '1px dashed var(--accent)', cursor: 'help' }}>
      CVPR 2016
    </span>
  </Tooltip>
)
