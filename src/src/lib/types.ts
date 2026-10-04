/**
 * types.ts — 与 Python 引擎 `pipeline.py` / `bridge.py` 对齐的类型定义
 *
 * 红线：本文件的字段名必须与 bridge.py 的 JSON 协议逐字一致。
 * bridge 的 result.data 用 camelCase（outputPath / totalNotes / separationMethod），
 * 而 TranscribeResult.tracks 的元素仍是 snake_case（name/program/notes/duration），
 * 因为那是从 midi_post 原样透传的下游 dict——不要"顺手统一"，会静默丢字段。
 */

/** 引擎五阶段（pipeline.STAGES）。UI 上呈现为四个锚点，prepare+separate 合并显示。 */
export type Stage = 'prepare' | 'separate' | 'spectrum' | 'track' | 'export'

/** 六条产品路径（pipeline.Mode） */
export type TranscribeMode =
  | 'full_auto'
  | 'accompaniment'
  | 'vocals'
  | 'basic'
  | 'basic_multi'
  | 'pre_separated'

/** engine/pipeline.describe_modes() 单项。前端模式列表以此为单一真源。 */
export interface ModeInfo {
  mode: TranscribeMode
  /** 归属功能页：1 = 歌曲扒谱，2 = 基本扒谱 */
  page: 1 | 2
  label: string
  description: string
  /** 是否会调用 Demucs 做分离（决定是否显示降级警告） */
  separates: boolean
  tracks: number
  roles: string[]
}

/** engine/bridge.py handle_modes 的 data */
export interface ModesPayload {
  modes: ModeInfo[]
  stageLabels: Record<Stage, string>
}

/** engine/format_guard.py probe_duration + bridge handle_probe */
export interface ProbeResult {
  path: string
  name: string
  /** 字节 */
  size: number
  /** 秒 */
  duration: number
  ok: boolean
}

/** engine/separator.py capabilities() */
export interface EnvInfo {
  demucs: boolean
  cuda: boolean
  device: 'cuda' | 'cpu' | string
  ffmpeg: boolean
  notes: string[]
}

/** TranscribeResult.tracks 的元素（midi_post 原样 dict，snake_case） */
export interface ResultTrack {
  name: string
  program: number
  notes: number
  duration: number
}

/** TranscribeResult 的 camelCase 序列化形式 */
export interface TranscribeResult {
  outputPath: string
  tracks: ResultTrack[]
  totalNotes: number
  /** 输入音频时长（秒） */
  duration: number
  /** 本次耗时（秒） */
  elapsed: number
  mode: string
  separationMethod: string | null
  warnings: string[]
}

/**
 * TranscribeRequest 的 camelCase 形式。
 * 字段名与 bridge.py handle_transcribe 的 allowed 集合一一对应。
 */
export interface TranscribeRequest {
  mode: TranscribeMode
  inputPath: string
  /** .mid 输出路径。留空则由 bridge 放在输入文件同目录 */
  outputPath?: string
  /** mode=pre_separated 时传 [伴奏路径]；basic_multi 时传其余轨道 */
  extraInputs?: string[]
  /** null = 用引擎按模式调好的默认值 */
  nPeaks?: number | null
  hopLength?: number
  onsetThreshold?: number
  pitchThreshold?: number
  minNoteDuration?: number
  tempo?: number
  perceptual?: boolean
  simplify?: number
  pianoMode?: boolean
  demucsModel?: string
  device?: string
  allowHpssFallback?: boolean
  trackNames?: (string | null)[]
}

/** start_transcribe 的返回值 */
export interface StartResult {
  taskId: string
}

/** on_progress 事件 payload */
export interface ProgressEvent {
  taskId: string
  stage: Stage
  /** 0~1 */
  pct: number
  message: string
}

/** on_log 事件 payload（引擎与第三方库的实时输出） */
export interface LogEvent {
  taskId: string
  message: string
}

/** on_done 事件 payload */
export interface DoneEvent {
  taskId: string
  result: TranscribeResult
}

/** on_error 事件 payload。message 已是中文面向用户文案，前端原样展示。 */
export interface ErrorEvent {
  taskId: string
  message: string
  detail: string
}

/** 引擎 midi_post.TRACK_DISPLAY_NAMES 的前端镜像，仅用于日志可读性 */
export const TRACK_DISPLAY_NAMES: Record<string, string> = {
  Voice: '人声',
  Choir: '合唱',
  Accompaniment: '伴奏',
  Piano: '钢琴',
  Instrument: '乐器',
  Melody: '旋律',
  Guitar: '吉他',
  Bass: '贝斯',
  Strings: '弦乐',
}
