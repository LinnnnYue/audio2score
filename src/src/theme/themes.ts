/**
 * themes.ts — 四方向主题 token（CSS 变量为唯一主题源）
 *
 * 架构约束：
 * 1. 切换主题**只改 :root 上的 CSS 变量**，不重挂组件树 → 页面状态不丢。
 * 2. 颜色一律走 `var(--*)`，组件里不出现字面色值。
 * 3. 主上红线：无品红（magenta/#FF00FF/霓虹粉紫）、无纯黑 #000000 大面积背景。
 *    深色方向用带暖调（Cellar #1A1815）/ 靛调（Abyss #14182B）的深色。
 */

export type ThemeId = 'frost' | 'cellar' | 'abyss' | 'paper'

export interface ThemeMeta {
  id: ThemeId
  label: string
  /** 一句话气质说明，显示在切换器 tooltip 里 */
  hint: string
  /** 切换器里的色板预览（3 色），非装饰，仅作识别 */
  swatch: [string, string, string]
}

/**
 * token 清单（全部方向必须提供齐全，缺一个就回退到 frost）：
 *   bg / bg-elev / surface / surface-2 / surface-3
 *   border / border-strong
 *   text / text-dim / text-faint
 *   accent / accent-hover / accent-contrast / accent-soft / accent-border
 *   success / warning / danger / danger-soft
 *   stage-track（进度条已完成部分）
 *   glass-top（卡片顶部一条内高光，做「水晶玻璃」的受光感；纸白方向给 0 = 不要高光）
 *   hl / hl-btn（★ 2026-10-07 官网化新增：强调渐变。hl = 文字/色块强调；
 *               hl-btn = 主按钮底。frost/abyss 走官网「青 → 紫」，
 *               cellar/paper 走同色系微差以保各自气质）
 *   r-sm / r-md / r-lg / shadow-panel / shadow-pop
 *   font-sans / font-mono
 */
export type ThemeTokens = Record<string, string>

const FROST: ThemeTokens = {
  /* ★ 2026-10-07 官网化：底色转为官网浅色 #eef3ff；accent 由深靛 #1f6f9c
     换成官网「青」#0d8fc0，按钮文字随之转为深色 #08182b
     （官网 .btn-p 同为「渐变底 + 深字」，白字在浅青渐变上不达标）。 */
  bg: '#eef3ff',
  'bg-elev': '#e4ecfa',
  surface: 'rgba(255, 255, 255, 0.74)',
  'surface-2': 'rgba(255, 255, 255, 0.92)',
  'surface-3': '#f6faff',
  border: 'rgba(30, 70, 150, 0.12)',
  'border-strong': 'rgba(30, 70, 150, 0.26)',
  text: '#16232e',
  'text-dim': '#4a5f70',
  'text-faint': '#7d92a3',
  accent: '#0d8fc0',
  'accent-hover': '#0a739c',
  'accent-contrast': '#08182b',
  'accent-soft': 'rgba(13, 143, 192, 0.11)',
  'accent-border': 'rgba(13, 143, 192, 0.36)',
  success: '#1f7a63',
  warning: '#a4681c',
  danger: '#b4443a',
  'danger-soft': 'rgba(180, 68, 58, 0.1)',
  'stage-track': 'rgba(13, 143, 192, 0.16)',
  'glass-top': 'rgba(255, 255, 255, 0.75)',
  /* 官网渐变（浅色）：--hl 同义；--hl-btn 用官网浅色专用的更亮一档，
     否则整块发暗。取自官网 html[data-theme="light"] 的 --hl-btn。 */
  hl: 'linear-gradient(135deg, #0d8fc0, #6b58d8)',
  'hl-btn': 'linear-gradient(120deg, #4bccf4, #9c88ff)',
  'r-sm': '10px',
  'r-md': '14px',
  'r-lg': '16px',
  'shadow-panel': '0 1px 2px rgba(20, 48, 70, 0.05), 0 8px 24px -12px rgba(20, 48, 70, 0.18)',
  'shadow-pop': '0 2px 6px rgba(20, 48, 70, 0.08), 0 16px 40px -16px rgba(20, 48, 70, 0.26)',
  'font-sans': `'Inter', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif`,
  'font-mono': `'JetBrains Mono', 'Cascadia Code', Consolas, monospace`,
}

const CELLAR: ThemeTokens = {
  bg: '#1a1815',
  'bg-elev': '#211e1a',
  surface: 'rgba(38, 34, 29, 0.72)',
  'surface-2': 'rgba(46, 41, 35, 0.88)',
  'surface-3': '#2a2621',
  border: 'rgba(233, 219, 195, 0.11)',
  'border-strong': 'rgba(233, 219, 195, 0.22)',
  text: '#f2ece1',
  'text-dim': '#b3a894',
  'text-faint': '#7d7365',
  accent: '#d9a441',
  'accent-hover': '#e8b558',
  'accent-contrast': '#1a1815',
  'accent-soft': 'rgba(217, 164, 65, 0.12)',
  'accent-border': 'rgba(217, 164, 65, 0.4)',
  success: '#7fa86a',
  warning: '#d98f3c',
  danger: '#d1663f',
  'danger-soft': 'rgba(209, 102, 63, 0.12)',
  'stage-track': 'rgba(233, 219, 195, 0.1)',
  'glass-top': 'rgba(255, 255, 255, 0.055)',
  /* 本方向保留原气质：渐变只做同色系微差，不引入青紫（青紫是 frost/abyss 的官网色） */
  hl: 'linear-gradient(135deg, #d9a441, #e8b558)',
  'hl-btn': 'linear-gradient(120deg, #e8b558, #c8923a)',
  'r-sm': '8px',
  'r-md': '10px',
  'r-lg': '12px',
  'shadow-panel': '0 1px 0 rgba(0, 0, 0, 0.3), 0 12px 28px -18px rgba(0, 0, 0, 0.8)',
  'shadow-pop': '0 2px 8px rgba(0, 0, 0, 0.4), 0 20px 44px -20px rgba(0, 0, 0, 0.9)',
  'font-sans': `'Inter', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif`,
  'font-mono': `'JetBrains Mono', 'Cascadia Code', Consolas, monospace`,
}

const ABYSS: ThemeTokens = {
  /* ★ 2026-10-07 官网化：底色对齐官网深色 #070b1a；accent 换官网「青」
     #6fe3ff，按钮文字转深色 #04121c（官网 .btn-p 深色态即 #04121c）。 */
  bg: '#070b1a',
  'bg-elev': '#0b1226',
  surface: 'rgba(22, 32, 62, 0.6)',
  'surface-2': 'rgba(30, 42, 80, 0.78)',
  'surface-3': '#131c38',
  border: 'rgba(150, 190, 255, 0.14)',
  'border-strong': 'rgba(150, 190, 255, 0.28)',
  text: '#e9f0ff',
  'text-dim': '#93a8cc',
  'text-faint': '#63739a',
  accent: '#6fe3ff',
  'accent-hover': '#8febff',
  'accent-contrast': '#04121c',
  'accent-soft': 'rgba(111, 227, 255, 0.12)',
  'accent-border': 'rgba(111, 227, 255, 0.38)',
  success: '#4fc98a',
  warning: '#e0b04a',
  danger: '#e8697d',
  'danger-soft': 'rgba(232, 105, 125, 0.12)',
  'stage-track': 'rgba(111, 227, 255, 0.14)',
  'glass-top': 'rgba(255, 255, 255, 0.07)',
  /* 官网渐变（深色）：--hl 与 --hl-btn 同值，即 青 → 紫 对角/横向两支 */
  hl: 'linear-gradient(135deg, #6fe3ff, #a99cff)',
  'hl-btn': 'linear-gradient(120deg, #6fe3ff, #a99cff)',
  'r-sm': '10px',
  'r-md': '14px',
  'r-lg': '16px',
  'shadow-panel': '0 1px 0 rgba(255, 255, 255, 0.03), 0 14px 34px -20px rgba(0, 0, 0, 0.9)',
  'shadow-pop': '0 2px 10px rgba(0, 0, 0, 0.45), 0 24px 50px -22px rgba(0, 0, 0, 0.95)',
  'font-sans': `'Inter', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif`,
  'font-mono': `'JetBrains Mono', 'Cascadia Code', Consolas, monospace`,
}

const PAPER: ThemeTokens = {
  bg: '#f7f5ef',
  'bg-elev': '#efede6',
  surface: '#ffffff',
  'surface-2': '#fbfaf6',
  'surface-3': '#f2f0e9',
  border: 'rgba(31, 58, 62, 0.14)',
  'border-strong': 'rgba(31, 58, 62, 0.3)',
  text: '#1a2b2e',
  'text-dim': '#4c5f62',
  'text-faint': '#7f8f90',
  accent: '#1f5c5e',
  'accent-hover': '#164a4c',
  'accent-contrast': '#ffffff',
  'accent-soft': 'rgba(31, 92, 94, 0.07)',
  'accent-border': 'rgba(31, 92, 94, 0.32)',
  success: '#3d6b46',
  warning: '#9a6b1c',
  danger: '#a83a2c',
  'danger-soft': 'rgba(168, 58, 44, 0.07)',
  'stage-track': 'rgba(31, 92, 94, 0.12)',
  'glass-top': 'rgba(255, 255, 255, 0)',
  /* 本方向保留原气质：同色系深青微渐变，白字仍成立 */
  hl: 'linear-gradient(135deg, #1f5c5e, #2f7a7c)',
  'hl-btn': 'linear-gradient(120deg, #256a6c, #1a4f51)',
  'r-sm': '8px',
  'r-md': '8px',
  'r-lg': '10px',
  'shadow-panel': '0 1px 0 rgba(31, 58, 62, 0.04), 0 6px 18px -14px rgba(31, 58, 62, 0.3)',
  'shadow-pop': '0 2px 4px rgba(31, 58, 62, 0.06), 0 14px 32px -18px rgba(31, 58, 62, 0.4)',
  'font-sans': `'Inter', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif`,
  'font-mono': `'JetBrains Mono', 'Cascadia Code', Consolas, monospace`,
}

export const THEMES: Record<ThemeId, ThemeTokens> = {
  frost: FROST,
  cellar: CELLAR,
  abyss: ABYSS,
  paper: PAPER,
}

export const THEME_LIST: ThemeMeta[] = [
  {
    id: 'frost',
    label: '霜蓝玻璃',
    hint: '官网浅色：雾白底，青紫渐变强调',
    swatch: ['#eef3ff', '#0d8fc0', '#9c88ff'],
  },
  {
    id: 'cellar',
    label: '暗房琥珀',
    hint: '深炭录音棚，琥珀金刻度',
    swatch: ['#1a1815', '#d9a441', '#f2ece1'],
  },
  {
    id: 'abyss',
    label: '深海声谱',
    hint: '官网深色：夜空底，青紫渐变强调',
    swatch: ['#070b1a', '#6fe3ff', '#a99cff'],
  },
  {
    id: 'paper',
    label: '素纸墨青',
    hint: '纸白极简，朱砂点睛',
    swatch: ['#f7f5ef', '#1f5c5e', '#c0492f'],
  },
]

/**
 * 存储 key。**带版本号**，改默认主题时必须同时升版本。
 *
 * 原因：loadTheme 优先读 localStorage，旧值会**盖过**新默认值 ——
 * 只把 DEFAULT_THEME 改成 frost，在主上这种已存过 'abyss' 的机器上
 * 完全不生效（表现为「改了默认值却没变化」）。
 * 升版本 = 让旧偏好自然失效，新默认值得以落地；
 * 用户此后主动切的主题照常持久化，不受影响。
 *
 * - v1 → v2：初版 4 主题定稿。
 * - v2 → v3（2026-10-07）：**官网化改色**。frost/abyss 全套 token 变动
 *   （底色、accent、按钮文字色），且新增 hl / hl-btn 两个 token。
 *   不升版本的话，已存过 'frost' 的机器读到旧值就直接 applyTheme，
 *   会拿到「新 token 缺失 + 旧色值」的半吊子状态 —— 按钮渐变不生效。
 */
const STORAGE_KEY = 'bapu.theme.v3'

/**
 * 默认主题：霜蓝玻璃（frost）。
 *
 * 变更记录：
 * - 2026-10-04 初版为 frost。
 * - 同期主上试看「深海声谱（abyss）」后暂定为 abyss。
 * - 2026-10-04 主上重新拍板：「霜蓝玻璃可以作为默认皮肤」。
 *   雾白玻璃 + 冰川蓝的观感更清爽，作为首启第一印象更稳；
 *   其余三向完整保留，切换器里随时可选。
 */
export const DEFAULT_THEME: ThemeId = 'frost'

/** 从 localStorage 恢复主题；无效值回退默认主题 */
export function loadTheme(): ThemeId {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw && raw in THEMES) return raw as ThemeId
  } catch {
    /* localStorage 不可用（隐私模式）时静默降级 */
  }
  return DEFAULT_THEME
}

export function persistTheme(id: ThemeId): void {
  try {
    localStorage.setItem(STORAGE_KEY, id)
  } catch {
    /* 同上，忽略 */
  }
}

/**
 * 把 token 写入 :root。
 * 注意：不清除未在目标主题中定义的变量——四个方向 token 已对齐，
 * 缺失项会由 CSS 兜底（见 index.css 的 @defaults 段）。
 */
export function applyTheme(id: ThemeId): void {
  const tokens = THEMES[id] ?? FROST
  const root = document.documentElement
  for (const [k, v] of Object.entries(tokens)) {
    root.style.setProperty(`--${k}`, v)
  }
  root.dataset.theme = id
  root.style.colorScheme = id === 'cellar' || id === 'abyss' ? 'dark' : 'light'
}
