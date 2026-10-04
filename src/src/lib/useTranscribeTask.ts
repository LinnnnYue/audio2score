/**
 * useTranscribeTask.ts — 扒谱任务的状态机
 *
 * 状态流转（主上验收 B3/B4 的核心）：
 *   idle ──start()──> running ──done事件──> done
 *     ↑                   │
 *     │                   ├──error事件──> error
 *     └───reset()─────────┤
 *                         └──cancel()──> error(取消文案) ──reset()──> idle
 *
 * 并发防护（B4）：running 期间 start() 直接拒绝，不发第二次 invoke。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import * as ipc from './ipc'
import type { ProgressEvent, Stage, TranscribeRequest, TranscribeResult } from './types'

export type TaskStatus = 'idle' | 'running' | 'done' | 'error'

/** 进度区展示的四个锚点。引擎五阶段里 prepare+separate 归为「分离人声」。 */
export interface StageAnchor {
  key: string
  label: string
  /** 引擎原始阶段名，用于判断当前是否激活 */
  stages: Stage[]
}

export const STAGE_ANCHORS: StageAnchor[] = [
  { key: 'split', label: '分离人声', stages: ['prepare', 'separate'] },
  { key: 'spectrum', label: '分析频谱', stages: ['spectrum'] },
  { key: 'track', label: '追踪音符', stages: ['track'] },
  { key: 'export', label: '生成 MIDI', stages: ['export'] },
]

export interface LogLine {
  id: number
  text: string
  /** 该行属于哪个阶段，用于左侧刻度着色 */
  stage: Stage | null
}

export interface TaskError {
  message: string
  detail: string
}

export interface TaskState {
  status: TaskStatus
  taskId: string | null
  stage: Stage | null
  /** 0~1 */
  pct: number
  /** 引擎给的当前阶段说明 */
  message: string
  logs: LogLine[]
  result: TranscribeResult | null
  error: TaskError | null
  /** 耗时（秒），running 时按秒累加 */
  elapsed: number
}

const INITIAL: TaskState = {
  status: 'idle',
  taskId: null,
  stage: null,
  pct: 0,
  message: '',
  logs: [],
  result: null,
  error: null,
  elapsed: 0,
}

const MAX_LOGS = 300

export interface UseTask extends TaskState {
  isRunning: boolean
  start: (req: TranscribeRequest) => Promise<void>
  cancel: () => Promise<void>
  reset: () => void
}

export function useTranscribeTask(): UseTask {
  const [state, setState] = useState<TaskState>(INITIAL)
  /** 事件回调里读最新的 taskId，避免闭包捕获旧值 */
  const taskIdRef = useRef<string | null>(null)
  const runningRef = useRef(false)
  const logSeq = useRef(0)
  const startedAt = useRef(0)
  /** 组件卸载后拒绝所有 setState */
  const aliveRef = useRef(true)

  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
    }
  }, [])

  /* ---- 事件订阅：整个生命周期只订一次 ---- */
  useEffect(() => {
    let unlisten: (() => void) | null = null
    let disposed = false

    const set = (fn: (s: TaskState) => TaskState) => {
      if (aliveRef.current) setState(fn)
    }

    ipc
      .subscribeAll({
        progress: (p: ProgressEvent) => {
          // 引擎可能在 invoke 返回 taskId 之前就推送首帧事件（冷启动竞态），
          // 故未拿到 taskId 时不过滤；拿到后才按 taskId 严格匹配。
          if (taskIdRef.current && p.taskId !== taskIdRef.current) return
          set((s) => ({
            ...s,
            stage: p.stage,
            pct: p.pct,
            message: p.message,
            logs: appendLog(s.logs, p.message, p.stage, logSeq),
          }))
        },
        log: (l) => {
          if (taskIdRef.current && l.taskId !== taskIdRef.current) return
          set((s) => ({ ...s, logs: appendLog(s.logs, l.message, s.stage, logSeq) }))
        },
        done: (d) => {
          if (taskIdRef.current && d.taskId !== taskIdRef.current) return
          runningRef.current = false
          set((s) => ({
            ...s,
            status: 'done',
            pct: 1,
            stage: 'export',
            result: d.result,
            elapsed: d.result?.elapsed ?? 0,
            message: '完成',
            logs: appendLog(s.logs, '扒谱完成，已导出 MIDI', 'export', logSeq),
          }))
        },
        error: (e) => {
          if (taskIdRef.current && e.taskId !== taskIdRef.current) return
          runningRef.current = false
          set((s) => ({
            ...s,
            status: 'error',
            error: { message: e.message, detail: e.detail },
            message: e.message,
          }))
        },
      })
      .then((u) => {
        if (disposed) u()
        else unlisten = u
      })
      .catch((err) => {
        console.error('[task] 事件订阅失败', err)
      })

    return () => {
      disposed = true
      unlisten?.()
    }
  }, [])

  /* ---- running 时秒表 ---- */
  useEffect(() => {
    if (state.status !== 'running') return
    const t = window.setInterval(() => {
      setState((s) => (s.status === 'running' ? { ...s, elapsed: (Date.now() - startedAt.current) / 1000 } : s))
    }, 1000)
    return () => window.clearInterval(t)
  }, [state.status])

  const start = useCallback(async (req: TranscribeRequest) => {
    if (runningRef.current) return // B4 防并发
    runningRef.current = true
    startedAt.current = Date.now()
    logSeq.current = 0
    setState({ ...INITIAL, status: 'running', logs: [], message: '正在启动引擎…', elapsed: 0 })

    try {
      const { taskId } = await ipc.startTranscribe(req)
      taskIdRef.current = taskId
      // invoke 返回前引擎可能已发过 progress（accepted 之前），
      // 这里不覆盖已有 state，只补 taskId。
      setState((s) => (s.status === 'running' ? { ...s, taskId } : s))
    } catch (err) {
      runningRef.current = false
      taskIdRef.current = null
      setState((s) => ({
        ...s,
        status: 'error',
        error: { message: describeIpcError(err), detail: String(err) },
        message: describeIpcError(err),
      }))
    }
  }, [])

  const cancel = useCallback(async () => {
    const id = taskIdRef.current
    if (!runningRef.current || !id) return
    try {
      await ipc.cancelTranscribe(id)
    } catch (err) {
      // 取消失败不阻塞 UI：引擎侧仍会发 error 事件收尾
      console.warn('[task] 取消请求失败', err)
    }
  }, [])

  const reset = useCallback(() => {
    runningRef.current = false
    taskIdRef.current = null
    setState(INITIAL)
  }, [])

  return {
    ...state,
    isRunning: state.status === 'running',
    start,
    cancel,
    reset,
  }
}

function appendLog(
  logs: LogLine[],
  text: string,
  stage: Stage | null,
  seq: { current: number },
): LogLine[] {
  const trimmed = text.trim()
  if (!trimmed) return logs
  const next = [...logs, { id: seq.current++, text: trimmed, stage }]
  // 环形裁剪，避免长任务把内存吃满
  return next.length > MAX_LOGS ? next.slice(next.length - MAX_LOGS) : next
}

/** 把 IPC 层抛出的各种错误翻译成人话；引擎的 TranscribeError 已在 error 事件里原样传回，不走这里。 */
export function describeIpcError(err: unknown): string {
  const raw = typeof err === 'string' ? err : err instanceof Error ? err.message : String(err)
  if (raw.includes('not found') || raw.includes('not allowed')) {
    return '引擎未就绪。请确认 src-tauri 侧已启动 sidecar 进程。'
  }
  if (raw.includes('denied') || raw.includes('permission')) {
    return '系统拒绝了文件访问权限，请检查应用权限设置。'
  }
  return `无法启动引擎：${raw}`
}

/** 当前应高亮的阶段锚点下标；未开始返回 -1 */
export function activeAnchorIndex(stage: Stage | null, status: TaskStatus): number {
  if (status === 'done') return STAGE_ANCHORS.length
  if (!stage) return -1
  const idx = STAGE_ANCHORS.findIndex((a) => a.stages.includes(stage))
  return idx
}
