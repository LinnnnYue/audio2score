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
 *   基础 basic  ~200MB  1-2 min   基本扒谱，不含人声分离
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
  Loader2,
  Package,
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
  type EngineStatus,
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
  const logRef = useRef<HTMLDivElement>(null)

  /* ── 首启检测 ── */
  const probe = useCallback(async () => {
    setPhase('checking')
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

    void onInstallDone(() => {
      void probe()
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
      const r = await installEngine(tier, mirror)
      setTaskId(r.taskId)
      setMessage('正在准备…')
    } catch (e) {
      setError(String(e))
      setPhase('failed')
    }
  }, [tier, mirror])

  const abort = useCallback(async () => {
    if (taskId) await cancelInstall(taskId)
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
                安装位置：{status?.engineDir ?? '%LOCALAPPDATA%\\bapu\\engine'}
                （用户级，无需管理员权限）
              </p>
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
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => void probe()}
                  className="inline-flex h-9 items-center gap-1.5 rounded-lg border px-3.5 text-[12.5px] transition-[transform] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)] active:scale-[0.97]"
                  style={{ borderColor: 'var(--border)', color: 'var(--text-dim)' }}
                >
                  <Cpu size={13} strokeWidth={2.2} />
                  重新检测
                </button>
                <button
                  type="button"
                  onClick={() => void startInstall()}
                  className="inline-flex h-9 items-center gap-1.5 rounded-lg px-3.5 text-[12.5px] font-medium transition-[transform] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)] active:scale-[0.97]"
                  style={{ background: 'var(--accent)', color: 'var(--accent-contrast)' }}
                >
                  <Sparkles size={13} strokeWidth={2.2} />
                  重新安装
                </button>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  )
}
