/**
 * Tooltip.tsx — 轻量 tooltip
 *
 * 用 CSS hover/focus 驱动，无 JS 定位、无测量。
 * origin-aware：从触发元素生长（transform-origin 指向触发点）。
 * 触屏（hover:none）下不显示，靠原生 title 兜底。
 */

import { useId, type CSSProperties, type ReactNode } from 'react'
import clsx from 'clsx'

interface Props {
  content: ReactNode
  children: ReactNode
  side?: 'top' | 'bottom'
  className?: string
}

export function Tooltip({ content, children, side = 'top', className }: Props) {
  const id = useId()
  const origin = side === 'top' ? 'bottom center' : 'top center'

  return (
    <span className={clsx('group/tt relative inline-flex', className)}>
      <span aria-describedby={id} tabIndex={0} className="inline-flex outline-none">
        {children}
      </span>
      <span
        role="tooltip"
        id={id}
        style={{ '--tt-origin': origin } as CSSProperties}
        className={clsx(
          'pointer-events-none absolute left-1/2 z-50 w-max max-w-[264px] -translate-x-1/2',
          'rounded-[var(--r-sm)] border border-line bg-surface-2 px-2.5 py-1.5',
          'text-[11px] font-normal leading-relaxed tracking-normal text-ink-dim shadow-pop',
          'origin-[var(--tt-origin)] scale-95 opacity-0',
          'transition-[opacity,transform] duration-150 ease-out',
          'group-hover/tt:scale-100 group-hover/tt:opacity-100',
          'group-focus-within/tt:scale-100 group-focus-within/tt:opacity-100',
          side === 'top' ? 'bottom-[calc(100%+7px)]' : 'top-[calc(100%+7px)]',
          'hidden [@media(hover:hover)and(pointer:fine)]:block',
        )}
      >
        {content}
      </span>
    </span>
  )
}
