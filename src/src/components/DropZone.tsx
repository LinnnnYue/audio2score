/**
 * DropZone.tsx — 音频拖放 / 点击选择区
 *
 * 两种入口：
 *  1. 点击 → 走 Tauri dialog 的 pickAudioFile
 *  2. 拖拽 → 走 webview 的 onDragDropEvent（拿到的是真实路径）
 *
 * 拖拽高亮态用 data-dragging 属性驱动 CSS（见 index.css .dropzone），
 * 不用 React state 反复重渲染。
 *
 * multiple 模式（基本扒谱多轨 / 已分离直入）可接收多个文件，
 * 超出配额时给出明确提示而非静默丢弃。
 */

import { FileAudio, FolderOpen, Upload } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import clsx from 'clsx'
import * as ipc from '../lib/ipc'
import { basename, ellipsisPath, formatBytes, formatDuration } from '../lib/format'
import type { ProbeResult } from '../lib/types'

export interface DroppedFile {
  path: string
  name: string
  size: number
  duration: number
}

interface Props {
  files: DroppedFile[]
  onFiles: (files: DroppedFile[]) => void
  multiple?: boolean
  /** multiple 时的最大文件数 */
  max?: number
  disabled?: boolean
  /** 主输入的槽位标签，如「人声」「伴奏」 */
  slotLabel?: string
  compact?: boolean
  /**
   * 是否为当前接收拖放的槽位。webview 的 onDragDropEvent 是**整窗全局**的
   * （事件里没有落点 DOM 信息），同页多个 DropZone 必须由父级指定唯一 armed 槽，
   * 否则一次 drop 会写进所有槽位。单槽页面恒为 true。
   */
  armed?: boolean
}

const inTauri = (): boolean =>
  typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

export function DropZone({
  files,
  onFiles,
  multiple = false,
  max = 1,
  disabled = false,
  slotLabel,
  compact = false,
  armed = true,
}: Props) {
  const [dragging, setDragging] = useState(false)
  /** 探测失败的文件（引擎侧给出中文文案） */
  const [probeError, setProbeError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const zoneRef = useRef<HTMLDivElement>(null)
  /** enter/leave 会成对抖动，用计数抵消 */
  const dragDepth = useRef(0)

  const accept = useCallback(
    async (paths: string[]) => {
      if (!paths.length) return
      setBusy(true)
      setProbeError(null)
      const slots = multiple ? paths.slice(0, max) : [paths[0]]
      if (multiple && paths.length > max) {
        setProbeError(`最多接收 ${max} 个文件，已忽略多余的 ${paths.length - max} 个。`)
      }
      const probed: DroppedFile[] = []
      for (const p of slots) {
        try {
          const r: ProbeResult = await ipc.probeAudio(p)
          probed.push({
            path: r.path || p,
            name: r.name || basename(p),
            size: r.size,
            duration: r.duration,
          })
        } catch (err) {
          // 引擎给的 user_message 原样透出，不二次包装
          setProbeError(typeof err === 'string' ? err : String(err))
        }
      }
      if (probed.length) onFiles(probed)
      setBusy(false)
    },
    [max, multiple, onFiles],
  )

  /* ---- webview 原生拖放 ---- */
  useEffect(() => {
    if (!inTauri()) return
    let unlisten: (() => void) | null = null
    let disposed = false

    import('@tauri-apps/api/webview')
      .then(({ getCurrentWebview }) => getCurrentWebview().onDragDropEvent((e) => {
        const p = e.payload
        if (p.type === 'enter') {
          dragDepth.current = 1
          // 非 armed 槽位不高亮，但仍需吞掉 drop，避免浏览器默认行为
          if (armed && !disabled) setDragging(true)
        } else if (p.type === 'over') {
          // 仅位置变化，不必重渲染
        } else if (p.type === 'leave') {
          dragDepth.current = 0
          setDragging(false)
        } else if (p.type === 'drop') {
          dragDepth.current = 0
          setDragging(false)
          if (armed && !disabled) void accept(p.paths)
        }
      }))
      .then((u) => {
        if (disposed) u()
        else unlisten = u
      })
      .catch((err) => console.warn('[DropZone] 拖放监听不可用', err))

    return () => {
      disposed = true
      unlisten?.()
    }
  }, [accept, armed, disabled])

  /* ---- 阻止浏览器默认的 drop（webview 未启用时至少不刷屏） ---- */
  useEffect(() => {
    const el = zoneRef.current
    if (!el) return
    const stop = (e: DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
    }
    el.addEventListener('dragover', stop)
    el.addEventListener('drop', stop)
    return () => {
      el.removeEventListener('dragover', stop)
      el.removeEventListener('drop', stop)
    }
  }, [])

  const onPick = async () => {
    if (disabled || busy) return
    const picked = await ipc.pickAudioFile(multiple)
    if (picked.length) await accept(picked)
  }

  const filled = files.length > 0
  const isMulti = multiple

  return (
    <div className="w-full">
      <div
        ref={zoneRef}
        data-dragging={dragging}
        onClick={onPick}
        role="button"
        tabIndex={0}
        aria-disabled={disabled}
        aria-label={isMulti ? '选择音频文件' : '选择音频文件'}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            void onPick()
          }
        }}
        className={clsx(
          'dropzone group relative flex w-full cursor-pointer flex-col justify-center',
          'rounded-[var(--r-md)] border-[1.5px] border-dashed border-line',
          compact ? 'gap-1.5 px-4 py-5' : 'gap-2.5 px-5 py-9',
          filled && 'border-solid border-line',
          disabled && 'pointer-events-none opacity-45',
          'active:scale-[0.995]',
        )}
      >
        {/* 空态 */}
        {!filled && (
          <>
            <div className="flex items-center gap-3">
              <span
                className={clsx(
                  'flex items-center justify-center rounded-[var(--r-sm)]',
                  'bg-accent-soft text-accent',
                  compact ? 'h-8 w-8' : 'h-10 w-10',
                  'transition-transform duration-200 ease-out',
                  '[@media(hover:hover)and(pointer:fine)]:group-hover:scale-[1.06]',
                )}
              >
                {dragging ? <Upload size={compact ? 15 : 18} strokeWidth={1.9} /> : <FileAudio size={compact ? 15 : 18} strokeWidth={1.9} />}
              </span>
              <div className="min-w-0">
                <p className="text-[13.5px] font-medium text-ink">
                  {dragging ? '松手放入' : `拖入音频${slotLabel ? ` · ${slotLabel}` : ''}`}
                </p>
                <p className="mt-0.5 text-[11.5px] text-ink-faint">
                  或点击选择文件
                  {isMulti && ` · 最多 ${max} 个`}
                </p>
              </div>
            </div>
            {!compact && (
              <p className="text-[10.5px] leading-relaxed text-ink-faint">
                支持 wav / mp3 / flac / m4a / ogg 等格式，其余需 ffmpeg 转码
              </p>
            )}
          </>
        )}

        {/* 已填充 */}
        {filled && (
          <div className="w-full animate-pop-in">
            <ul className="flex flex-col gap-1.5">
              {files.map((f, i) => (
                <li
                  key={f.path}
                  style={{ '--i': i } as React.CSSProperties}
                  className="stagger animate-rise-in flex items-center gap-2.5"
                >
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--r-sm)] bg-accent-soft text-accent">
                    <FileAudio size={13} strokeWidth={1.9} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[12.5px] font-medium text-ink" title={f.path}>
                      {slotLabel && isMulti ? `${slotLabel} · ` : ''}
                      {f.name}
                    </span>
                    <span className="num block truncate text-[10.5px] text-ink-faint" title={f.path}>
                      {formatDuration(f.duration)} · {formatBytes(f.size)}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
            {!disabled && (
              <p className="mt-2 flex items-center gap-1 text-[10.5px] text-ink-faint opacity-0 transition-opacity duration-150 ease-out [@media(hover:hover)and(pointer:fine)]:group-hover:opacity-100">
                <FolderOpen size={11} strokeWidth={1.8} />
                点击更换
              </p>
            )}
          </div>
        )}

        {busy && (
          <span className="absolute right-3 top-3 text-[10.5px] text-ink-faint">读取中…</span>
        )}
      </div>

      {/* 路径与错误：失败文案原样透出引擎的 user_message */}
      {probeError && (
        <p className="mt-2 flex items-start gap-1.5 text-[11.5px] leading-relaxed text-bad">
          <span className="mt-[3px] h-1 w-1 shrink-0 rounded-full bg-bad" />
          {probeError}
        </p>
      )}
      {filled && !probeError && (
        <p className="num mt-1.5 truncate text-[10.5px] text-ink-faint" title={files[0].path}>
          {ellipsisPath(files[0].path, 64)}
          {files.length > 1 && ` 等 ${files.length} 个文件`}
        </p>
      )}
    </div>
  )
}
