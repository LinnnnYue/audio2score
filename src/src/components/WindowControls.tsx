/**
 * WindowControls.tsx — 无边框窗口的右上角三键
 *
 * 命中区 38×38（Windows 最小舒适值），视觉图标 12px（原 10px 偏小、发虚，故上调）。
 * hover 态包在 [@media(hover:hover)_and_(pointer:fine)] 内，触屏不触发幽灵态。
 * 这三个键必须 stopPropagation，否则会被 chrome 的拖动区吃掉点击。
 */

import { getCurrentWindow } from '@tauri-apps/api/window'
import { useEffect, useState, type MouseEvent, type ReactNode } from 'react'
import clsx from 'clsx'

const inTauri = (): boolean =>
  typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

const HOVER = '[[@media(hover:hover)_and_(pointer:fine)]]:hover:'

export function WindowControls() {
  const [maximized, setMaximized] = useState(false)

  useEffect(() => {
    if (!inTauri()) return
    let alive = true
    getCurrentWindow()
      .isMaximized()
      .then((v) => alive && setMaximized(v))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])

  const guard =
    (fn: () => Promise<void>) =>
    (e: MouseEvent) => {
      e.stopPropagation()
      fn().catch(() => {})
    }

  const onToggleMax = (e: MouseEvent) => {
    e.stopPropagation()
    getCurrentWindow()
      .toggleMaximize()
      .then(() => getCurrentWindow().isMaximized())
      .then(setMaximized)
      .catch(() => {})
  }

  return (
    <div className="flex items-center gap-0.5" style={{ marginRight: -8 }}>
      <WinBtn label="最小化" onClick={guard(() => getCurrentWindow().minimize())}>
        <svg width="12" height="12" viewBox="0 0 10 10" aria-hidden="true">
          <path d="M1.5 5h7" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
        </svg>
      </WinBtn>

      <WinBtn label={maximized ? '还原' : '最大化'} onClick={onToggleMax}>
        {maximized ? (
          <svg width="12" height="12" viewBox="0 0 10 10" aria-hidden="true">
            <path d="M3.6 3.2V1.9h5.5v5.5H7.8" fill="none" stroke="currentColor" strokeWidth="1.1" />
            <rect x="1.5" y="3.2" width="5.3" height="5.3" fill="none" stroke="currentColor" strokeWidth="1.1" />
          </svg>
        ) : (
          <svg width="12" height="12" viewBox="0 0 10 10" aria-hidden="true">
            <rect x="1.5" y="1.5" width="7" height="7" fill="none" stroke="currentColor" strokeWidth="1.1" />
          </svg>
        )}
      </WinBtn>

      <WinBtn label="关闭" danger onClick={guard(() => getCurrentWindow().close())}>
        <svg width="12" height="12" viewBox="0 0 10 10" aria-hidden="true">
          <path d="M1.8 1.8l6.4 6.4M8.2 1.8L1.8 8.2" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
        </svg>
      </WinBtn>
    </div>
  )
}

function WinBtn({
  children,
  label,
  onClick,
  danger,
}: {
  children: ReactNode
  label: string
  onClick: (e: MouseEvent) => void
  danger?: boolean
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className={clsx(
        'flex h-[38px] w-[38px] items-center justify-center rounded-[var(--r-sm)]',
        'transition-[color,background-color,transform] duration-150 ease-out',
        'active:scale-[0.94]',
        danger
          ? clsx('text-ink-faint', `${HOVER}bg-bad-soft`, `${HOVER}text-bad`)
          : clsx('text-ink-faint', `${HOVER}bg-accent-soft`, `${HOVER}text-ink`),
      )}
    >
      {children}
    </button>
  )
}
