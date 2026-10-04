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

/* ================================================================== *
 * 首启引导安装接口
 *
 * 背景：引擎 venv 实测 5.1GB（torch 一家 4.4GB），打进 installer 不现实。
 * 故应用壳（约 4MB）随包分发，引擎在首次运行时按需安装（主上拍板）。
 * ================================================================== */

export type InstallTier = 'basic' | 'full' | 'mcp'

export interface InstallTierInfo {
  id: InstallTier
  label: string
  desc: string
  /** 选这个档位**能做什么**——小白判断不了 200MB vs 5.2GB，得告诉他能力 */
  can: string[]
  /** 不能做什么（明确排除项，避免装完才发现缺功能） */
  cannot: string[]
  /** 磁盘需求（MB），用于安装前提示 */
  diskMB: number
}

export interface MirrorInfo {
  id: string
  label: string
  hint: string
}

export interface EngineStatus {
  engineDir: string
  ready: boolean
  reason: string
  python: string | null
  basic: boolean
  demucs: boolean
  cuda: boolean
  torchVersion: string
  mcp: boolean
  tiers: InstallTierInfo[]
  mirrors: MirrorInfo[]
  defaultMirror: string
  /** 已装但缺失的能力（如已装基础档则含 "demucs"），用于给增量升级入口 */
  missing: string[]
}

export interface InstallProgressEvent {
  taskId: string
  pct: number
  message: string
}

export interface InstallLogEvent {
  taskId: string
  message: string
}

/**
 * 安装结束事件。
 *
 * ⚠️ `ok` 必须看，不能只把「进程结束」当作成功 ——
 * 网络中断 / 磁盘不足 / pip 报错同样是正常退出 + 一段可读文本。
 * 此前只传 {taskId, lines}，导致失败时界面无声弹回选择页，
 * 用户完全不知道发生了什么。
 */
export interface InstallDoneEvent {
  taskId: string
  lines: number
  ok: boolean
  message: string | null
  logTail: string | null
  /** true = 脚本异常退出（崩溃/被杀），没来得及输出结构化结果 */
  outcomeMissing?: boolean
}

/** 查询引擎安装状态。未就绪时 UI 应显示引导安装页。 */
export async function checkEngine(): Promise<EngineStatus> {
  if (!inTauri()) {
    return {
      engineDir: '', ready: true, reason: '', python: null,
      basic: true, demucs: true, cuda: false, torchVersion: '', mcp: false,
      tiers: [], mirrors: [], defaultMirror: 'cn', missing: [],
    }
  }
  return invoke<EngineStatus>('check_engine')
}

/**
 * 启动引擎安装。进度经 install://progress 事件回传。
 *
 * `mirror` 默认国内镜像——PyTorch 的 CUDA wheel 约 2.5GB，
 * 官方源（境外）在国内常年几十 KB/s，是首启安装的头号杀手。
 */
export async function installEngine(
  tier: InstallTier,
  mirror = 'cn',
  targetDir?: string,
): Promise<StartResult> {
  if (!inTauri()) throw new Error('非桌面环境无法安装引擎')
  return invoke<StartResult>('install_engine', {
    tier,
    mirror,
    // null = 用后端默认位置（%LOCALAPPDATA%/bapu/engine）
    targetDir: targetDir && targetDir.trim() ? targetDir : null,
  })
}

/**
 * 选择引擎安装目录。
 *
 * 为什么要有：完整档约 5.2GB，若 C 盘空间紧张，用户必须能装到 D 盘。
 * 此前位置写死在 %LOCALAPPDATA%（即 C 盘），没有选择余地。
 * 返回 null 表示用户取消。
 */
export async function pickInstallDir(): Promise<string | null> {
  if (!inTauri()) return null
  const { open } = await import('@tauri-apps/plugin-dialog')
  const picked = await open({ directory: true, multiple: false })
  return typeof picked === 'string' ? picked : null
}

export async function cancelInstall(taskId: string): Promise<void> {
  if (!inTauri()) return
  return invoke<void>('cancel_transcribe', { taskId })
}

export async function onInstallProgress(
  cb: (e: InstallProgressEvent) => void,
): Promise<UnlistenFn> {
  if (!inTauri()) return () => {}
  return listen<InstallProgressEvent>('install://progress', (e) => cb(e.payload))
}

export async function onInstallLog(
  cb: (e: InstallLogEvent) => void,
): Promise<UnlistenFn> {
  if (!inTauri()) return () => {}
  return listen<InstallLogEvent>('install://log', (e) => cb(e.payload))
}

export async function onInstallDone(
  cb: (e: InstallDoneEvent) => void,
): Promise<UnlistenFn> {
  if (!inTauri()) return () => {}
  return listen<InstallDoneEvent>('install://done', (e) => cb(e.payload))
}
