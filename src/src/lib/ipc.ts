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

/**
 * 浏览器预览用的桩数据，**必须与 engine/pipeline.describe_modes() 逐字一致**。
 * 真值永远来自引擎；此处只是无 Tauri 运行时的降级副本，改引擎时同步改这里。
 */
const STUB_MODES: ModesPayload = {
  modes: [
    {
      mode: 'full_auto',
      page: 1,
      label: '全自动扒谱（两轨）',
      description: '分离人声与伴奏，分别扒谱，导出双轨 MIDI',
      hint: '先分离再扒：伴奏走多音高，人声走单旋律，一次拿到两条轨。',
      separates: true,
      tracks: 2,
      roles: ['vocals', 'accompaniment'],
    },
    {
      mode: 'accompaniment',
      page: 1,
      label: '只扒伴奏',
      description: '分离后只扒伴奏轨，适合只要伴奏旋律',
      hint: '先分离再扒：只保留伴奏一轨，人声部分不输出。',
      separates: true,
      tracks: 1,
      roles: ['accompaniment'],
    },
    {
      mode: 'vocals',
      page: 1,
      label: '只扒人声旋律',
      description: '分离后只扒人声旋律，单音轨',
      hint: '先分离再扒：只保留人声旋律一轨，适合翻唱或独奏参考。',
      separates: true,
      tracks: 1,
      roles: ['vocals'],
    },
    {
      mode: 'basic',
      page: 1,
      label: '整段直扒（乐器 · 不分离）',
      description: '不分离，直接对整段音频做多音高识别',
      hint: '不分离，直接对整段音频做多音高识别；适合纯器乐音频。',
      separates: false,
      tracks: 1,
      roles: ['instrument'],
    },
    {
      mode: 'basic_vocals',
      page: 2,
      label: '单轨直扒（人声旋律）',
      description: '不分离，对整段音频追一条旋律线，输出单轨',
      hint: '不分离，直接提取单条旋律线，输出 1 轨。适合已分好的人声，或小提琴等单声部乐器独奏录音。',
      separates: false,
      tracks: 1,
      roles: ['vocals'],
    },
    {
      mode: 'basic_accompaniment',
      page: 2,
      label: '单轨直扒（伴奏多音高）',
      description: '不分离，对整段音频做多音高识别，输出单轨',
      hint: '不分离，直接做多音高识别，输出 1 轨。适合伴奏、钢琴、吉他等复音乐器，方便改成别的乐器演奏。',
      separates: false,
      tracks: 1,
      roles: ['accompaniment'],
    },
    {
      mode: 'basic_multi',
      page: 2,
      label: '多轨直扒（逐轨扒谱）',
      description: '不分离，每个文件输出一轨，适合已分好轨的素材',
      hint: '不分离，每个文件各输出 1 轨；适合手上已经分好轨的多轨素材。',
      separates: false,
      tracks: 0,
      roles: ['instrument'],
    },
    {
      mode: 'pre_separated',
      page: 2,
      label: '已分离音频直入',
      description: '导入你已分离好的人声 + 伴奏，跳过分离步骤',
      hint: '你已分好人声与伴奏，放进两个槽位即可，跳过分离直接扒。',
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
  /**
   * 该档位是否依赖 PyTorch（GPU 版）。
   *
   * 依赖 torch 的档位在 Python 版本不兼容时必然安装失败，
   * UI 据此决定是否显示 pythonCompat 警告。
   */
  needsTorch: boolean
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
  /**
   * 本机 Python 是否满足 PyTorch（GPU 版）要求（需 3.10 – 3.13）。
   *
   * 为什么要在选择页就拿到：torch cu124 的 wheel 只到 cp313。
   * 若本机只有 3.14，完整档必失败，而报错是 pip 的
   * 「Could not find a version ... (from versions: none)」——
   * 与「镜像不可用」逐字相同，用户会误判成网络问题而反复换源。
   * 前置告知，用户直接改选基础档即可。
   */
  pythonCompat?: { ok: boolean; cmd: string; detail: string }
  /**
   * 应用是否自带了独立的 Python 运行时（随包分发，约 45MB）。
   *
   * 为 true 时，用户**无需自备任何 Python**，也不必装任何前置环境——
   * 点一下「安装」就能跑完。为 false 时才会回退到用户机器上的系统 Python，
   * 那时「本机没有 Python 3.9+」才会构成真正的阻碍。
   *
   * 引导页据此把这条最容易劝退小白的门槛，换成一句安心的说明。
   */
  runtimeBundled?: boolean
  /** 实际用于创建引擎环境的解释器绝对路径（诊断用，也用来在界面上明示来源） */
  basePython?: string
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
      pythonCompat: { ok: true, cmd: '', detail: '' },
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
 * 把已安装的引擎迁移到新位置。
 *
 * 不重装：完整档 5.4GB，用户只是想把引擎从 C 盘挪走，不该付重下 2.5GB 的代价。
 * 进度与结果**复用安装的同一套事件通道**（install://progress / log / done），
 * 所以这里不需要新的订阅接口。
 */
export async function migrateEngine(newDir: string): Promise<StartResult> {
  if (!inTauri()) throw new Error('非桌面环境无法迁移引擎')
  return invoke<StartResult>('migrate_engine', { newDir })
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

/* ================================================================== *
 * 新手指引：CLI / MCP 接入信息
 *
 * 路径为什么必须由后端给：引擎装在哪块盘、哪个目录是用户在引导页
 * 自己选的（完整档 5.4GB，C 盘紧张就得换盘），前端无从知晓。
 * 而 MCP 客户端配置里必须写绝对路径——所以这份 JSON 只能现算。
 * ================================================================== */

export interface IntegrationInfo {
  /** 引擎是否已就绪。false 时其余字段为空，应引导用户去安装 */
  ready: boolean
  /** 引擎源码目录（含 cli.py / mcp_server.py） */
  engineDir: string
  /** 引擎解释器绝对路径 */
  python: string
  /** 能力自检命令，可整行粘贴 */
  cliCaps: string
  /** 扒谱示例命令 */
  cliExample: string
  /** 可直接粘进 MCP 客户端配置的完整 JSON */
  mcpConfig: string
  /** 引擎档位是否含 mcp 依赖（基础档不含） */
  mcpReady: boolean
  /** 未就绪时的说明 */
  reason: string
}

const STUB_INTEGRATION: IntegrationInfo = {
  ready: false,
  engineDir: '',
  python: '',
  cliCaps: '',
  cliExample: '',
  mcpConfig: '',
  mcpReady: false,
  reason: '浏览器预览模式，无本地引擎',
}

export async function getIntegrationInfo(): Promise<IntegrationInfo> {
  if (!inTauri()) return STUB_INTEGRATION
  return invoke<IntegrationInfo>('get_integration_info')
}

/**
 * 把 MCP 配置导出成 .json 文件，返回写入的绝对路径。
 * 内容由后端现算，前端只能决定「存到哪」。
 */
export async function exportMcpConfig(target: string): Promise<string> {
  if (!inTauri()) throw new Error('非桌面环境无法导出')
  return invoke<string>('export_mcp_config', { target })
}

/** 选一个保存 .json 的位置。取消返回 null。 */
export async function pickJsonSavePath(defaultName: string): Promise<string | null> {
  if (!inTauri()) return null
  const { save } = await import('@tauri-apps/plugin-dialog')
  return await save({
    defaultPath: defaultName,
    filters: [{ name: 'JSON 配置', extensions: ['json'] }],
  })
}

/* ══════════════════════════════════════════════════════════
 * 问题反馈：一键诊断报告
 *
 * 设计意图（主上原话）：「做一个可以复制反馈 debug 报错日志结果或文件的按钮」，
 * 且要「更无感小白化」—— 小白用户不知道日志在哪，也不该知道。
 * 故由 Rust 侧把系统/引擎/Python 编码等事实一次收集齐，前端只负责
 * 「一键拿到文本 → 复制 / 落盘 / 打开文件夹」。
 * ══════════════════════════════════════════════════════════ */

/**
 * 诊断报告的「现场上下文」。
 *
 * 刻意由前端补料而非让 Rust 去猜：报错现场只有界面知道 ——
 * 用户拖的是哪个文件、界面上已经显示了哪些日志行。
 */
export interface DiagnosticContext {
  /** 界面上「运行日志」里的那些行（时间正序） */
  logs: string[]
  errorMessage?: string | null
  errorDetail?: string | null
  inputPath?: string | null
  mode?: string | null
  /** 由前端生成：Rust 侧没有日期格式化能力，不为它引入 chrono */
  generatedAt?: string | null
  appVersion?: string | null
}

/** 生成诊断报告文本（不落盘）。可先拿来做预览或直接复制。 */
export async function buildDiagnosticReport(
  context: DiagnosticContext,
): Promise<string> {
  if (!inTauri()) throw new Error('非桌面环境无法生成诊断报告')
  return invoke<string>('build_diagnostic_report', { context })
}

/**
 * 写入诊断报告文件。
 *
 * 后端会加 UTF-8 BOM —— 否则 Windows 记事本按 ANSI 解读，中文全乱码，
 * 而这份文件正是要拿给别人看的。
 */
export async function saveDiagnosticReport(
  target: string,
  content: string,
): Promise<string> {
  if (!inTauri()) throw new Error('非桌面环境无法保存')
  return invoke<string>('save_diagnostic_report', { target, content })
}

/** 选一个保存 .txt 的位置。取消返回 null。 */
export async function pickTextSavePath(defaultName: string): Promise<string | null> {
  if (!inTauri()) return null
  const { save } = await import('@tauri-apps/plugin-dialog')
  return await save({
    defaultPath: defaultName,
    filters: [{ name: '文本文件', extensions: ['txt'] }],
  })
}

/* ══════════════════════════════════════════════════════════
 * 诊断报告的上报通道
 *
 * 主上原话：「如果能更无感小白化 点提交 bug 问题日志直接给我们的项目就好了」。
 *
 * ## 为什么不能在前端直接发
 * tauri.conf.json 的 CSP 是 `default-src 'self'`，**没有 connect-src**，
 * 前端 fetch 外部端点会被 WebView 拦下。所以上报必须走 Rust 侧 ——
 * 那里不受 CSP 约束。
 *
 * ## 为什么端点与凭证不在这里
 * 凭证只落在 Rust 侧常量里。前端代码可被 WebView 开发者工具直接查看，
 * 凭证不该出现在那儿。这里只负责「把报告交出去」这一个动作。
 *
 * ## 未接入时的行为
 * Rust 侧 token 为空 → 返回 unconfigured → 界面自动降级为「复制到剪贴板」。
 * 小白不会被卡在一个不存在的网络上。
 * ══════════════════════════════════════════════════════════ */

export type SubmitOutcome =
  | { status: 'sent' }
  | { status: 'unconfigured' }
  | { status: 'failed'; reason: string }

/** 把报告交给项目方。未接入端点时返回 unconfigured，由调用方降级处理。 */
export async function submitDiagnosticReport(report: string): Promise<SubmitOutcome> {
  if (!inTauri()) return { status: 'unconfigured' }
  try {
    await invoke<void>('submit_diagnostic_report', { report })
    return { status: 'sent' }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    if (msg.includes('unconfigured')) return { status: 'unconfigured' }
    return { status: 'failed', reason: msg }
  }
}
