/**
 * clipboard.ts — 复制文本到剪贴板（双通道降级）
 *
 * ## 为什么不能只用 navigator.clipboard
 * 它是异步 API，要求 secure context 与「用户手势」上下文。在 WebView 里
 * 两个条件通常都满足，但仍存在失败面：权限策略、窗口未聚焦、
 * 或者用户手势链被前置的 await 打断（本模块的调用点就是
 * 「先 await 生成报告、再写入剪贴板」，很容易踩到这条）。
 *
 * 而「一键反馈问题」是小白用户唯一会走的路径 —— 它失败就等于功能不存在。
 * 故先试现代 API，失败则回落到 `execCommand('copy')`：
 * 后者虽已标记废弃，但在所有 WebView 里都稳定可用。
 */

/** 复制成功返回 true。调用方据此决定要不要提示「请手动复制」。 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // 继续走兜底通道
  }

  try {
    const ta = document.createElement('textarea')
    ta.value = text
    // 放到视口外：避免页面滚动跳动与一帧的闪烁
    ta.style.position = 'fixed'
    ta.style.top = '-9999px'
    ta.style.left = '-9999px'
    ta.style.opacity = '0'
    ta.setAttribute('readonly', '')
    document.body.appendChild(ta)
    ta.select()
    ta.setSelectionRange(0, text.length)
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}
