/**
 * ResultCard.tsx — 产出展示
 *
 * 主上需求：文件名 / 音符总数 / 各轨名与音符数 /
 *          「打开所在文件夹」/「用 MuseScore 打开」
 *
 * 设计：这是全应用唯一允许"有框卡片"的产出对象（重复项 + 产出确认）。
 * 轨名用引擎 midi_post.TRACK_DISPLAY_NAMES 的中文名。
 */

import { FileCheck2, FolderOpen, Music4, TriangleAlert } from 'lucide-react'
import { basename, formatCount, formatSeconds, programName } from '../lib/format'
import * as ipc from '../lib/ipc'
import { TRACK_DISPLAY_NAMES, type TranscribeResult } from '../lib/types'

interface Props {
  result: TranscribeResult
  /** 引擎分离方式说明（hpss / demucs / null） */
  separationLabel: string | null
  onReset: () => void
}

export function ResultCard({ result, separationLabel, onReset }: Props) {
  const fileName = basename(result.outputPath)

  /** 轨名中文化：引擎写入的是 ASCII 名（Voice/Accompaniment…） */
  const displayName = (name: string) => TRACK_DISPLAY_NAMES[name] ?? name

  return (
    <div className="animate-rise-in flex flex-col gap-3.5">
      {/* ---- 产出概要 ---- */}
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--r-sm)] bg-ok/12 text-ok">
          <FileCheck2 size={17} strokeWidth={1.9} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[14px] font-semibold text-ink" title={result.outputPath}>
            {fileName}
          </p>
          <p className="num mt-0.5 text-[11px] text-ink-faint">
            {formatCount(result.totalNotes)} 音符 · {result.tracks.length} 轨 · 耗时{' '}
            {formatSeconds(result.elapsed)}
            {separationLabel && ` · ${separationLabel}`}
          </p>
        </div>
      </div>

      {/* ---- 分轨明细 ---- */}
      <ul className="flex flex-col gap-1">
        {result.tracks.map((t, i) => {
          const pct = result.totalNotes > 0 ? (t.notes / result.totalNotes) * 100 : 0
          return (
            <li
              key={`${t.name}-${i}`}
              style={{ '--i': i } as React.CSSProperties}
              className="stagger animate-rise-in group/track rounded-[var(--r-sm)] px-2 py-1.5 transition-colors duration-150 ease-out [@media(hover:hover)and(pointer:fine)]:hover:bg-accent-soft"
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="flex min-w-0 items-baseline gap-1.5">
                  <span className="truncate text-[12.5px] font-medium text-ink">
                    {displayName(t.name)}
                  </span>
                  <span className="shrink-0 text-[10px] text-ink-faint">
                    {programName(t.program)}
                  </span>
                </span>
                <span className="num shrink-0 text-[11.5px] text-ink-dim">
                  {formatCount(t.notes)}
                </span>
              </div>
              {/* 音符占比条：一眼看出各轨分量 */}
              <div className="mt-1 h-[2px] w-full overflow-hidden rounded-full bg-[var(--stage-track)]">
                <div
                  className="h-full rounded-full bg-accent transition-[width] duration-500 ease-out"
                  style={{ width: `${Math.max(pct, t.notes > 0 ? 1.5 : 0)}%` }}
                />
              </div>
            </li>
          )
        })}
      </ul>

      {/* ---- 引擎警告 ---- */}
      {result.warnings.length > 0 && (
        <div className="flex flex-col gap-1 rounded-[var(--r-sm)] border border-[var(--warning)]/25 bg-[color-mix(in_srgb,var(--warning)_10%,transparent)] px-2.5 py-2">
          {result.warnings.map((w, i) => (
            <p key={i} className="flex items-start gap-1.5 text-[11px] leading-relaxed text-warn">
              <TriangleAlert size={11} strokeWidth={2} className="mt-[3px] shrink-0" />
              {w}
            </p>
          ))}
        </div>
      )}

      {/* ---- 导出操作 ---- */}
      <div className="flex flex-wrap gap-2 border-t border-line pt-3">
        <button
          type="button"
          onClick={() => ipc.revealInFolder(result.outputPath).catch(() => {})}
          className="btn btn-primary"
        >
          <FolderOpen size={13} strokeWidth={1.9} />
          打开所在文件夹
        </button>
        <button
          type="button"
          onClick={() => ipc.openWithMuseScore(result.outputPath).catch(() => {})}
          className="btn"
        >
          <Music4 size={13} strokeWidth={1.9} />
          用 MuseScore 打开
        </button>
        <button type="button" onClick={onReset} className="btn btn-ghost ml-auto">
          继续扒下一首
        </button>
      </div>
    </div>
  )
}
