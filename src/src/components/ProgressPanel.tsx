/**
 * ProgressPanel.tsx — 处理中状态区
 *
 * 三层信息（主上验收 B2）：
 *   1. 四个阶段锚点（准备/分离 → 频谱 → 音符 → MIDI），当前阶段高亮
 *   2. 百分比大字 + 进度条 + 当前阶段说明 + 耗时秒表
 *   3. 实时日志行（引擎与第三方库输出，等宽小字，新行淡入）
 *
 * 状态覆盖：running / error / cancelled。
 *
 * 失败时额外提供「报告问题」—— 小白不知道日志在哪，也不该知道，
 * 一键把现场事实（系统编码、Python 环境、输入路径、日志）打包成可发送的文本。
 */

import { useCallback, useState } from 'react'
import {
  AlertCircle,
  Bug,
  CheckCircle2,
  FolderOpen,
  Loader2,
  Save,
  Square,
} from 'lucide-react'
import clsx from 'clsx'
import { formatSeconds } from '../lib/format'
import { copyText } from '../lib/clipboard'
import * as ipc from '../lib/ipc'
import { STAGE_ANCHORS, activeAnchorIndex, type TaskState } from '../lib/useTranscribeTask'
import { Tooltip } from './Tooltip'

interface Props {
  task: TaskState
  onCancel: () => void
  /** 引擎下发的阶段中文标签，来自 get_modes().stageLabels */
  stageLabels: Record<string, string>
  /** 本次任务的主输入路径，进诊断报告用（可为空：提交前就失败时没有） */
  inputPath?: string | null
  /** 当前模式中文名，进诊断报告用 */
  modeLabel?: string | null
}

/** 诊断报告：一次生成、多处复用（复制 / 保存 / 预览） */
interface ReportState {
  /** 报告正文；null = 尚未生成 */
  text: string | null
  /** 生成或写入进行中 */
  busy: boolean
  /** 一行操作反馈；null = 不显示 */
  note: { tone: 'ok' | 'bad'; text: string } | null
  /** 已落盘的绝对路径 */
  savedPath: string | null
}

const REPORT_INITIAL: ReportState = { text: null, busy: false, note: null, savedPath: null }

/** 文件名用的时间戳，本地时区 */
function timeStamp(): string {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`
}

function errText(e: unknown): string {
  return typeof e === 'string' ? e : e instanceof Error ? e.message : String(e)
}

export function ProgressPanel({ task, onCancel, stageLabels, inputPath, modeLabel }: Props) {
  const { status, stage, pct, message, logs, error, elapsed } = task
  const running = status === 'running'
  const activeIdx = activeAnchorIndex(stage, status)
  const showPct = Math.round(pct * 100)

  const [rep, setRep] = useState<ReportState>(REPORT_INITIAL)

  /** 采集现场事实。Rust 侧补齐版本号，前端只交界面才知道的部分。 */
  const ensureReport = useCallback(async () => {
    const text = await ipc.buildDiagnosticReport({
      logs: logs.map((l) => l.text),
      errorMessage: error?.message ?? null,
      errorDetail: error?.detail ?? null,
      inputPath: inputPath ?? null,
      mode: modeLabel ?? null,
      generatedAt: new Date().toLocaleString('zh-CN', { hour12: false }),
      appVersion: null,
    })
    setRep((s) => ({ ...s, text }))
    return text
  }, [logs, error, inputPath, modeLabel])

  /** 主路径：能直报就直报，不能就复制 —— 两者都不需要小白知道日志在哪 */
  const onReport = useCallback(async () => {
    setRep((s) => ({ ...s, busy: true, note: null }))
    try {
      const text = await ensureReport()
      if ((await ipc.submitDiagnosticReport(text)).status === 'sent') {
        setRep((s) => ({ ...s, busy: false, note: { tone: 'ok', text: '报告已提交，感谢反馈' } }))
        return
      }
      const copied = await copyText(text)
      setRep((s) => ({
        ...s,
        busy: false,
        note: copied
          ? { tone: 'ok', text: '报告已复制，粘贴发给开发者即可' }
          : { tone: 'bad', text: '复制失败，请改用「保存报告」' },
      }))
    } catch (e) {
      setRep((s) => ({
        ...s,
        busy: false,
        note: { tone: 'bad', text: `生成报告失败：${errText(e)}` },
      }))
    }
  }, [ensureReport])

  /** 备用路径：落盘并直接定位到文件，省掉「文件存哪了」这一问 */
  const onSaveReport = useCallback(async () => {
    setRep((s) => ({ ...s, busy: true, note: null }))
    try {
      const text = await ensureReport()
      const target = await ipc.pickTextSavePath(`扒谱助手-诊断报告-${timeStamp()}.txt`)
      if (!target) {
        setRep((s) => ({ ...s, busy: false })) // 用户取消：静默
        return
      }
      const saved = await ipc.saveDiagnosticReport(target, text)
      setRep((s) => ({ ...s, busy: false, savedPath: saved, note: { tone: 'ok', text: '报告已保存' } }))
      await ipc.revealInFolder(saved)
    } catch (e) {
      setRep((s) => ({ ...s, busy: false, note: { tone: 'bad', text: `保存失败：${errText(e)}` } }))
    }
  }, [ensureReport])

  const onOpenFolder = useCallback(() => {
    if (rep.savedPath) void ipc.revealInFolder(rep.savedPath)
  }, [rep.savedPath])

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
          {/* 失败时不再重复错误文案——下方红框已完整展示 user_message，
              这里只提示失败发生在哪个阶段，避免同一句话出现两次 */}
          <p className="mt-1 truncate text-[11.5px] text-ink-faint" title={message}>
            {status === 'error'
              ? `中断于「${stageLabels[stage ?? ''] ?? '未知阶段'}」`
              : message || '—'}
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

      {/* ---- 问题反馈：一键取证 ---- */}
      {status === 'error' && error && (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={onReport}
              disabled={rep.busy}
              className="btn btn-primary"
            >
              {rep.busy ? (
                <Loader2 size={12} strokeWidth={2} className="animate-spin" />
              ) : (
                <Bug size={12} strokeWidth={2} />
              )}
              报告问题
            </button>
            <button
              type="button"
              onClick={onSaveReport}
              disabled={rep.busy}
              className="btn btn-ghost"
            >
              <Save size={12} strokeWidth={2} />
              保存报告
            </button>
            {rep.savedPath && (
              <button type="button" onClick={onOpenFolder} className="btn btn-ghost">
                <FolderOpen size={12} strokeWidth={2} />
                打开文件夹
              </button>
            )}
            {rep.note && (
              <span
                role="status"
                className={clsx(
                  'animate-pop-in text-[11px]',
                  rep.note.tone === 'ok' ? 'text-ink-dim' : 'text-bad',
                )}
              >
                {rep.note.text}
              </span>
            )}
          </div>

          {/* 透明：发出去的是什么，点开就能看 */}
          {rep.text && (
            <details className="group/rep">
              <summary className="cursor-pointer list-none text-[10.5px] text-ink-faint transition-colors duration-150 ease-out [[@media(hover:hover)_and_(pointer:fine)]]:hover:text-ink-dim">
                查看报告内容
              </summary>
              <pre className="num mt-1.5 max-h-[200px] overflow-y-auto whitespace-pre-wrap break-all rounded-[var(--r-sm)] border border-line bg-[var(--surface-3)] px-2.5 py-2 text-[10px] leading-relaxed text-ink-dim">
                {rep.text}
              </pre>
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
