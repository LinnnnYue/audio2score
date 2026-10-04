/**
 * ProgressPanel.tsx — 处理中状态区
 *
 * 三层信息（主上验收 B2）：
 *   1. 四个阶段锚点（准备/分离 → 频谱 → 音符 → MIDI），当前阶段高亮
 *   2. 百分比大字 + 进度条 + 当前阶段说明 + 耗时秒表
 *   3. 实时日志行（引擎与第三方库输出，等宽小字，新行淡入）
 *
 * 状态覆盖：running / error / cancelled。
 */

import { AlertCircle, CheckCircle2, Loader2, Square } from 'lucide-react'
import clsx from 'clsx'
import { formatSeconds } from '../lib/format'
import { STAGE_ANCHORS, activeAnchorIndex, type TaskState } from '../lib/useTranscribeTask'
import { Tooltip } from './Tooltip'

interface Props {
  task: TaskState
  onCancel: () => void
  /** 引擎下发的阶段中文标签，来自 get_modes().stageLabels */
  stageLabels: Record<string, string>
}

export function ProgressPanel({ task, onCancel, stageLabels }: Props) {
  const { status, stage, pct, message, logs, error, elapsed } = task
  const running = status === 'running'
  const activeIdx = activeAnchorIndex(stage, status)
  const showPct = Math.round(pct * 100)

  return (
    <div className="flex flex-col gap-3.5">
      {/* ---- 阶段锚点 ---- */}
      <ol className="flex items-stretch gap-[3px]" aria-label="处理阶段">
        {STAGE_ANCHORS.map((a, i) => {
          const done = i < activeIdx
          const active = i === activeIdx && running
          return (
            <li key={a.key} className="min-w-0 flex-1">
              <div
                className={clsx(
                  'h-[3px] w-full rounded-full',
                  'transition-colors duration-300 ease-out',
                  done || active ? 'bg-accent' : 'bg-[var(--stage-track)]',
                )}
              />
              <span
                className={clsx(
                  'mt-1.5 block truncate text-[10.5px] leading-tight',
                  'transition-colors duration-200 ease-out',
                  active ? 'font-medium text-accent' : done ? 'text-ink-dim' : 'text-ink-faint',
                )}
              >
                {stageLabels[a.key] ?? a.label}
              </span>
            </li>
          )
        })}
      </ol>

      {/* ---- 状态头 ---- */}
      <div className="flex items-end justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            {running && (
              <Loader2 size={14} strokeWidth={2} className="shrink-0 animate-spin text-accent" />
            )}
            {status === 'done' && (
              <CheckCircle2 size={14} strokeWidth={2} className="shrink-0 text-ok" />
            )}
            {(status === 'error') && (
              <AlertCircle size={14} strokeWidth={2} className="shrink-0 text-bad" />
            )}
            <span
              className={clsx(
                'truncate text-[13px] font-medium',
                running && 'text-ink',
                status === 'done' && 'text-ok',
                status === 'error' && 'text-bad',
              )}
            >
              {status === 'done'
                ? '扒谱完成'
                : status === 'error'
                  ? '扒谱失败'
                  : (stageLabels[stage ?? ''] ?? '准备中')}
            </span>
          </div>
          <p className="mt-1 truncate text-[11.5px] text-ink-faint" title={message}>
            {message || '—'}
          </p>
        </div>

        <div className="flex shrink-0 items-baseline gap-2">
          <span
            className={clsx(
              'num text-[26px] font-semibold leading-none tracking-[-0.02em]',
              status === 'error' ? 'text-bad' : running ? 'text-accent' : 'text-ink',
            )}
          >
            {status === 'done' ? 100 : showPct}
            <span className="ml-0.5 text-[13px] font-normal text-ink-faint">%</span>
          </span>
          <Tooltip content="本次任务已耗时">
            <span className="num text-[11px] text-ink-faint">{formatSeconds(elapsed)}</span>
          </Tooltip>
        </div>
      </div>

      {/* ---- 进度条 ---- */}
      <div className="relative h-[5px] w-full overflow-hidden rounded-full bg-[var(--stage-track)]">
        <div
          className={clsx(
            'absolute inset-y-0 left-0 rounded-full',
            status === 'error' ? 'bg-bad' : 'bg-accent',
            'transition-[width] duration-300 ease-out',
          )}
          style={{ width: `${status === 'done' ? 100 : showPct}%` }}
        />
        {/* 运行中的高光扫掠：克制的一点点光，不做霓虹 */}
        {running && (
          <div className="absolute inset-0 overflow-hidden">
            <div className="h-full w-1/4 animate-sweep bg-gradient-to-r from-transparent via-[var(--accent-soft)] to-transparent" />
          </div>
        )}
      </div>

      {/* ---- 错误详情：引擎 user_message 原样展示 ---- */}
      {status === 'error' && error && (
        <div className="animate-pop-in rounded-[var(--r-sm)] border border-[var(--danger)]/25 bg-bad-soft px-3 py-2.5">
          <p className="whitespace-pre-line text-[12px] leading-relaxed text-bad">{error.message}</p>
          {error.detail && (
            <details className="group/det mt-1.5">
              <summary className="cursor-pointer list-none text-[10.5px] text-ink-faint transition-colors duration-150 ease-out [[@media(hover:hover)_and_(pointer:fine)]]:hover:text-ink-dim">
                技术细节
              </summary>
              <p className="num mt-1 break-all text-[10px] leading-relaxed text-ink-faint">
                {error.detail}
              </p>
            </details>
          )}
        </div>
      )}

      {/* ---- 日志 ---- */}
      {logs.length > 0 && (
        <div className="flex min-h-0 flex-col">
          <div className="mb-1 flex items-center justify-between">
            <span className="text-[10.5px] font-medium uppercase tracking-wider text-ink-faint">
              运行日志
            </span>
            <span className="num text-[10px] text-ink-faint">{logs.length} 行</span>
          </div>
          <div
            className={clsx(
              'max-h-[132px] overflow-y-auto rounded-[var(--r-sm)]',
              'border border-line bg-[var(--surface-3)] px-2.5 py-1.5',
            )}
          >
            <ul className="flex flex-col gap-[3px]">
              {logs.slice(-40).map((l) => (
                <li
                  key={l.id}
                  className="animate-log-in num flex gap-2 text-[10.5px] leading-relaxed"
                >
                  <span className="w-[3px] shrink-0 rounded-full bg-[var(--accent-border)]" />
                  <span className="min-w-0 break-all text-ink-dim">{l.text}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {/* ---- 取消 ---- */}
      {running && (
        <button type="button" onClick={onCancel} className="btn btn-danger self-start">
          <Square size={12} strokeWidth={2} />
          取消任务
        </button>
      )}
    </div>
  )
}
