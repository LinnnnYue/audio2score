/**
 * links.ts — 对外链接 + 「交给系统浏览器打开」的统一入口
 *
 * ## 为什么集中在一处
 * 官网与说明文档是**同一个单文件站点**的两个视图（WorkBuddy 资料库托管）：
 * 站内靠 hash 路由切换，`#/` 是首页，`#/docs` 是文档页。
 * 页面改版后重新发布，链接本身不变 —— 所以这里写死是安全的。
 *
 * ## 为什么不用 window.open
 * 应用是无边框（decorations:false）的 Tauri 窗口，`window.open` 弹出的
 * 新窗口没有系统标题栏，用户**关不掉**。必须交给系统默认浏览器 —— 走 opener 插件。
 * 权限已在 `src-tauri/capabilities/default.json` 里授予 `opener:allow-open-url`。
 */

/** 资料库发布态链接：外部可直接访问，无需登录 */
const SITE = 'https://workbuddy.link/p/6gCdf83meMdb9Zoc2dZJkd'

/** 官网首页（含「自动指向最新 Release」的下载按钮） */
export const SITE_URL = `${SITE}#/`

/** 说明文档：九个旋钮 / 症状表 / 参数详解，与软件内提示同源 */
export const DOCS_URL = `${SITE}#/docs`

/** 源码仓库 */
export const GITHUB_URL = 'https://github.com/LinnnnYue/audio2score'

/** 全部版本 */
export const RELEASES_URL = `${GITHUB_URL}/releases`

/**
 * 用系统默认浏览器打开外部链接。
 *
 * 非 Tauri 宿主（例如 `npm run dev` 在浏览器里调试）时降级为 `window.open`：
 * 否则 `invoke` 必然抛错，开发态点按钮毫无反应。
 *
 * @throws 宿主拒绝打开时抛出，调用方负责给出路（见 Settings 页的失败提示）。
 */
export async function openExternal(url: string): Promise<void> {
  const inTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
  if (!inTauri) {
    window.open(url, '_blank', 'noopener,noreferrer')
    return
  }
  // 动态 import：非 Tauri 环境不必加载插件代码
  const { openUrl } = await import('@tauri-apps/plugin-opener')
  await openUrl(url)
}
