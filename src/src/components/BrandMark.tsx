/**
 * BrandMark — 品牌标志（六边形 + 双八分音符）。
 *
 * 与官网页头 .brand-mark、桌面应用图标（src-tauri/icons）同源：
 * 白底圆角方块 + 青(#6fe3ff)→紫(#a99cff) 渐变线框。
 *
 * 三点刻意的取舍：
 * 1. 底板用白色（不是界面主色）—— 照搬官网 light 主题下的 .brand-mark，
 *    它靠极淡描边界定边界，在浅色面上呈「浮起的小卡片」。
 * 2. 几何用「小尺寸优化版」（图案放大到约 70%、线宽 2.5）—— 这个标志在
 *    标题栏只有 22px，用大图那套细线会糊成一片。
 * 3. 渐变 id 走 useId，避免一页多实例时 id 冲突（SVG 的 url(#…) 是按
 *    id 全局查找的，同名会让后出现的实例串色）。
 */

import { useId } from 'react'

type Props = {
  /** 边长（px）。默认 22 —— 标题栏 logo 位的高度。 */
  size?: number
  className?: string
}

export function BrandMark({ size = 22, className }: Props) {
  // useId 生成的串含冒号（如 :r0:），在 url(#…) 里不安全，去掉。
  const uid = useId().replace(/:/g, '')
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
        <linearGradient id={inkId} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#6fe3ff" />
          <stop offset="1" stopColor="#a99cff" />
        </linearGradient>
      </defs>

      {/* 白底圆角方块 + 极淡描边 */}
      <rect x="0" y="0" width="1024" height="1024" rx="232" fill="#ffffff" />
      <rect
        x="3"
        y="3"
        width="1018"
        height="1018"
        rx="229.5"
        fill="none"
        stroke="#1e4696"
        strokeOpacity="0.10"
        strokeWidth="6"
      />

      {/* 六边形 + 双八分音符 */}
      <g
        transform="translate(512 512) scale(25.5) translate(-16 -16)"
        stroke={`url(#${inkId})`}
        strokeWidth="2.5"
        strokeLinejoin="round"
        fill="none"
      >
        <path d="M16 3l10.5 6.5v13L16 29 5.5 22.5v-13z" />
        <path d="M13.2 20.2v-7.6l7-1.8v7.4" strokeLinecap="round" />
      </g>
      <g transform="translate(512 512) scale(25.5) translate(-16 -16)">
        <circle cx="11.4" cy="20.6" r="2.5" fill="#6fe3ff" />
        <circle cx="18.4" cy="18.8" r="2.5" fill="#a99cff" />
      </g>
    </svg>
  )
}
