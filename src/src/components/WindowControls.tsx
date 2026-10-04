/**
 * WindowControls.tsx — 无边框窗口的右上角三键
 *
 * 要求：宽松留白、命中区充足（36×28，视觉 10×10 但命中区 36 宽）。
 * 用 data-tauri-drag-region 无关——本项目 chrome 整体可拖动，
 * 这三个键自身必须 stopPropagation 否则拖不动。
 */

import { getCurrentWindow } from '@tauri-apps/api/window'
import { useEffect, useState } from 'react'
import clsx from 'clsx'

const inTauri = (): boolean =>
  typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

export function WindowControls() {
  const [maximized, setMaximized] = useState(false)

  useEffect(() => {
    if (!inTauri()) return
    let alive = true
    getCurrentWindow()
      .isMaximized()
      .then((v) => {
        if (alive) setMaximized(v)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])

  const act = (fn: () => Promise<void>) => (e: React.MouseEvent) => {
    e.stopPropagation()
    fn().catch(() => {})
  }

  const onToggleMax = () => {
    getCurrentWindow()
      .toggleMaximize()
      .then(() => getCurrentWindow().isMaximized())
      .then(setMaximized)
      .catch(() => {})
  }

  return (
    <div className="flex items-center" style={{ marginRight: -10 }}>
      <WinBtn label="最小化" onClick={act(() => getCurrentWindow().minimize())}>
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
          <path d="M1.5 5h7" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
        </svg>
      </WinBtn>
      <WinBtn label={maximized ? '还原' : '最大化'} onClick={onToggleMax}>
        {maximized ? (
          <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
            <rect x="1.5" y="3" width="5.5" height="5.5" fill="none" stroke="currentColor" strokeWidth="1.1" />
            <path d="M3.6 3V1.9h5.5v5.5H7.4" fill="none" stroke="currentColor" strokeWidth="1.1" />
          </svg>
        ) : (
          <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
            <rect x="1.5" y="1.5" width="7" height="7" fill="none" stroke="currentColor" strokeWidth="1.1" />
          </svg>
        )}
      </WinBtn>
      <WinBtn label="关闭" danger onClick={act(() => getCurrentWindow().close())}>
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
          <path
            d="M1.8 1.8l6.4 6.4M8.2 1.8L1.8 8.2"
            stroke="currentColor"
            strokeWidth="1.1"
            strokeLinecap="round"
          />
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
  children: React.ReactNode
  label: string
  onClick: (e: React.MouseEvent) => void
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
        'text-ink-faint transition-[background-color,color,transform] duration-150 ease-out',
        'active:scale-[0.94]',
        danger && 'hover:text-danger',
      )}
      style={{ color: danger ? 'var(--danger)' : undefined }}
      onMouseEnter={(e) => {
        if (!danger) e.currentTarget.style.color = 'var(--text)'
      }}
      onMouseLeave={(e) => {
        if (!danger) e.currentTarget.style.color = ''
      }}
    >
      {children}
    </button>
  )
}
