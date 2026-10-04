/**
 * ThemeSwitcher.tsx — 窗口 chrome 区的四方向主题切换器
 *
 * 关键：切换只改 :root CSS 变量，**不重挂组件树** → 页面状态、已选文件、
 * 运行中的任务全部不丢（主上验收点）。
 *
 * 下拉从触发按钮生长：transform-origin: top right，150–250ms 时长内。
 */

import { Check, ChevronsUpDown } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import clsx from 'clsx'
import { THEME_LIST, type ThemeId } from '../theme/themes'

interface Props {
  value: ThemeId
  onChange: (id: ThemeId) => void
}

export function ThemeSwitcher({ value, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const current = THEME_LIST.find((t) => t.id === value) ?? THEME_LIST[0]

  // 点击外部 / Esc 关闭
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={`主题：${current.label} — ${current.hint}`}
        className={clsx(
          'flex h-[30px] items-center gap-2 rounded-[var(--r-sm)] px-2',
          'text-[12px] text-ink-dim',
          'border border-transparent',
          'transition-[background-color,color,border-color] duration-150 ease-out',
          'active:scale-[0.97]',
          '[[@media(hover:hover)_and_(pointer:fine)]]:hover:border-line',
          '[[@media(hover:hover)_and_(pointer:fine)]]:hover:bg-accent-soft',
          '[[@media(hover:hover)_and_(pointer:fine)]]:hover:text-ink',
          open && 'border-line bg-accent-soft text-ink',
        )}
      >
        <Swatch colors={current.swatch} />
        <span className="font-medium">{current.label}</span>
        <ChevronsUpDown size={12} strokeWidth={1.8} className="text-ink-faint" />
      </button>

      {/* origin-aware：从按钮右下角生长，180ms ease-out */}
      <div
        role="listbox"
        className={clsx(
          'absolute right-0 top-[calc(100%+6px)] z-50 w-[228px] origin-[top_right]',
          'rounded-[var(--r-md)] border border-line bg-surface-2 p-1 shadow-pop',
          'transition-[opacity,transform] duration-200 ease-out',
          open
            ? 'pointer-events-auto scale-100 opacity-100'
            : 'pointer-events-none scale-95 opacity-0',
        )}
      >
        {THEME_LIST.map((t, i) => (
          <button
            key={t.id}
            role="option"
            aria-selected={t.id === value}
            onClick={() => {
              onChange(t.id)
              setOpen(false)
            }}
            style={{ '--i': i } as React.CSSProperties}
            className={clsx(
              'stagger flex w-full items-center gap-2.5 rounded-[var(--r-sm)] px-2 py-1.5 text-left',
              'transition-colors duration-150 ease-out',
              'active:scale-[0.98]',
              t.id === value ? 'bg-accent-soft' : '[[@media(hover:hover)_and_(pointer:fine)]]:hover:bg-surface-3',
            )}
          >
            <Swatch colors={t.swatch} />
            <span className="min-w-0 flex-1">
              <span className="block text-[12.5px] font-medium text-ink">{t.label}</span>
              <span className="block truncate text-[10.5px] text-ink-faint">{t.hint}</span>
            </span>
            {t.id === value && (
              <Check size={13} strokeWidth={2.2} className="shrink-0 text-accent" />
            )}
          </button>
        ))}
      </div>
    </div>
  )
}

/** 三色识别色板：纯功能性，不是装饰 */
function Swatch({ colors }: { colors: [string, string, string] }) {
  return (
    <span className="flex shrink-0 overflow-hidden rounded-[3px] ring-1 ring-inset ring-[var(--border-strong)]">
      {colors.map((c) => (
        <span key={c} className="h-[13px] w-[5px]" style={{ background: c }} />
      ))}
    </span>
  )
}
