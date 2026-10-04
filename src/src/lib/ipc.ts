/**
 * ipc.ts — Tauri command 调用的类型化封装
 *
 * 这是前端与 Rust 侧的唯一接缝。所有 invoke / listen 都收敛在这里，
 * 组件层不直接碰 @tauri-apps/api。
 *
 * ── 与 Rust 侧的约定（详见 src/lib/engine-contract.ts）──────────────────
 * invoke 命令名（snake_case，与 #[tauri::command] fn 名一致）：
 *   start_transcribe   (request: TranscribeRequest) -> StartResult
 *   cancel_transcribe  (taskId: string) -> void
 *   probe_audio        (path: string) -> ProbeResult
 *   get_env_info       () -> EnvInfo
 *   get_modes          () -> ModesPayload
 *   reveal_in_folder   (path: string) -> void
 *   open_with_musescore(path: string) -> void
 *
 * 事件名（Tauri event，全局）：
 *   transcribe://progress  -> ProgressEvent
 *   transcribe://log      -> LogEvent
 *   transcribe://done      -> DoneEvent
 *   transcribe://error     -> ErrorEvent
 *
 * ⚠️ 关键约定：snake_case ↔ camelCase 的转换责任在 **Rust 侧**。
 * Tauri 的 invoke 参数默认按 snake_case 匹配 serde 字段名，因此 Rust 侧
 * 需对 TranscribeRequest 加 `#[serde(rename_all = "camelCase")]`。
 * 前端发送 camelCase（见 types.ts），桥接层负责与 bridge.py 的
 * snake_case payload 对接。
 * ──────────────────────────────────────────────────────────────────
 */

import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import type {
  DoneEvent,
  EnvInfo,
  ErrorEvent,
  LogEvent,
  ModesPayload,
  ProbeResult,
  ProgressEvent,
  StartResult,
  TranscribeRequest,
} from './types'

/* ------------------------------------------------------------------ *
 * 非 Tauri 环境（浏览器里 vite dev / tsc 校验）下的降级桩。
 * 目的是让前端在无 Tauri 运行时也能渲染，不抛 "invoke not available"。
 * ------------------------------------------------------------------ */
const inTauri = (): boolean =>
  typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

function stub<T>(name: string, value: T): Promise<T> {
  console.warn(`[ipc] 非 Tauri 环境，${name} 返回桩值`)
  return Promise.resolve(value)
}

const STUB_MODES: ModesPayload = {
  modes: [
    {
      mode: 'full_auto',
      page: 1,
      label: '全自动扒谱（两轨）',
      description: '分离人声与伴奏，分别扒谱，导出双轨 MIDI',
      separates: true,
      tracks: 2,
      roles: ['vocals', 'accompaniment'],
    },
    {
      mode: 'accompaniment',
      page: 1,
      label: '只扒伴奏',
      description: '分离后只扒伴奏轨，适合只要伴奏旋律',
      separates: true,
      tracks: 1,
      roles: ['accompaniment'],
    },
    {
      mode: 'vocals',
      page: 1,
      label: '只扒人声旋律',
      description: '分离后只扒人声旋律，单音轨',
      separates: true,
      tracks: 1,
      roles: ['vocals'],
    },
    {
      mode: 'basic',
      page: 1,
      label: '基本扒谱（乐器 / 单音轨）',
      description: '不分离，直接对整段音频做多音高识别',
      separates: false,
      tracks: 1,
      roles: ['instrument'],
    },
    {
      mode: 'basic_multi',
      page: 2,
      label: '基本扒谱（多音轨）',
      description: '适合已有多轨素材，逐轨扒谱',
      separates: false,
      tracks: 1,
      roles: ['instrument'],
    },
    {
      mode: 'pre_separated',
      page: 2,
      label: '已分离音频直入',
      description: '导入你已分离好的人声 + 伴奏，跳过分离步骤',
      separates: false,
      tracks: 2,
      roles: ['vocals', 'accompaniment'],
    },
  ],
  stageLabels: {
    prepare: '准备音频',
    separate: '分离人声与伴奏',
    spectrum: '分析频谱',
    track: '追踪音符',
    export: '生成 MIDI',
  },
}

const STUB_ENV: EnvInfo = {
  demucs: false,
  cuda: false,
  device: 'cpu',
  ffmpeg: false,
  notes: ['未连接引擎（浏览器预览模式），以下能力检测结果为桩值。'],
}

/* ------------------------------------------------------------------ *
 * Commands
 * ------------------------------------------------------------------ */

/** 启动一次扒谱任务，立即返回 taskId；结果经事件回流。 */
export function startTranscribe(request: TranscribeRequest): Promise<StartResult> {
  if (!inTauri()) return stub('start_transcribe', { taskId: 'stub-task' })
  return invoke<StartResult>('start_transcribe', { request })
}

/** 取消任务。取消后引擎侧会发 error 事件，文案为「任务已被取消。」 */
export function cancelTranscribe(taskId: string): Promise<void> {
  if (!inTauri()) return stub('cancel_transcribe', undefined)
  return invoke<void>('cancel_transcribe', { taskId })
}

/** 读音频时长/大小，用于拖入后即时显示。 */
export function probeAudio(path: string): Promise<ProbeResult> {
  if (!inTauri()) {
    return stub('probe_audio', {
      path,
      name: path.split(/[\\/]/).pop() ?? path,
      size: 0,
      duration: 0,
      ok: true,
    })
  }
  return invoke<ProbeResult>('probe_audio', { path })
}

/** 引擎环境自检：Demucs / CUDA / ffmpeg。 */
export function getEnvInfo(): Promise<EnvInfo> {
  if (!inTauri()) return stub('get_env_info', STUB_ENV)
  return invoke<EnvInfo>('get_env_info')
}

/** 六模式的元信息（单一真源）。 */
export function getModes(): Promise<ModesPayload> {
  if (!inTauri()) return stub('get_modes', STUB_MODES)
  return invoke<ModesPayload>('get_modes')
}

/** 在文件管理器中定位文件。 */
export function revealInFolder(path: string): Promise<void> {
  if (!inTauri()) return stub('reveal_in_folder', undefined)
  return invoke<void>('reveal_in_folder', { path })
}

/** 用 MuseScore 打开 MIDI。未安装时 Rust 侧应回 error。 */
export function openWithMuseScore(path: string): Promise<void> {
  if (!inTauri()) return stub('open_with_musescore', undefined)
  return invoke<void>('open_with_musescore', { path })
}

/* ------------------------------------------------------------------ *
 * Events
 * ------------------------------------------------------------------ */

export const EVENT_PROGRESS = 'transcribe://progress'
export const EVENT_LOG = 'transcribe://log'
export const EVENT_DONE = 'transcribe://done'
export const EVENT_ERROR = 'transcribe://error'

type Handler<T> = (payload: T) => void

function subscribe<T>(event: string, handler: Handler<T>): Promise<UnlistenFn> {
  if (!inTauri()) {
    console.warn(`[ipc] 非 Tauri 环境，事件 ${event} 未订阅`)
    return Promise.resolve(() => {})
  }
  return listen<T>(event, (e) => handler(e.payload))
}

export const onProgress = (h: Handler<ProgressEvent>) => subscribe(EVENT_PROGRESS, h)
export const onLog = (h: Handler<LogEvent>) => subscribe(EVENT_LOG, h)
export const onDone = (h: Handler<DoneEvent>) => subscribe(EVENT_DONE, h)
export const onError = (h: Handler<ErrorEvent>) => subscribe(EVENT_ERROR, h)

/**
 * 一次性订阅四类事件，返回统一的取消函数。
 * 组件卸载时调用，避免回调打到已卸载的组件上。
 */
export async function subscribeAll(handlers: {
  progress: Handler<ProgressEvent>
  log: Handler<LogEvent>
  done: Handler<DoneEvent>
  error: Handler<ErrorEvent>
}): Promise<UnlistenFn> {
  const uns = await Promise.all([
    onProgress(handlers.progress),
    onLog(handlers.log),
    onDone(handlers.done),
    onError(handlers.error),
  ])
  return () => uns.forEach((u) => u())
}

/* ------------------------------------------------------------------ *
 * 文件选择（Tauri dialog 插件）
 * ------------------------------------------------------------------ */

const AUDIO_EXTENSIONS = [
  'wav', 'mp3', 'flac', 'ogg', 'oga', 'opus', 'aiff', 'aif', 'aifc', 'au', 'snd',
  'm4a', 'mp4', 'm4b', 'aac', 'wma', 'ape', 'alac', 'mpc', 'tta', 'wv',
]

export const AUDIO_FILTER = [
  { name: '音频文件', extensions: AUDIO_EXTENSIONS },
  { name: '所有文件', extensions: ['*'] },
]

/**
 * 打开文件选择对话框。
 * @param multiple true 时返回多选（basic_multi / pre_separated 需要）
 */
export async function pickAudioFile(multiple = false): Promise<string[]> {
  if (!inTauri()) return []
  const { open } = await import('@tauri-apps/plugin-dialog')
  const picked = await open({
    multiple,
    directory: false,
    filters: AUDIO_FILTER,
  })
  if (!picked) return []
  return Array.isArray(picked) ? picked : [picked]
}

/**
 * 选择 MIDI 输出位置。取消则返回 null，调用方回落到引擎默认
 * （放在输入文件同目录，见 bridge.py handle_transcribe）。
 */
export async function pickMidiOutput(defaultName: string): Promise<string | null> {
  if (!inTauri()) return null
  const { save } = await import('@tauri-apps/plugin-dialog')
  return await save({
    defaultPath: defaultName,
    filters: [{ name: 'MIDI 文件', extensions: ['mid', 'midi'] }],
  })
}
