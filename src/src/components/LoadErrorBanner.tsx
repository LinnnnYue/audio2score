/**
 * LoadErrorBanner.tsx — 引擎数据加载失败提示
 *
 * ## 为什么必须有这个组件
 * 踩坑实录（主上反馈「模式下拉菜单什么都没有」）：
 * `useWorkspace.load()` 里 `getModes()` 抛错时，错误被存进 `loadError` state，
 * 但**界面上没有任何地方渲染它**——表现就是「菜单空着，什么都不显示」，
 * 主上看不出是加载失败还是本来就没数据。
 *
 * 这类「静默失败」比直接报错更糟：用户无从判断该做什么。
 * 故凡是有 loadError 的位置，必须把它显示出来，并给出可操作的下一步。
 */

import { AlertTriangle, RefreshCw } from 'lucide-react'

interface Props {
  message: string | null
  onRetry?: () => void
  /** 上下文补充，如「模式列表」「环境信息」 */
  what?: string
}

export function LoadErrorBanner({ message, onRetry, what }: Props) {
  if (!message) return null

  return (
    <div
      role="alert"
      className="flex items-start gap-2.5 rounded-lg border px-3.5 py-3"
      style={{
        background: 'var(--danger-soft)',
        borderColor: 'var(--danger)',
      }}
    >
      <AlertTriangle
        size={15}
        strokeWidth={2.2}
        className="mt-px shrink-0"
        style={{ color: 'var(--danger)' }}
      />
      <div className="min-w-0 flex-1">
        <p className="text-[12.5px] font-medium" style={{ color: 'var(--text)' }}>
          {what ? `${what}加载失败` : '加载失败'}
        </p>
        <p
          className="mt-1 break-words text-[11.5px] leading-relaxed"
          style={{ color: 'var(--text-dim)' }}
        >
          {message}
        </p>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="mt-2 inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[11.5px] transition-[background-color,transform] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)] active:scale-[0.97]"
            style={{
              background: 'var(--surface-2)',
              border: '1px solid var(--border)',
              color: 'var(--text-dim)',
            }}
          >
            <RefreshCw size={11} strokeWidth={2.2} />
            重试
          </button>
        )}
      </div>
    </div>
  )
}
