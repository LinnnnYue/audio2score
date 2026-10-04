/**
 * EnvBanner.tsx — 引擎环境自检条
 *
 * 数据来自 engine separator.capabilities()（经 get_env_info）。
 * 只在有降级项时出现——全部正常时静默，避免"永远占一行的噪音"。
 * 降级文案直接用引擎给的 notes 原文，不二次改写。
 */

import { Cpu, TriangleAlert, X } from 'lucide-react'
import { useState } from 'react'
import clsx from 'clsx'
import type { EnvInfo } from '../lib/types'

interface Props {
  env: EnvInfo | null
  /** 当前模式是否需要分离（决定 Demucs 缺失是否要拦） */
  needsSeparation: boolean
}

export function EnvBanner({ env, needsSeparation }: Props) {
  const [dismissed, setDismissed] = useState(false)

  if (!env || dismissed) return null

  // 挑真正影响本次操作的项
  const blocking: string[] = []
  if (needsSeparation && !env.demucs) {
    blocking.push('未安装 Demucs，人声与伴奏将降级为中频分离，音质明显下降。')
  }
  if (needsSeparation && !env.cuda) {
    blocking.push('未检测到 CUDA，分离将在 CPU 上运行，3 分钟歌曲可能需要数分钟。')
  }
  const notes = [...blocking, ...env.notes.filter((n) => !blocking.includes(n))]

  // 全部正常：不显示
  const isFullHealth = env.demucs && env.cuda && env.ffmpeg
  if (isFullHealth || notes.length === 0) return null

  return (
    <div
      className={clsx(
        'animate-rise-in flex items-start gap-2.5 border-b px-5 py-2.5',
        blocking.length > 0
          ? 'border-[var(--warning)]/20 bg-[color-mix(in_srgb,var(--warning)_8%,transparent)]'
          : 'border-line bg-[var(--surface-3)]',
      )}
      role="status"
    >
      <TriangleAlert
        size={13}
        strokeWidth={2}
        className={clsx('mt-[2px] shrink-0', blocking.length > 0 ? 'text-warn' : 'text-ink-faint')}
      />
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11.5px] leading-relaxed text-ink-dim">
          <span className="font-medium text-ink">
            {blocking.length > 0 ? '引擎能力受限' : '环境提示'}
          </span>
          <span className="text-ink-faint">{notes.join(' ')}</span>
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <span className="num flex items-center gap-1 text-[10.5px] text-ink-faint">
          <Cpu size={11} strokeWidth={1.8} />
          {env.device.toUpperCase()}
        </span>
        <button
          type="button"
          onClick={() => setDismissed(true)}
          aria-label="关闭提示"
          className="flex h-[22px] w-[22px] items-center justify-center rounded-[4px] text-ink-faint transition-colors duration-150 ease-out active:scale-[0.94] [@media(hover:hover)and(pointer:fine)]:hover:bg-accent-soft"
        >
          <X size={11} strokeWidth={2} />
        </button>
      </div>
    </div>
  )
}
