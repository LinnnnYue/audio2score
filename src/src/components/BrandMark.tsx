/**
 * BrandMark — 品牌标志（六边形 + 双八分音符）。
 *
 * 与官网图标、桌面应用图标（src-tauri/icons）同源几何：
 * 深色圆角底板 + 青→紫渐变线框。
 *
 * 两点刻意的取舍：
 * 1. 几何用「小尺寸优化版」（图案放大到约 92%、线宽 3.6）—— 这个标志在
 *    标题栏只有 22px，用大图那套细线会糊成一片。
 * 2. 渐变 id 走 useId，避免一页多实例时 id 冲突（SVG 的 url(#…) 是按
 *    id 全局查找的，同名会让后出现的实例串色）。
 */

import { useId } from 'react'

type Props = {
  /** 边长（px）。默认 22 —— 标题栏的高度。 */
  size?: number
  className?: string
}

export function BrandMark({ size = 22, className }: Props) {
  // useId 生成的串含冒号（如 :r0:），在 url(#…) 里不安全，去掉。
  const uid = useId().replace(/:/g, '')
  const bgId = `bm-bg-${uid}`
  const inkId = `bm-ink-${uid}`

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 1024 1024"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id={bgId} x1="0" y1="0" x2="1024" y2="1024" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#1d2637" />
          <stop offset="1" stopColor="#0c1018" />
        </linearGradient>
        <linearGradient id={inkId} x1="190" y1="100" x2="830" y2="920" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#6fe3ff" />
          <stop offset="1" stopColor="#a99cff" />
        </linearGradient>
      </defs>

      <rect width="1024" height="1024" rx="184" fill={`url(#${bgId})`} />

      <g
        transform="translate(512 512) scale(29.5) translate(-16 -16)"
        stroke={`url(#${inkId})`}
        strokeWidth="3.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      >
        <path d="M16 2l11 7v14l-11 7l-11 -7V9z" />
        <path d="M13 20V12l8-2v8" />
      </g>
      <g transform="translate(512 512) scale(29.5) translate(-16 -16)">
        <circle cx="11" cy="20.5" r="3.2" fill="#6fe3ff" />
        <circle cx="19" cy="18.5" r="3.2" fill="#a99cff" />
      </g>
    </svg>
  )
}
