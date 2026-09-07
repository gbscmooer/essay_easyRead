/**
 * 设计系统入口 — EssayRead Design System (app-facing barrel).
 *
 * 面向应用的设计系统入口:精选展示型组件统一从这里导入(`@/ds-entry`),
 * 而不是各自的组件模块,让设计系统表面只有一个真源。
 * 新增共享 UI 基元时,在对应组件模块实现后,从这里再导出一行即可。
 */
export {
  SkeletonLine,
  SkeletonBlock,
  SkeletonCircle,
  SkeletonCard,
  SkeletonAnalysis,
} from '@/components/Skeleton'
export { Tooltip } from '@/components/Tooltip'
export { ConfirmDialog } from '@/components/ConfirmDialog'
export { LoadingSpinner } from '@/components/LoadingSpinner'
export { LoadingState } from '@/components/LoadingState'
export { ShortcutHelp } from '@/components/ShortcutHelp'
