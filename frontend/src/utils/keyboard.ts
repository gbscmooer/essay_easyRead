import type { KeyboardEvent as ReactKeyboardEvent } from 'react'

/** 行内可交互元素处理 Enter/Space 时，不能让外层账本行同时执行导航。 */
export function stopRowActivation(event: ReactKeyboardEvent<HTMLElement>): void {
  if (event.key === 'Enter' || event.key === ' ') {
    event.stopPropagation()
  }
}
