/**
 * ErrorBoundary.tsx — 界面崩溃兜底
 *
 * ## 为什么必须有
 * 主上反馈「点了加装直接变黑，没进度条没提示没界面」。
 * 根因之一：渲染期未捕获的异常会让 React **卸载整棵树**，
 * 加上窗口是 `transparent: true`（无背景色即透黑），
 * 用户看到的就是一片纯黑 —— **完全无从判断发生了什么**。
 *
 * 这类「黑屏」是所有失败模式里最糟的：
 * 用户不知道是卡了、崩了，还是自己点错了，也不知道该做什么。
 *
 * ## 设计要点
 * 1. 背景色**硬编码**，不依赖主题 CSS 变量 ——
 *    因为崩溃可能发生在 applyTheme 之前，那时变量根本不存在。
 * 2. 展示错误原文（中文优先），并提供「重新加载」这一条确定可用的出路。
 * 3. 控制台保留完整堆栈，便于事后排查。
 */

import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 完整堆栈留给控制台/日志，界面只给用户看得懂的部分
    console.error('[ErrorBoundary] 界面渲染异常：', error, info.componentStack)
  }

  render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <div
        className="flex h-screen w-screen flex-col items-center justify-center gap-5 p-10"
        // 硬编码：崩溃可能早于主题变量注入，不能依赖 var(--bg)
        style={{ background: '#14182B', color: '#E8EAF2' }}
      >
        <div className="flex max-w-[640px] flex-col gap-3">
          <h1 className="text-[17px] font-semibold">界面遇到了一个错误</h1>
          <p className="text-[13px] leading-relaxed" style={{ color: '#9FB0C9' }}>
            这不是你的操作问题。可以点下面的按钮重新加载；
            若反复出现，请把下面这段信息反馈给开发者。
          </p>

          <pre
            className="max-h-[220px] overflow-auto whitespace-pre-wrap break-words rounded-lg p-3 font-mono text-[11.5px] leading-relaxed"
            style={{ background: '#0E1322', color: '#C9D4E6' }}
          >
            {error.message || String(error)}
          </pre>

          <button
            type="button"
            onClick={(): void => {
              window.location.reload()
            }}
            className="h-9 w-fit rounded-lg px-4 text-[13px] font-medium transition-transform duration-150 active:scale-[0.97]"
            style={{ background: '#3B82F6', color: '#fff' }}
          >
            重新加载
          </button>
        </div>
      </div>
    )
  }
}
