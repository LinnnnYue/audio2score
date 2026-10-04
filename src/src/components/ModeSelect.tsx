/**
 * ModeSelect.tsx — 模式选择下拉
 *
 * 文案**不硬编码**：全部来自 engine `describe_modes()`（经 get_modes 下发）。
 * 这样引擎改文案，前端零改动。
 *
 * 交互：点击展开，从触发点 origin-aware 生长，200ms ease-out；
 * 键盘 ↑↓ / Enter / Esc 可用。
 */

import { Check, ChevronDown, Layers, Loader2, Zap } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import clsx from 'clsx'
import type { ModeInfo, TranscribeMode } from '../lib/types'
import { Tooltip } from './Tooltip'

interface Props {
  modes: ModeInfo[]
  value: TranscribeMode | null
  onChange: (mode: TranscribeMode) => void
  disabled?: boolean
}

export function ModeSelect({ modes, value, onChange, disabled }: Props) {
  const [open, setOpen] = useState(false)
  /** 键盘高亮位 */
  const [cursor, setCursor] = useState(0)
  /** 记住上次是否展开。用 state 而非 ref：渲染期调整 state 是 React 官方推荐模式，
      且不会触发 refs 规则告警。 */
  const [wasOpen, setWasOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const selected = useMemo(() => modes.find((m) => m.mode === value) ?? null, [modes, value])

  // 渲染期派生：刚展开时把光标落到当前选中项
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      const i = modes.findIndex((m) => m.mode === value)
      setCursor(i >= 0 ? i : 0)
    }
  }

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  // 键盘导航
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false)
        return
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setCursor((c) => Math.min(c + 1, modes.length - 1))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setCursor((c) => Math.max(c - 1, 0))
      } else if (e.key === 'Enter') {
        e.preventDefault()
        const m = modes[cursor]
        if (m) {
          onChange(m.mode)
          setOpen(false)
        }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, cursor, modes, onChange])

  // 键盘移动时把高亮项滚进视野
  useEffect(() => {
    if (!open) return
    listRef.current?.querySelector<HTMLElement>(`[data-idx="${cursor}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [open, cursor])

  /**
   * 空态：模式列表没拿到（引擎未就绪 / invoke 被拒 / 后端异常）。
   *
   * 踩坑实录（主上反馈「模式下拉菜单什么都没有」）：初版无空态，
   * `modes` 为空数组时按钮仍显示「选择扒谱模式」，点开是空白浮层——
   * 用户完全无从判断是加载失败还是本来就没数据。**静默空态是 bug。**
   */
  if (modes.length === 0) {
    return (
      <div
        className="flex items-center gap-2.5 rounded-[var(--r-sm)] border border-line bg-surface-3 px-3 py-2"
        role="status"
      >
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[6px] bg-accent-soft text-accent">
          <Loader2 size={12} strokeWidth={1.9} className="animate-spin" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-medium text-ink">
            模式列表加载中
          </span>
          <span className="block truncate text-[10.5px] text-ink-faint">
            若长时间无内容，请检查引擎是否已安装
          </span>
        </span>
      </div>
    )
  }

  return (
    <div ref={wrapRef} className="relative w-full">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={clsx(
          'flex w-full items-center gap-2.5 rounded-[var(--r-sm)] border border-line bg-surface-3',
          'px-3 py-2 text-left',
          'transition-[border-color,background-color,transform] duration-150 ease-out',
          'active:scale-[0.99]',
          disabled && 'pointer-events-none opacity-45',
          open && 'border-accent',
          !open && '[[@media(hover:hover)_and_(pointer:fine)]]:hover:border-[var(--border-strong)]',
        )}
      >
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[6px] bg-accent-soft text-accent">
          {selected ? <ModeIcon separates={selected.separates} /> : <Layers size={12} strokeWidth={1.9} />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-medium text-ink">
            {selected?.label ?? '选择扒谱模式'}
          </span>
          {selected && (
            <span className="block truncate text-[10.5px] text-ink-faint">
              {selected.tracks} 轨 · {selected.separates ? '需分离' : '不分离'}
            </span>
          )}
        </span>
        <ChevronDown
          size={14}
          strokeWidth={1.9}
          className={clsx(
            'shrink-0 text-ink-faint transition-transform duration-200 ease-out',
            open && 'rotate-180',
          )}
        />
      </button>

      <div
        role="listbox"
        ref={listRef}
        className={clsx(
          'absolute left-0 top-[calc(100%+5px)] z-50 w-full origin-[top_center]',
          'rounded-[var(--r-md)] border border-line bg-surface-2 p-1 shadow-pop',
          'transition-[opacity,transform] duration-200 ease-out',
          open ? 'pointer-events-auto scale-100 opacity-100' : 'pointer-events-none scale-95 opacity-0',
        )}
      >
        {modes.map((m, i) => (
          <button
            key={m.mode}
            data-idx={i}
            role="option"
            aria-selected={m.mode === value}
            onMouseEnter={() => setCursor(i)}
            onClick={() => {
              onChange(m.mode)
              setOpen(false)
            }}
            style={{ '--i': i } as React.CSSProperties}
            className={clsx(
              'stagger flex w-full items-start gap-2.5 rounded-[var(--r-sm)] px-2.5 py-2 text-left',
              'transition-colors duration-150 ease-out',
              i === cursor ? 'bg-accent-soft' : '',
            )}
          >
            <span className="mt-[2px] flex h-5 w-5 shrink-0 items-center justify-center text-ink-faint">
              <ModeIcon separates={m.separates} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-1.5">
                <span className="truncate text-[12.5px] font-medium text-ink">{m.label}</span>
                {m.separates && (
                  <span className="shrink-0 rounded-[4px] bg-surface-3 px-1 py-[1px] text-[9.5px] font-medium text-ink-faint">
                    分离
                  </span>
                )}
              </span>
              <span className="mt-0.5 block text-[10.5px] leading-relaxed text-ink-faint">
                {m.description}
              </span>
            </span>
            {m.mode === value && (
              <Check size={13} strokeWidth={2.2} className="mt-0.5 shrink-0 text-accent" />
            )}
          </button>
        ))}
      </div>
    </div>
  )
}

/** 模式图标：需分离的用 Zap（Demucs 动作），不分离的用 Layers */
function ModeIcon({ separates }: { separates: boolean }) {
  return separates ? (
    <Zap size={12} strokeWidth={1.9} />
  ) : (
    <Layers size={12} strokeWidth={1.9} />
  )
}

/** 选中模式的说明条（描述 + 产出轨数），贴在选择器下方 */
export function ModeNote({ mode }: { mode: ModeInfo | null }) {
  if (!mode) return null
  return (
    <div className="flex animate-rise-in items-start gap-2 rounded-[var(--r-sm)] bg-accent-soft px-2.5 py-2">
      <p className="flex-1 text-[11.5px] leading-relaxed text-ink-dim">{mode.description}</p>
      <Tooltip content={`输出 ${mode.tracks} 条MIDI 音轨`}>
        <span className="num mt-[1px] shrink-0 text-[10.5px] font-medium text-accent">
          {mode.tracks} 轨
        </span>
      </Tooltip>
    </div>
  )
}
