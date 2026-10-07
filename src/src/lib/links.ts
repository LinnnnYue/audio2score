/**
 * links.ts — 对外链接 + 「交给系统浏览器打开」的统一入口
 *
 * ## 为什么是**两个**链接，而不是一页两视图
 * 资料库的发布链接（`workbuddy.link/p/<id>`）在浏览器里是
 * 「WorkBuddy 外壳 + iframe」，内容托管在另一台静态服务器上：
 *     <iframe src="https://workbuddy-space-static.codebuddy.work/page/<id>/<rev>/xxx.html">
 *
 * ⚠️ 实测（2026-10-07）：**外层 URL 的 hash 与 query 都不透传给 iframe**
 * （iframe 的 src 完全由外壳拼定，只有 path 起作用）。所以
 * `…p/<id>#docs`、`…p/<id>#s7`、`…p/<id>?view=docs` 一律落在 iframe 内的首页。
 * 站内深链接只在**直接打开静态产物**（`…codebuddy.work/page/…/xxx.html#s7`）时有效。
 *
 * 本站已把首页与文档合并为**同一份单页**（站内 `#docs` 切换视图），官网节点即唯一对外入口；
 * 原先并存的「说明文档」独立短链节点已于 2026-10-07 下架，避免两条同内容链接。
 * 经短链打开文档时，走首页顶栏的「文档」入口即可 —— 外壳限制下这是唯一可靠路径。
 *
 * ## 为什么不用 window.open
 * 应用是无边框（decorations:false）的 Tauri 窗口，`window.open` 弹出的
 * 新窗口没有系统标题栏，用户**关不掉**。必须交给系统默认浏览器 —— 走 opener 插件。
 * 权限已在 `src-tauri/capabilities/default.json` 里授予 `opener:allow-open-url`。
 */

/** 官网首页（含「自动指向最新 Release」的下载按钮） */
export const SITE_URL = 'https://workbuddy.link/p/6gCdf83meMdb9Zoc2dZJkd'

/**
 * 说明文档：与官网是同一份单页，`#docs` 即文档视图。
 *
 * 直接打开静态产物时该锚点有效；经短链访问时外壳不透传 hash，会落在首页
 * （首页顶栏有「文档」入口，一点即到）。这是短链体系的能力边界，不是缺陷。
 */
export const DOCS_URL = `${SITE_URL}#docs`

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
