import { ConfirmDialog } from 'essay-read-frontend'

const noop = () => {}

// The dialog renders `position: fixed` over a frosted-glass backdrop. We wrap it
// in a `transform`ed box so that box becomes the containing block for the fixed
// overlay — letting the open dialog center inside a sized card instead of the
// page viewport.
const Stage = ({ children }: { children: React.ReactNode }) => (
  <div style={{ position: 'relative', width: 460, height: 300, transform: 'translateZ(0)', overflow: 'hidden', borderRadius: 14, border: '1px solid var(--border)' }}>
    {children}
  </div>
)

export const Danger = () => (
  <Stage>
    <ConfirmDialog
      open
      variant="danger"
      title="删除论文"
      message="确定要删除《Deep Residual Learning》及其全部解析结果吗？此操作不可撤销。"
      confirmLabel="删除"
      cancelLabel="取消"
      onConfirm={noop}
      onCancel={noop}
    />
  </Stage>
)

export const Warning = () => (
  <Stage>
    <ConfirmDialog
      open
      variant="warning"
      title="清空工作区"
      message="将移除工作区内的所有论文关联，但不会删除论文本身。是否继续？"
      confirmLabel="清空"
      onConfirm={noop}
      onCancel={noop}
    />
  </Stage>
)
