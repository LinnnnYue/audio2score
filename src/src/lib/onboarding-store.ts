/**
 * onboarding-store.ts — 「是否看过新手指引」的本机标记
 *
 * ## 为什么单独成一个文件
 * 这份状态与 React 无关（就是 localStorage 里的一个标记），
 * 却既被 App.tsx 读、又被 Onboarding.tsx 写。放在组件文件里导出，
 * 一是不符合分层惯例，二是会破坏组件的 Fast Refresh
 *（一个文件同时导出组件与普通函数时，改这个文件会让整棵子树重挂载）。
 * 故收敛进 lib/ —— 与 ipc.ts / format.ts 同级。
 *
 * ## 为什么要版本号
 * 指引内容会随功能变化。将来大改一次（比如新增一整节），
 * 把 v1 升到 v2 就能让老用户再看一遍新的；否则他们永远停留在
 * 「我看过了」的旧世界里，看不到新写的内容。
 */

const STORE_KEY = 'bapu.onboarding.v1'

/** 是否已经看过新手指引。用于「只在首次自动弹」。 */
export function hasSeenOnboarding(): boolean {
  try {
    return localStorage.getItem(STORE_KEY) === '1'
  } catch {
    // 隐私模式 / 存储被禁用时可能抛异常。
    // 抛了就当没看过——多弹一次无害；静默吞掉会让人永远看不到指引。
    return false
  }
}

/** 记下「已看过」。写不进去也不影响本次会话。 */
export function markOnboardingSeen(): void {
  try {
    localStorage.setItem(STORE_KEY, '1')
  } catch {
    /* 同上 */
  }
}
