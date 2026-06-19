/**
 * 设计系统入口 — EssayRead Design System (app-facing barrel).
 *
 * 面向应用的设计系统入口:精读助手里被同步到 claude.ai/design 的那批精选展示型
 * 组件,统一从这里导入(`@/ds-entry`),而不是各自的组件模块,让设计系统表面只有
 * 一个真源。
 *
 * 这是 frontend/ds-entry.tsx(design-sync 构建用、走相对路径)的应用侧镜像 —— 此处
 * 走 `@/` 别名,供 Vite / tsc 解析。新增共享 UI 基元时,在对应组件模块实现后,从这里
 * 再导出一行即可。
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
