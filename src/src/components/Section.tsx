/**
 * Section.tsx — 区块外壳
 *
 * 页面区块默认用「全宽色带 + 无框架」，只有工具面板才套.panel。
 * 这个组件负责统一的标题行 / 序号 / 计数，让信息层级一致。
 */

import type { ReactNode } from 'react'
import clsx from 'clsx'

interface Props {
  /** 区块序号（01 / 02），营造工具面板的秩序感 */
  index?: string
  title: string
  /** 标题右侧的补充说明或计数 */
  aside?: ReactNode
  children: ReactNode
  className?: string
}

export function Section({ index, title, aside, children, className }: Props) {
  return (
    <section className={clsx('flex min-h-0 flex-col', className)}>
      <header className="mb-2.5 flex items-center gap-2">
        {index && (
          <span className="num text-[10px] font-medium tracking-wider text-ink-faint">{index}</span>
        )}
        <h2 className="text-[12px] font-semibold tracking-[0.02em] text-ink-dim">{title}</h2>
        <span className="h-px flex-1 bg-line" />
        {aside}
      </header>
      {children}
    </section>
  )
}
