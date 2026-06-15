import { ShortcutHelp } from 'essay-read-frontend'

const noop = () => {}

// Open keyboard-shortcuts dialog. Wrapped in a `transform`ed box so the
// `position: fixed` overlay is contained within a sized card (see ConfirmDialog
// for the same technique).
export const Open = () => (
  <div style={{ position: 'relative', width: 480, height: 460, transform: 'translateZ(0)', overflow: 'hidden', borderRadius: 14, border: '1px solid var(--border)' }}>
    <ShortcutHelp open onClose={noop} />
  </div>
)
