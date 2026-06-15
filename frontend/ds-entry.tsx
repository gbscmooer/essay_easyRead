/**
 * EssayRead Design System — scoped entry for /design-sync.
 *
 * Re-exports ONLY the curated, presentational components that render
 * standalone on the seed-token CSS variables. App-coupled pieces (Layout,
 * Sidebar, SelectionPopup, TerminalPanel, ThemeProvider, pages …) are
 * deliberately excluded — see .design-sync/NOTES.md.
 *
 * This file is never imported by the app; it exists only so design-sync can
 * bundle a tight, dependency-free subset (esbuild externalizes react/react-dom).
 */
export {
  SkeletonLine,
  SkeletonBlock,
  SkeletonCircle,
  SkeletonCard,
  SkeletonAnalysis,
} from './src/components/Skeleton'
export { Tooltip } from './src/components/Tooltip'
export { ConfirmDialog } from './src/components/ConfirmDialog'
export { LoadingSpinner } from './src/components/LoadingSpinner'
export { LoadingState } from './src/components/LoadingState'
export { ShortcutHelp } from './src/components/ShortcutHelp'
