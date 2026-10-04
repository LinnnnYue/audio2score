/**
 * links.ts — 对外链接 + 「交给系统浏览器打开」的统一入口
 *
 * ## 为什么是**两个**链接，而不是一页两视图
 * 资料库的发布链接（`workbuddy.link/p/<id>`）在浏览器里是
 * 「WorkBuddy 外壳 + iframe」，内容托管在另一台静态服务器上：
 *     <iframe src="https://workbuddy-space-static.codebuddy.work/page/<id>/<rev>/xxx.html">
 *
 * ⚠️ 实测：**外层 URL 的 hash 不会透传给 iframe**（iframe 的 src 永远不带 hash）。
 * 所以「单页面 + `#/`、`#/docs` 站内路由」在分享页里必然失效 ——
 * 打开 `…p/<id>#/docs` 只会落在 iframe 内的首页。
 *
 * 正解：把首页与文档**拆成两个独立页面**、各自发布、互相绝对链接。
 * 这样每个按钮都有自己可直接到达的目标。
 *
 * ## 为什么不用 window.open
 * 应用是无边框（decorations:false）的 Tauri 窗口，`window.open` 弹出的
 * 新窗口没有系统标题栏，用户**关不掉**。必须交给系统默认浏览器 —— 走 opener 插件。
 * 权限已在 `src-tauri/capabilities/default.json` 里授予 `opener:allow-open-url`。
 */

/** 官网首页（含「自动指向最新 Release」的下载按钮） */
export const SITE_URL = 'https://workbuddy.link/p/6gCdf83meMdb9Zoc2dZJkd'

/** 说明文档：九个旋钮 / 症状表 / 参数详解，与软件内提示同源 */
export const DOCS_URL = 'https://workbuddy.link/p/Q4EtYgaEhSHmEgOUWXS1sQ'

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
