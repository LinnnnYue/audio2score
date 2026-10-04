/**
 * engine-contract.ts — 前端 ↔ Rust 侧的 Tauri command / 事件契约
 *
 * 这份文件是**契约依据**，不是可执行代码（除了几个类型别名）。
 * Rust 侧照此实现即可与前端对接；前端所有 invoke 都集中在 `src/lib/ipc.ts`。
 *
 *⚠️ 命名约定（最容易踩的坑）
 * Tauri 2 的 `invoke(cmd, args)` 里，args 的键按**命令函数参数名**匹配，
 * 默认不做 camelCase 转换。因此：
 *   - command 名：snake_case，与 `#[tauri::command] fn` 名逐字一致
 *   - **结构体 payload**：Rust 侧必须加 `#[serde(rename_all = "camelCase")]`
 *     因为前端发的是 camelCase（见 types.ts）
 *   - 简单标量参数（path / taskId）由 Tauri 自动兼容两种写法
 *
 *另一处易错：engine `bridge.py` 的 result.data 用 camelCase，但
 * `TranscribeResult.tracks` 的元素是 **snake_case**（name/program/notes/duration），
 * 因为那是 midi_post 原样透传的下游 dict。转换时**不要递归转 snake**，
 * 否则会静默丢字段。
 */

/* ================================================================== *
 * 1. Commands（invoke）
 * ================================================================== */

/** 启动扒谱，立即返回；结果经事件回流 */
export interface StartTranscribeArgs {
  request: TranscribeRequestCamel
}
export interface StartTranscribeResult {
  taskId: string
}

/** 取消任务。取消后引擎侧仍会发一条 error 事件，文案为「任务已被取消。」 */
export interface CancelTranscribeArgs {
  taskId: string
}

/** 读音频时长/大小 */
export interface ProbeAudioArgs {
  path: string
}
export interface ProbeAudioResult {
  path: string
  name: string
  size: number
  duration: number
  ok: boolean
}

/** 引擎环境自检 */
export interface GetEnvInfoResult {
  demucs: boolean
  cuda: boolean
  device: string
  ffmpeg: boolean
  notes: string[]
}

/** 六模式元信息（前端模式列表的单一真源） */
export interface GetModesResult {
  modes: ModeInfo[]
  stageLabels: Record<string, string>
}

export interface RevealInFolderArgs {
  path: string
}
export interface OpenWithMuseScoreArgs {
  path: string
}

/**
 * 命令清单（Rust 侧签名照抄）：
 * | command                | args                | 返回                |
 * |------------------------|---------------------|---------------------|
 * | start_transcribe       | {request}           | {taskId}            |
 * | cancel_transcribe      | {taskId}            | void                |
 * | probe_audio            | {path}              | ProbeResult         |
 * | get_env_info           | —                   | EnvInfo             |
 * | get_modes              | —                   | ModesPayload        |
 * | reveal_in_folder       | {path}              | void                |
 * | open_with_musescore    | {path}              | void                |
 *
 * 注意：
 * - reveal_in_folder / open_with_musescore 在「文件不存在」「未装 MuseScore」时
 *   **不要** panic，改为返回 Err(String)，前端会catch 并可提示。
 * - get_modes 的返回是 {modes, stageLabels} 二元组，不要只回 modes，
 *   前端要用 stageLabels 渲染进度区中文标签。
 */

/* ================================================================== *
 * 2. Events（listen）
 * ================================================================== *
 * 四个全局事件名（不带窗口前缀）：
 *   transcribe://progress
 *   transcribe://log
 *   transcribe://done
 *   transcribe://error
 *
 *每个 payload 都带 taskId，前端据此过滤掉过期任务的事件。
 */

export interface ProgressEventPayload {
  taskId: string
  stage: 'prepare' | 'separate' | 'spectrum' | 'track' | 'export'
  /** 0~1，不是 0~100 */
  pct: number
  message: string
}

export interface LogEventPayload {
  taskId: string
  message: string
}

export interface DoneEventPayload {
  taskId: string
  result: TranscribeResultCamel
}

export interface ErrorEventPayload {
  taskId: string
  /** 引擎 TranscribeError.user_message，已是中文面向用户文案，前端原样展示 */
  message: string
  /** 技术细节，前端收进折叠区 */
  detail: string
}

/* ================================================================== *
 * 3. Payload 类型
 * ================================================================== */

export interface ModeInfo {
  mode: string
  page: 1 | 2
  label: string
  description: string
  separates: boolean
  tracks: number
  roles: string[]
}

export interface TrackInfo {
  /**注意：snake_case，与 bridge.py 的 tracks 元素一致，不要转 */
  name: string
  program: number
  notes: number
  duration: number
}

export interface TranscribeResultCamel {
  outputPath: string
  tracks: TrackInfo[]
  totalNotes: number
  duration: number
  elapsed: number
  mode: string
  separationMethod: string | null
  warnings: string[]
}

export interface TranscribeRequestCamel {
  mode: string
  inputPath: string
  /** .mid 输出路径。留空/省略则由 bridge 放在输入文件同目录 */
  outputPath?: string
  /** pre_separated 传 [伴奏路径]；basic_multi 传其余轨道 */
  extraInputs?: string[]
  /** null/省略 = 用引擎按模式调好的默认值 */
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

/* ================================================================== *
 * 4. 前端如何调用（示例，Rust 侧对照检查字段是否接得住）
 * ================================================================== */

/*
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'

// 启动
const { taskId } = await invoke<StartTranscribeResult>('start_transcribe', {
  request: {
    mode: 'full_auto',
    inputPath: 'D:/music/a.wav',
    nPeaks: null,// 用引擎默认
    onsetThreshold: 0.3,
    perceptual: false,   // 默认关
    pianoMode: false,
  },
})

// 进度
await listen<ProgressEventPayload>('transcribe://progress', (e) => {
  console.log(e.payload.stage, e.payload.pct) // 0~1
})

// 取消
await invoke('cancel_transcribe', { taskId })

// 产出后
await invoke('reveal_in_folder', { path: result.outputPath })
await invoke('open_with_musescore', { path: result.outputPath })
*/

/* ================================================================== *
 * 5. 前端已做的容错（Rust 侧不必重复处理，但需知道边界）
 * ================================================================== */

/**
 * - 拖放：webview 的 `onDragDropEvent` 给的是**绝对路径**，不是 File 对象，
 *   所以前端把路径交给 probe_audio 拿时长。不需要前端读文件内容。
 * - 非 Tauri 环境（浏览器预览）：ipc.ts 全部走桩值，不会崩，可离线看 UI。
 * - 引擎未就绪：start_transcribe 抛错时前端展示通用中文提示，
 *   但**引擎运行期的失败**请务必走 error 事件并带 user_message，不要用 panic。
 * - 阶段标签：UI 上是四个锚点（分离人声 / 分析频谱 / 追踪音符 / 生成 MIDI），
 *   对应引擎五阶段里 prepare+separate 合并。stageLabels 由 get_modes 下发，
 *   前端不硬编码中文。
 */
