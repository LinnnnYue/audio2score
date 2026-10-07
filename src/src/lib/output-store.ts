/**
 * output-store.ts — 「默认输出目录」的本机设置
 *
 * ## 为什么要有
 * 扒谱默认把 .mid 放在**源音频同目录**。多数情况下这是对的，
 * 但小白用户的音频常散落在下载夹、桌面、微信接收目录 —— 扒完，
 * 文件就跟着散在那儿，回头找不到。主上原话：「输出位置也要有
 * 自定义目录的设置」。于是给一个**固定落点**：一次设好，之后每次
 * 扒完都往那儿放，不必每回都点「另存为」。
 *
 * ## 三种状态
 *   未设置（''）→ 引擎默认：与源音频同目录
 *   已设置       → 固定目录 + 与源音频同名的 .mid
 *   单次指定      → 页面上的「另存为」，优先级最高（不改本设置）
 *
 * ## 为什么自带订阅
 * 与 onboarding-store 同样是纯 localStorage。区别在于这份要被**功能页实时读到**：
 * 设置页改完目录，功能页底部那句「将输出到 …」必须当场跟着变。
 * 同页内写 localStorage 不会触发 `storage` 事件（那只跨标签页），
 * 所以只能自己维护一份极简订阅。
 */

import { useSyncExternalStore } from 'react'

const STORE_KEY = 'bapu.output.dir.v1'

/** 订阅者集合。同页内改值后逐一声明。 */
const listeners = new Set<() => void>()

/** 当前的默认输出目录。`''` = 未设置（走引擎默认：与源音频同目录）。 */
export function getDefaultOutputDir(): string {
  try {
    return localStorage.getItem(STORE_KEY) ?? ''
  } catch {
    // 隐私模式 / 存储被禁用。当作未设置：退回到「与源音频同目录」，
    // 这是不会出错的那一侧。静默吞掉会让人以为设置生效了。
    return ''
  }
}

/** 写入默认输出目录。传 `''` 表示恢复默认（与源音频同目录）。 */
export function setDefaultOutputDir(dir: string): void {
  try {
    if (dir) localStorage.setItem(STORE_KEY, dir)
    else localStorage.removeItem(STORE_KEY)
  } catch {
    /* 写不进去不影响本次上屏，只是下次开机会丢 —— 没有更轻的补救手段 */
  }
  listeners.forEach((l) => l())
}

/** 订阅变更。返回退订函数。 */
export function subscribeOutputDir(cb: () => void): () => void {
  listeners.add(cb)
  return () => {
    listeners.delete(cb)
  }
}

/** React 侧读值。服务端快照恒为 `''`（本项目无 SSR，仅为类型完备）。 */
export function useDefaultOutputDir(): string {
  return useSyncExternalStore(subscribeOutputDir, getDefaultOutputDir, () => '')
}

/**
 * 把目录与文件名拼成引擎要的完整路径。
 *
 * 分隔符跟随目录自身的写法：Tauri 的目录选择器在 Windows 上给的是
 * `D:\音乐`，但用户也可能手改过配置。不做「一律换成正斜杠」这种自作主张 ——
 * 引擎侧两种都收，保持原样最不容易出错。结尾多余的斜杠会先削掉。
 */
export function joinOutputPath(dir: string, fileName: string): string {
  const sep = dir.includes('/') && !dir.includes('\\') ? '/' : '\\'
  return `${dir.replace(/[\\/]+$/, '')}${sep}${fileName}`
}
