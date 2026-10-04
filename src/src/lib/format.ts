/**
 * format.ts — 展示层格式化
 *
 * 原则：数值一律等宽对齐（.num 类），单位显式写出，不让用户猜。
 */

/** 秒 → m:ss */
export function formatDuration(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return '—'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

/** 秒 → 0.0s（短标签用） */
export function formatSeconds(sec: number): string {
  if (!Number.isFinite(sec)) return '—'
  return `${sec.toFixed(1)}s`
}

/** 字节 → 人类可读 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let v = bytes
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

/** 千分位 */
export function formatCount(n: number): string {
  return n.toLocaleString('zh-CN')
}

/** 只取文件名（跨平台分隔符） */
export function basename(path: string): string {
  return path.split(/[\\/]/).pop() ?? path
}

/** 去掉扩展名 */
export function stripExt(name: string): string {
  return name.replace(/\.[^.]+$/, '')
}

/** 中间省略的长路径，保留首尾（用于 tooltip 全路径显示） */
export function ellipsisPath(path: string, max = 52): string {
  if (path.length <= max) return path
  const head = Math.ceil((max - 3) / 2)
  const tail = Math.floor((max - 3) / 2)
  return `${path.slice(0, head)}…${path.slice(path.length - tail)}`
}

/** General MIDI program 号 → 中文乐器名（覆盖引擎会输出的常见 program） */
const GM_NAMES: Record<number, string> = {
  0: '钢琴',
  24: '尼龙弦吉他',
  25: '钢弦吉他',
  26: '电吉他',
  32: '电贝斯',
  33: '电贝斯（拾音）',
  40: '小提琴',
  41: '中提琴',
  42: '大提琴',
  43: '竖琴',
  48: '弦乐合奏',
  52: '合唱人声',
  53: '独唱人声',
  73: '长笛',
  80: '主音合成器',
  98: '合成器',
}

export function programName(program: number): string {
  return GM_NAMES[program] ?? `GM ${program}`
}
