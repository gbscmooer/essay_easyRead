import { type ReactNode, type CSSProperties, type ElementType } from 'react'
import { useReveal } from '@/hooks/useReveal'

interface RevealProps {
  children: ReactNode
  /** 渲染的标签，默认 div */
  as?: ElementType
  /** 进入视口后延迟（ms），配合列表逐项错峰 */
  delay?: number
  className?: string
  style?: CSSProperties
}

/**
 * 滚动揭示包裹器 —— 元素进入视口时淡入上移。
 * 包裹任意内容即可，逻辑层零侵入；reduced-motion 下自动直显。
 */
export function Reveal({ children, as: Tag = 'div', delay = 0, className = '', style }: RevealProps) {
  const { ref, visible } = useReveal<HTMLElement>()
  return (
    <Tag
      ref={ref as never}
      className={`reveal ${visible ? 'is-visible' : ''} ${className}`}
      style={{ transitionDelay: visible ? `${delay}ms` : '0ms', ...style }}
    >
      {children}
    </Tag>
  )
}
