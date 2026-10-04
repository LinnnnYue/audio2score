/**
 * EngineSetup.tsx — 首启引导安装页
 *
 * 为什么需要这一页
 * -----------------
 * 引擎 venv 实测 5.1GB（torch 一家 4.4GB）。打进 NSIS installer 体积不可接受，
 * 故应用壳（约 4MB）随包分发，引擎在首次运行时按需安装（主上 2026-10-04 拍板
 * 「首启动引导装也行」）。
 *
 * 三档选择
 * --------
 *   基础 basic  ~200MB  1-2 min   音频直扒，不含人声分离
 *   完整 full   ~5.1GB  8-20 min  + GPU 版 torch 与 Demucs（推荐）
 *   +MCP  mcp   ~5.1GB  8-20 min  额外供 AI 助手调用
 *
 * 设计约束（沿用项目动效硬标准）
 * -----------------------------
 *   禁 ease-in；自定义曲线；时长 <300ms；press scale(0.97)；
 *   origin-aware；无 transition:all；尊重 prefers-reduced-motion。
 *
 * 审美红线：无品红、无纯黑。全部走主题 CSS 变量，四方向自动适配。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  Check,
  CircleDot,
  Cpu,
  Download,
  Globe,
  HardDrive,
  Loader2,
  Package,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  X,
} from 'lucide-react'
import {
  cancelInstall,
  checkEngine,
  installEngine,
  onInstallDone,
  onInstallLog,
  onInstallProgress,
  pickInstallDir,
  type EngineStatus,
  type InstallDoneEvent,
  type InstallTier,
} from '../lib/ipc'
import { WindowControls } from './WindowControls'

type Phase = 'checking' | 'ready' | 'choosing' | 'installing' | 'done' | 'failed'

interface Props {
  onReady: () => void
  /**
   * 进入意图。
   * - `first-run`（默认）：引擎就绪即自动进主界面
   * - `upgrade`：用户是来「加装缺失功能」的，就绪时**停在选择界面**，
   *   否则会立刻被弹回主界面，根本来不及选。
   */
  intent?: 'first-run' | 'upgrade'
}

export function EngineSetup({ onReady, intent = 'first-run' }: Props) {
  const [phase, setPhase] = useState<Phase>('checking')
  const [status, setStatus] = useState<EngineStatus | null>(null)
  const [tier, setTier] = useState<InstallTier>('full')
  const [pct, setPct] = useState(0)
  const [message, setMessage] = useState('')
  const [logs, setLogs] = useState<string[]>([])
  const [error, setError] = useState('')
  const [taskId, setTaskId] = useState('')
  /** 下载源。默认国内镜像——官方源下 2.5GB 的 PyTorch 在国内常超时 */
  const [mirror, setMirror] = useState('cn')
  /** 自定义安装目录。空 = 用后端默认（%LOCALAPPDATA%/bapu/engine，即 C 盘） */
  const [targetDir, setTargetDir] = useState('')
  /** 安装结束的结构化结果（成败 + 说明） */
  const [outcome, setOutcome] = useState<InstallDoneEvent | null>(null)
  const logRef = useRef<HTMLDivElement>(null)

  /* ── 首启检测 ── */
  /**
   * 注意：这里刻意**不**在开头 setPhase('checking')。
   *   - 首次挂载：phase 初值本就是 'checking'，无需设置；
   *   - 手动「重新检测」：由按钮自己先切到 'checking' 再调本函数。
   * 效果是 effect 内不再出现同步 setState（React 会警告「级联渲染」），
   * 行为完全不变。
   */
  const probe = useCallback(async () => {
    try {
      const s = await checkEngine()
      setStatus(s)
      if (s.defaultMirror) setMirror(s.defaultMirror)
      if (s.ready && intent === 'first-run') {
        setPhase('ready')
        // 首次运行：引擎已就绪，短暂展示后进入主界面
        setTimeout(onReady, 650)
      } else {
        // 未就绪，或用户主动来加装功能 → 都停在选择界面
        setPhase('choosing')
      }
    } catch (e) {
      setError(String(e))
      setPhase('failed')
    }
  }, [onReady, intent])

  useEffect(() => {
    // 静态检查在此为保守误报：probe() 内部所有 setState 都在
    // await checkEngine() 之后，属异步更新，不会造成同步级联渲染；
    // 首帧使用的 'checking' 是 useState 初值，压根不是 setState。
    // oxlint-disable-next-line react/set-state-in-effect
    void probe()
  }, [probe])

  /* ── 安装事件订阅 ── */
  useEffect(() => {
    if (phase !== 'installing') return
    const cleanups: Array<() => void> = []

    void onInstallProgress((e) => {
      setPct(e.pct)
      setMessage(e.message)
    }).then((f) => cleanups.push(f))

    void onInstallLog((e) => {
      setLogs((prev) => [...prev.slice(-200), e.message])
    }).then((f) => cleanups.push(f))

    void onInstallDone((e) => {
      if (e.ok) {
        // 成功：停在「完成」页，让用户看清装了什么、再自己进主界面。
        // 之前这里直接 probe() → 就绪即弹回选择页，用户以为失败了。
        setOutcome(e)
        setPhase('done')
      } else {
        // 失败也必须说清楚「为什么」。
        // 后端已经回传了 logTail（安装器最后一条日志）——它往往就是根因本身
        // （pip 的 ERROR 行 / 磁盘不足提示 / 网络超时）。
        // 初版把 logTail 直接丢掉，用户只看到一句「安装未完成」，等于没说。
        const tail = typeof e.logTail === 'string' && e.logTail.trim() ? e.logTail.trim() : ''
        const head =
          e.message ||
          (e.outcomeMissing
            ? '安装进程异常退出，没有返回结果。请重试或查看下方日志。'
            : '安装失败。')
        setOutcome(e)
        setError(tail ? `${head}\n\n— 安装器最后一条日志 —\n${tail}` : head)
        setPhase('failed')
      }
    }).then((f) => cleanups.push(f))

    return () => cleanups.forEach((f) => f())
  }, [phase, probe])

  /* 日志自动滚到底 */
  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' })
  }, [logs])

  /* ── 开始安装 ── */
  const startInstall = useCallback(async () => {
    setPhase('installing')
    setPct(0)
    setLogs([])
    setError('')
    try {
      const r = await installEngine(tier, mirror, targetDir)
      setTaskId(r.taskId)
      setMessage('正在准备…')
    } catch (e) {
      setError(String(e))
      setPhase('failed')
    }
  }, [tier, mirror, targetDir])

  const abort = useCallback(async () => {
    if (taskId) await cancelInstall(taskId)
    // 取消后回选择页（probe 自身不再负责切 phase，见上方注释）
    setPhase('choosing')
    void probe()
  }, [taskId, probe])

  /**
   * 档位数据全部来自后端 `install_status()`，**前端不硬编码文案**。
   * 这样「能做/不能做」与「后端实际装什么」永远是同一份真源——
   * 之前 TIER_FULL 漏装 demucs 却仍宣称支持分离，正是因为描述与实现
   * 分处两地、无人对账。
   */
  // 逐字段兜底：后端版本不匹配时（如旧 bootstrap.py 没有 can 字段），
  // 少了防御就会在渲染期抛 TypeError → React 卸载整棵树 → 黑屏。
  const tiers = (status?.tiers ?? []).map((t) => ({
    ...t,
    label: t.label ?? t.id,
    desc: t.desc ?? '',
    can: t.can ?? [],
    cannot: t.cannot ?? [],
  }))
  const mirrors = status?.mirrors ?? []

  /**
   * 当前选中档位是否依赖 PyTorch。
   *
   * 优先用后端下发的 needsTorch —— 前端硬编码「非 basic 就需 torch」是
   * 第二份真源，后端的档位定义一改就会失配（同类失配已在本项目发生过一次）。
   * 仅在后端字段缺失（旧 bootstrap.py）时按 basic 例外兜底。
   */
  const needsTorch = tiers.find((t) => t.id === tier)?.needsTorch ?? tier !== 'basic'

  /** 已装但缺能力时，给出「加装」提示而非让用户整个重装 */
  const needsUpgrade = (status?.missing?.length ?? 0) > 0

  return (
    // bg-bg 必不可少：窗口设了 transparent:true，没有背景色就是一片纯黑。
    // 主上实测踩过：「点了加装直接变黑」。
    <div className="flex h-screen flex-col overflow-hidden bg-bg text-[var(--text)]">
      {/* 窗口 chrome（与主界面一致，无可见交界线） */}
      <header
        data-tauri-drag-region
        className="flex h-11 shrink-0 items-center gap-3 px-4 select-none"
      >
        <div className="flex items-center gap-2 text-[var(--text-dim)]">
          <CircleDot size={15} strokeWidth={2.2} className="text-[var(--accent)]" />
          <span className="text-[12.5px] font-medium tracking-tight">扒谱台</span>
        </div>
        <div className="flex-1" />
        <WindowControls />
      </header>

      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto flex min-h-full w-full max-w-[720px] flex-col justify-center px-8 py-10">
          {/* ── 检测中 ── */}
          {phase === 'checking' && (
            <div className="flex flex-col items-center gap-4 text-center">
              <Loader2
                size={26}
                strokeWidth={2}
                className="animate-spin text-[var(--accent)]"
              />
              <p className="text-[13px] text-[var(--text-dim)]">正在检查引擎状态…</p>
            </div>
          )}

          {/* ── 已就绪 ── */}
          {phase === 'ready' && (
            <div className="flex flex-col items-center gap-4 text-center">
              <div className="grid h-12 w-12 place-items-center rounded-full bg-[var(--accent-soft)]">
                <Check size={22} strokeWidth={2.4} className="text-[var(--accent)]" />
              </div>
              <div>
                <h2 className="text-[17px] font-semibold text-[var(--text)]">
                  引擎已就绪
                </h2>
                <p className="mt-1 text-[12.5px] text-[var(--text-dim)]">
                  {status?.demucs
                    ? status.cuda
                      ? 'Demucs + CUDA 加速可用'
                      : 'Demucs 可用（CPU 模式）'
                    : '基础扒谱可用，未装 Demucs'}
                </p>
              </div>
            </div>
          )}

          {/* ── 安装完成 ──
              必须给一个明确的「完成」页：此前安装成功后直接 probe()，
              引擎就绪即被弹回选择页，用户会以为失败了
              （主上实测反馈「跳回这个页面了，还无法返回」）。 */}
          {phase === 'done' && (
            <div className="flex flex-col items-center gap-5 py-8 text-center">
              <div className="grid h-14 w-14 place-items-center rounded-full bg-[var(--accent-soft)]">
                <Check size={26} strokeWidth={2.4} className="text-[var(--accent)]" />
              </div>
              <div>
                <h2 className="text-[18px] font-semibold text-[var(--text)]">
                  安装完成
                </h2>
                <p className="mt-2 max-w-[48ch] text-[13px] leading-relaxed text-[var(--text-dim)]">
                  {outcome?.message || '所需功能已就绪。'}
                </p>
              </div>
              <button
                type="button"
                onClick={onReady}
                className="inline-flex h-10 items-center gap-2 rounded-lg px-5 text-[13px] font-medium transition-[background-color,transform] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)] active:scale-[0.97]"
                style={{ background: 'var(--accent)', color: 'var(--accent-contrast)' }}
              >
                进入主界面
              </button>
            </div>
          )}

          {/* ── 选择档位 ── */}
          {phase === 'choosing' && (
            <>
              <header className="mb-7">
                <div className="mb-3 inline-flex items-center gap-2 rounded-full bg-[var(--accent-soft)] px-2.5 py-1">
                  <Package size={12} strokeWidth={2.2} className="text-[var(--accent)]" />
                  <span className="text-[11px] font-medium text-[var(--accent)]">
                    首次使用 · 需安装引擎
                  </span>
                </div>
                <h1 className="text-[22px] font-semibold tracking-[-0.02em] text-[var(--text)]">
                  选择安装档位
                </h1>
                <p className="mt-2 max-w-[54ch] text-[13px] leading-relaxed text-[var(--text-dim)]">
                  扒谱依赖音频分析库与音源分离模型，体积较大，故不随应用打包，
                  首次运行时安装到本机，之后永久可用。
                </p>
              </header>

              {/* ── 内置运行时说明 ──
                  应用自带一份独立的 Python（随包分发，约 45MB），
                  与用户机器上有没有、装的是几版**完全无关**。

                  为什么值得专门占一行：小白拿到这类工具的第一个疑问就是
                  「我电脑没装 Python，能不能用」。在他点安装之前回答掉，
                  比装完再解释有效得多——而且这行本身就是「零前置条件」的承诺。
                  仅在内置运行时确实在位时显示，不留空话。 */}
              {status?.runtimeBundled && (
                <div
                  className="mb-2.5 flex items-start gap-2.5 rounded-xl border px-3.5 py-3"
                  style={{
                    background: 'color-mix(in srgb, var(--accent) 7%, transparent)',
                    borderColor: 'color-mix(in srgb, var(--accent) 26%, transparent)',
                  }}
                >
                  <ShieldCheck
                    size={14}
                    strokeWidth={2.2}
                    className="mt-px shrink-0 text-[var(--accent)]"
                  />
                  <span className="text-[12px] leading-relaxed text-[var(--text-dim)]">
                    本应用已内置独立的 Python 运行时，
                    <span className="text-[var(--text)]">无需事先安装 Python</span>
                    ，也不需要管理员权限。直接开始安装即可。
                  </span>
                </div>
              )}

              {/* ── Python 版本预检警告 ──
                  torch 的 GPU wheel 只发布到 cp313。若本机 Python 是 3.14，
                  完整档必然失败，而且报错是 pip 的
                  「Could not find a version ... (from versions: none)」——
                  与「镜像不可用」逐字相同，用户会误以为换源能解决、白白反复折腾。
                  所以在点击安装**之前**就把原因说清楚。

                  只在该档位真的需要 torch 时提示：基础档不装 torch，
                  本机 Python 是几都无所谓，提示了反而是噪音。 */}
              {status?.pythonCompat && !status.pythonCompat.ok && needsTorch && (
                <div
                  className="mb-2.5 flex items-start gap-2.5 rounded-xl border px-3.5 py-3"
                  style={{
                    background: 'color-mix(in srgb, var(--warning) 8%, transparent)',
                    borderColor: 'color-mix(in srgb, var(--warning) 30%, transparent)',
                  }}
                >
                  <AlertTriangle
                    size={14}
                    strokeWidth={2.3}
                    className="mt-px shrink-0 text-[var(--warning)]"
                  />
                  <span className="text-[12px] leading-relaxed text-[var(--text-dim)]">
                    {status.pythonCompat.detail}
                  </span>
                </div>
              )}

              <div className="flex flex-col gap-2.5">
                {tiers.map((t) => {
                  const active = tier === t.id
                  return (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => setTier(t.id)}
                      aria-pressed={active}
                      className="group flex w-full items-start gap-3 rounded-xl border p-3.5 text-left transition-[background-color,border-color,box-shadow] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)] active:scale-[0.97]"
                      style={{
                        background: active ? 'var(--accent-soft)' : 'var(--surface)',
                        borderColor: active ? 'var(--accent)' : 'var(--border)',
                        boxShadow: active
                          ? '0 0 0 1px var(--accent), 0 6px 20px -10px var(--accent-border)'
                          : 'none',
                      }}
                    >
                      <span
                        className="mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full border transition-colors duration-150"
                        style={{
                          borderColor: active ? 'var(--accent)' : 'var(--border-strong)',
                          background: active ? 'var(--accent)' : 'transparent',
                        }}
                      >
                        {active && (
                          <Check size={11} strokeWidth={3} className="text-[var(--accent-contrast)]" />
                        )}
                      </span>
                      <span className="flex-1">
                        <span className="flex items-center gap-2">
                          <span className="text-[13.5px] font-medium text-[var(--text)]">
                            {t.label}
                          </span>
                          {t.id === 'full' && (
                            <span
                              className="rounded px-1.5 py-px text-[10px] font-medium"
                              style={{ background: 'var(--accent)', color: 'var(--accent-contrast)' }}
                            >
                              推荐
                            </span>
                          )}
                        </span>
                        <span className="mt-1 block text-[12px] leading-relaxed text-[var(--text-dim)]">
                          {t.desc}
                        </span>
                        {/* 能做 / 不能做。
                            小白判断不了「200MB vs 5.2GB」该选哪个 ——
                            告诉他**选完能干什么**才是可决策的信息。
                            「不能做」必须显式列出，否则用户装完才发现缺功能。 */}
                        <span className="mt-2 flex flex-col gap-1">
                          {(t.can ?? []).map((c) => (
                            <span
                              key={c}
                              className="flex items-start gap-1.5 text-[11.5px] leading-snug text-[var(--text-dim)]"
                            >
                              <Check
                                size={11}
                                strokeWidth={3}
                                className="mt-[3px] shrink-0 text-[var(--accent)]"
                              />
                              {c}
                            </span>
                          ))}
                          {(t.cannot ?? []).map((c) => (
                            <span
                              key={c}
                              className="flex items-start gap-1.5 text-[11.5px] leading-snug text-[var(--text-faint)]"
                            >
                              <X size={11} strokeWidth={3} className="mt-[3px] shrink-0" />
                              {c}
                            </span>
                          ))}
                        </span>
                      </span>
                    </button>
                  )
                })}
              </div>

              {/* ── 下载源 ──
                  PyTorch 的 CUDA 包约 2.5GB，官方源在国内常年几十 KB/s。
                  这是首启安装最容易劝退的一步，所以把源选择放到明面上。 */}
              {mirrors.length > 1 && (
                <div className="mt-5">
                  <div className="mb-2 flex items-center gap-1.5">
                    <Globe size={12} strokeWidth={2.2} className="text-[var(--text-faint)]" />
                    <span className="text-[12px] font-medium text-[var(--text-dim)]">
                      下载源
                    </span>
                  </div>
                  <div className="flex flex-col gap-1.5">
                    {mirrors.map((m) => {
                      const on = mirror === m.id
                      return (
                        <button
                          key={m.id}
                          type="button"
                          onClick={() => setMirror(m.id)}
                          aria-pressed={on}
                          className="flex items-start gap-2.5 rounded-lg border px-3 py-2 text-left transition-[background-color,border-color] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)] active:scale-[0.98]"
                          style={{
                            background: on ? 'var(--accent-soft)' : 'var(--surface)',
                            borderColor: on ? 'var(--accent)' : 'var(--border)',
                          }}
                        >
                          <span
                            className="mt-0.5 grid h-3.5 w-3.5 shrink-0 place-items-center rounded-full border"
                            style={{
                              borderColor: on ? 'var(--accent)' : 'var(--border-strong)',
                              background: on ? 'var(--accent)' : 'transparent',
                            }}
                          >
                            {on && (
                              <Check size={9} strokeWidth={3.5} className="text-[var(--accent-contrast)]" />
                            )}
                          </span>
                          <span className="flex-1">
                            <span className="block text-[12.5px] text-[var(--text)]">
                              {m.label}
                            </span>
                            <span className="block text-[11px] leading-snug text-[var(--text-faint)]">
                              {m.hint}
                            </span>
                          </span>
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}

              {/* ── 安装位置 ──
                  完整档约 5.2GB。若 C 盘紧张，用户必须能装到别的盘 ——
                  位置写死在 %LOCALAPPDATA%（C 盘）是不合理的。 */}
              <div className="mt-5">
                <div className="mb-2 flex items-center gap-1.5">
                  <HardDrive size={12} strokeWidth={2.2} className="text-[var(--text-faint)]" />
                  <span className="text-[12px] font-medium text-[var(--text-dim)]">
                    安装位置
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <div
                    className="min-w-0 flex-1 truncate rounded-lg border px-3 py-2 text-[12px]"
                    style={{
                      background: 'var(--surface)',
                      borderColor: 'var(--border)',
                      color: targetDir ? 'var(--text)' : 'var(--text-dim)',
                    }}
                    title={targetDir || status?.engineDir || ''}
                  >
                    {targetDir || status?.engineDir || '默认位置'}
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      void (async () => {
                        const picked = await pickInstallDir()
                        if (picked) setTargetDir(picked)
                      })()
                    }}
                    className="shrink-0 rounded-lg border px-3 py-2 text-[12px] transition-[background-color,transform] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)] active:scale-[0.97]"
                    style={{ borderColor: 'var(--border)', color: 'var(--text-dim)' }}
                  >
                    浏览…
                  </button>
                  {targetDir && (
                    <button
                      type="button"
                      onClick={() => setTargetDir('')}
                      className="shrink-0 rounded-lg border px-2.5 py-2 text-[12px] transition-[transform] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)] active:scale-[0.97]"
                      style={{ borderColor: 'var(--border)', color: 'var(--text-faint)' }}
                    >
                      默认
                    </button>
                  )}
                </div>
                <p className="mt-1.5 text-[11px] leading-snug text-[var(--text-faint)]">
                  完整档需约 7.3GB 空间，请确保所选磁盘余量充足。
                </p>
              </div>

              <button
                type="button"
                onClick={() => void startInstall()}
                className="mt-6 inline-flex h-10 items-center justify-center gap-2 rounded-lg px-5 text-[13px] font-medium transition-[background-color,transform] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)] active:scale-[0.97]"
                style={{ background: 'var(--accent)', color: 'var(--accent-contrast)' }}
              >
                <Download size={15} strokeWidth={2.2} />
                {needsUpgrade ? '加装缺失功能' : '开始安装'}
              </button>

              <p className="mt-4 flex items-start gap-1.5 text-[11.5px] leading-relaxed text-[var(--text-faint)]">
                <ShieldCheck size={13} strokeWidth={2} className="mt-px shrink-0" />
                {targetDir
                  ? `安装位置：${targetDir}`
                  : `默认位置：${status?.engineDir ?? '程序数据目录'}`}
              </p>

              {/* 无论引擎是否就绪，都留一条退路。
                  否则用户一旦从这里进来（如点「加装」），就再也回不去主界面 —— 
                  主上实测反馈过「还无法返回」。
                  初版这里写着 status?.ready 条件，引擎没装成时按钮根本不出现，
                  用户被「困死」在引导页；窗口又是无边框的，连关闭都要靠任务管理器。
                  困死是比「功能暂不可用」严重得多的问题，故去掉条件。 */}
              <button
                type="button"
                onClick={onReady}
                className="mt-3 inline-flex h-9 w-fit items-center gap-1.5 rounded-lg border px-3.5 text-[12.5px] transition-[background-color,transform] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)] active:scale-[0.97]"
                style={{ borderColor: 'var(--border)', color: 'var(--text-dim)' }}
              >
                <RotateCcw size={13} strokeWidth={2.2} />
                返回主界面
              </button>
            </>
          )}

          {/* ── 安装中 ── */}
          {phase === 'installing' && (
            <>
              <header className="mb-6">
                <h1 className="text-[20px] font-semibold tracking-[-0.02em] text-[var(--text)]">
                  正在安装引擎
                </h1>
                <p className="mt-1.5 text-[13px] text-[var(--text-dim)]">
                  {tier === 'basic'
                    ? '基础档约 200MB，通常 1-2 分钟。'
                    : '完整档需下载约 2.5GB 的 PyTorch，请保持网络畅通（已选国内镜像会快很多）。'}
                </p>
              </header>

              <div className="flex flex-col gap-2">
                <div
                  className="h-1.5 w-full overflow-hidden rounded-full"
                  style={{ background: 'var(--border)' }}
                >
                  <div
                    className="h-full rounded-full transition-[width] duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]"
                    style={{ width: `${Math.round(pct * 100)}%`, background: 'var(--accent)' }}
                  />
                </div>
                <div className="flex items-baseline justify-between">
                  <span className="text-[12px] text-[var(--text-dim)]">
                    {message || '准备中…'}
                  </span>
                  <span className="font-mono text-[12px] tabular-nums text-[var(--text-faint)]">
                    {(pct * 100).toFixed(0)}%
                  </span>
                </div>
              </div>

              <div
                ref={logRef}
                className="mt-5 h-40 overflow-y-auto rounded-lg border p-3 font-mono text-[11px] leading-relaxed"
                style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}
              >
                {logs.length === 0 ? (
                  <span className="text-[var(--text-faint)]">等待输出…</span>
                ) : (
                  logs.map((l, i) => (
                    <div key={i} className="break-all text-[var(--text-dim)]">
                      {l}
                    </div>
                  ))
                )}
              </div>

              <button
                type="button"
                onClick={() => void abort()}
                className="mt-5 inline-flex h-9 w-fit items-center gap-1.5 rounded-lg border px-3.5 text-[12.5px] transition-[background-color,transform] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)] active:scale-[0.97]"
                style={{ borderColor: 'var(--border)', color: 'var(--text-dim)' }}
              >
                <X size={13} strokeWidth={2.2} />
                取消安装
              </button>
            </>
          )}

          {/* ── 失败 ── */}
          {phase === 'failed' && (
            <div className="flex flex-col items-start gap-4">
              <div className="flex items-center gap-2.5">
                <div className="grid h-9 w-9 place-items-center rounded-full bg-[var(--danger-soft)]">
                  <AlertTriangle size={17} strokeWidth={2.2} className="text-[var(--warning)]" />
                </div>
                <h2 className="text-[16px] font-semibold text-[var(--text)]">
                  安装未完成
                </h2>
              </div>
              <pre className="w-full whitespace-pre-wrap break-words rounded-lg border p-3.5 font-mono text-[11.5px] leading-relaxed text-[var(--text-dim)]"
                   style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
                {error}
              </pre>
              {/* 三条出路缺一不可。
                  踩坑实录（主上实测「还无法返回」）：初版这里只有
                  「重新检测 / 重新安装」，且返回主界面的按钮被 `status?.ready`
                  挡住 —— 引擎没装成就永远走不掉，用户被**困死**在引导页。
                  窗口又是无边框的，连关闭都要靠任务管理器。
                  正解：失败时也一律给出退路。 */}
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => {
                    // probe 不再自己切 phase（避免 effect 内同步 setState），
                    // 手动重测时由这里先亮出「检测中」。
                    setPhase('checking')
                    void probe()
                  }}
                  className="inline-flex h-9 items-center gap-1.5 rounded-lg border px-3.5 text-[12.5px] transition-[transform] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)] active:scale-[0.97]"
                  style={{ borderColor: 'var(--border)', color: 'var(--text-dim)' }}
                >
                  <Cpu size={13} strokeWidth={2.2} />
                  重新检测
                </button>
                <button
                  type="button"
                  onClick={() => setPhase('choosing')}
                  className="inline-flex h-9 items-center gap-1.5 rounded-lg px-3.5 text-[12.5px] font-medium transition-[transform] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)] active:scale-[0.97]"
                  style={{ background: 'var(--accent)', color: 'var(--accent-contrast)' }}
                >
                  <Sparkles size={13} strokeWidth={2.2} />
                  换档位 / 换位置重来
                </button>
                <button
                  type="button"
                  onClick={onReady}
                  className="inline-flex h-9 items-center gap-1.5 rounded-lg border px-3.5 text-[12.5px] transition-[transform] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)] active:scale-[0.97]"
                  style={{ borderColor: 'var(--border)', color: 'var(--text-dim)' }}
                >
                  <RotateCcw size={13} strokeWidth={2.2} />
                  返回主界面
                </button>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  )
}
