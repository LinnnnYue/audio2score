/**
 * Settings.tsx — 设置页
 *
 * ## 为什么是整页而非浮层
 * 这里承载「引擎目录迁移」——5.4GB 的搬运，带进度条、日志流、
 * 失败回滚说明。浮层放不下这些，也容易被误关（窗口无边框，
 * 一旦误关就只剩一个半迁移的目录）。
 *
 * ## 为什么设置页不跟随 engineEpoch 重挂载
 * App 的 `main` 里，功能页树套了 `key={engineEpoch}` —— 引擎状态被改写后
 * 必须让它们重拉 env。但设置页自己就是**发起改写的那一方**：
 * 迁移一完成就把自己重建，用户刚看到的「迁移完成 / 新位置」会瞬间消失，
 * 只剩一个看起来毫无变化（因为 status 还在异步路上）的旧路径。
 * 故 App 侧只对功能页树套 key，设置页常驻。详见 App.tsx 内容区注释。
 *
 * ## 交互原则
 * 迁移期间**不给取消按钮**。bootstrap 的清理逻辑（删半成品 / rename 回滚）
 * 靠进程内异常捕获，进程被 kill 时根本没机会执行，只会在目标盘留一份
 * 不完整的 5.4GB。与其给一个会造成脏数据的按钮，不如把风险写在按钮旁边。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  BookOpen,
  Check,
  Cpu,
  ExternalLink,
  FolderOpen,
  Globe,
  HardDrive,
  Loader2,
  Package,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  X,
} from 'lucide-react'
import clsx from 'clsx'
import { Section } from '../components/Section'
import {
  migrateEngine,
  onInstallDone,
  onInstallLog,
  onInstallProgress,
  pickInstallDir,
  revealInFolder,
  type EngineStatus,
} from '../lib/ipc'
import { DOCS_URL, GITHUB_URL, openExternal, SITE_URL } from '../lib/links'

type MovePhase = 'idle' | 'confirm' | 'moving' | 'done' | 'failed'

interface Props {
  /** 引擎状态。null = 尚未探测到（安装刚完成时 App 会先置 null 再异步刷新）。 */
  status: EngineStatus | null
  /** 打开引导页补装功能（走与首次运行完全相同的向导）。 */
  onUpgrade: () => void
  /** 重新确认引擎状态；引擎位置/能力发生变化后必须调。 */
  onRefresh: () => void
  /** 重看新手指引（含命令行与 AI 助手接入）。 */
  onShowGuide: () => void
}

/**
 * 能力清单。key 对应 EngineStatus 上的布尔字段。
 *
 * 刻意由前端列出「该有哪些能力」而不是遍历 status 的字段：
 * 后端将来加字段（如 ffmpeg）不会悄悄多出一行，需要显式在此登记文案。
 */
const CAPS: { key: 'basic' | 'demucs' | 'cuda' | 'mcp'; label: string; hint: string }[] = [
  { key: 'basic', label: '音频直扒', hint: '单轨 / 多轨音符识别' },
  { key: 'demucs', label: '人声与伴奏分离', hint: '歌曲双轨模式的依赖' },
  { key: 'cuda', label: 'GPU 加速', hint: '分离与识别显著提速' },
  { key: 'mcp', label: 'AI 助手调用', hint: '供外部助手连接引擎' },
]

export function SettingsPage({ status, onUpgrade, onRefresh, onShowGuide }: Props) {
  const [phase, setPhase] = useState<MovePhase>('idle')
  /** 用户选定的目标目录（confirm / moving 阶段显示） */
  const [targetDir, setTargetDir] = useState('')
  const [pct, setPct] = useState(0)
  const [message, setMessage] = useState('')
  const [logs, setLogs] = useState<string[]>([])
  const [error, setError] = useState('')
  /** 结果说明（后端结构化结果里的 message），与 error 二选一 */
  const [outcome, setOutcome] = useState('')
  /** 「在资源管理器中打开」这类轻动作的失败提示，不占 error 位 */
  const [note, setNote] = useState('')
  /** 「帮助与关于」唤起浏览器失败时的提示（附链接原文，便于手动复制） */
  const [helpNote, setHelpNote] = useState('')
  const logRef = useRef<HTMLDivElement>(null)

  const ready = status?.ready ?? false
  const engineDir = status?.engineDir ?? ''
  const missing = status?.missing ?? []
  const busy = phase === 'moving'

  /* ── 迁移事件订阅 ──
     复用安装的那套通道（install://progress / log / done）。
     只在 moving 阶段订阅：成功/失败后立刻退订，避免回调打到已结束的流程上。 */
  useEffect(() => {
    if (phase !== 'moving') return
    const cleanups: Array<() => void> = []

    void onInstallProgress((e) => {
      setPct(e.pct)
      setMessage(e.message)
    }).then((f) => cleanups.push(f))

    void onInstallLog((e) => {
      // 只留最后 200 行：跨盘复制会有上万行，无限增长会拖死渲染
      setLogs((prev) => [...prev.slice(-200), e.message])
    }).then((f) => cleanups.push(f))

    void onInstallDone((e) => {
      if (e.ok) {
        setOutcome(e.message ?? '引擎已迁移到新位置。')
        setPhase('done')
        /* ⚠️ 位置变了 = 引擎能力探测的前提全变（解释器路径换了）。
           必须立刻让 App 层重新确认状态：否则 01 块还写着旧路径，
           功能页的 Demucs / CUDA 结果也可能落在旧引擎上。
           设置页不随 engineEpoch 重挂载，所以本次刷新不会冲掉上面的完成提示。 */
        onRefresh()
      } else {
        const tail = typeof e.logTail === 'string' && e.logTail.trim() ? e.logTail.trim() : ''
        const head =
          e.message ||
          (e.outcomeMissing
            ? '迁移进程异常退出，没有返回结果。引擎已回滚到原位置，可重试。'
            : '迁移失败。')
        setError(tail ? `${head}\n\n— 最后一条日志 —\n${tail}` : head)
        setPhase('failed')
      }
    }).then((f) => cleanups.push(f))

    return () => cleanups.forEach((f) => f())
  }, [phase, onRefresh])

  /* 日志自动滚到底 */
  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' })
  }, [logs])

  /** 选目录 → 进入确认态。真正动手前一定让用户再看一眼「从哪搬到哪」。 */
  const chooseTarget = useCallback(async () => {
    setNote('')
    try {
      const picked = await pickInstallDir()
      if (!picked) return
      setTargetDir(picked)
      setError('')
      setPhase('confirm')
    } catch (e) {
      setNote(String(e))
    }
  }, [])

  const startMove = useCallback(async () => {
    if (!targetDir) return
    setPhase('moving')
    setPct(0)
    setLogs([])
    setError('')
    setMessage('正在准备…')
    try {
      await migrateEngine(targetDir)
    } catch (e) {
      setError(String(e))
      setPhase('failed')
    }
  }, [targetDir])

  const openFolder = useCallback(async () => {
    if (!engineDir) return
    setNote('')
    try {
      await revealInFolder(engineDir)
    } catch (e) {
      setNote(String(e))
    }
  }, [engineDir])

  /**
   * 用系统默认浏览器打开外部链接。
   *
   * ⚠️ 失败必须给出路 —— 这类「唤起浏览器」失败在用户看来就是
   * 「按钮点了没反应」。所以把链接原文摆出来，让他能手动复制粘贴。
   * （界面不允许「无出路的终态」。）
   */
  const openLink = useCallback(async (url: string) => {
    setHelpNote('')
    try {
      await openExternal(url)
    } catch (e) {
      setHelpNote(`没能唤起浏览器（${String(e)}）。可手动复制链接：${url}`)
    }
  }, [])

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[860px] flex-col gap-7 px-8 py-7">
        <header>
          <h1 className="text-[17px] font-semibold tracking-[-0.02em] text-ink">设置</h1>
          <p className="mt-1 text-[12px] leading-snug text-ink-dim">
            引擎状态、安装位置与档位补装。改动只影响本机，不涉及任何在线账号。
          </p>
        </header>

        {/* ═══════════════ 01 引擎状态 ═══════════════ */}
        <Section
          index="01"
          title="引擎状态"
          aside={
            status === null ? (
              <span className="flex items-center gap-1.5 text-[10.5px] text-ink-faint">
                <Loader2 size={11} className="animate-spin" />
                读取中
              </span>
            ) : (
              <span
                className={clsx(
                  'flex items-center gap-1 rounded-full px-2 py-[3px] text-[10.5px] font-medium',
                  ready ? 'bg-accent-soft text-accent' : 'bg-bad-soft text-bad',
                )}
              >
                {ready ? (
                  <Check size={10} strokeWidth={3} />
                ) : (
                  <AlertTriangle size={10} strokeWidth={2.6} />
                )}
                {ready ? '已就绪' : '未安装'}
              </span>
            )
          }
        >
          {status === null ? (
            <div className="rounded-lg border border-line bg-surface px-3.5 py-3 text-[12px] text-ink-faint">
              正在读取引擎状态…
            </div>
          ) : (
            <>
              {/* 就绪与否一句话说清；未就绪时把后端给的原因原样端出来 */}
              <p className="text-[12.5px] leading-relaxed text-ink-dim">
                {ready
                  ? `引擎可用${status.python ? `（Python ${status.python}）` : ''}。`
                  : status.reason || '尚未安装引擎，主界面的功能暂不可用。'}
              </p>

              {/* 能力清单：小白判断不了「装了 torch 没有」，只看得懂「能不能分离人声」 */}
              <div className="mt-3 grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                {CAPS.map((c) => {
                  const on = Boolean(status[c.key])
                  return (
                    <div
                      key={c.key}
                      className="flex items-center gap-2.5 rounded-lg border border-line bg-surface px-3 py-2"
                    >
                      <span
                        className={clsx(
                          'grid h-4 w-4 shrink-0 place-items-center rounded-full',
                          on ? 'bg-accent-soft text-accent' : 'bg-bg-elev text-ink-faint',
                        )}
                      >
                        {on ? (
                          <Check size={10} strokeWidth={3.4} />
                        ) : (
                          <X size={10} strokeWidth={3} />
                        )}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span
                          className={clsx(
                            'block text-[12.5px]',
                            on ? 'text-ink' : 'text-ink-faint',
                          )}
                        >
                          {c.label}
                        </span>
                        <span className="block text-[11px] leading-snug text-ink-faint">
                          {c.hint}
                        </span>
                      </span>
                    </div>
                  )
                })}
              </div>

              <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] text-ink-faint">
                <span className="flex items-center gap-1.5">
                  <Cpu size={11} strokeWidth={2.2} />
                  设备 {status.cuda ? 'GPU（CUDA）' : 'CPU'}
                </span>
                <span className="num">
                  torch {status.torchVersion ? status.torchVersion : '未安装'}
                </span>
              </div>
            </>
          )}
        </Section>

        {/* ═══════════════ 02 引擎位置 ═══════════════ */}
        <Section
          index="02"
          title="引擎位置"
          aside={
            <span className="text-[10.5px] text-ink-faint">
              完整档约 5.4GB，可迁到别的盘
            </span>
          }
        >
          {!ready ? (
            <div className="rounded-lg border border-line bg-surface px-3.5 py-3 text-[12px] text-ink-dim">
              安装引擎后，这里可以查看并把引擎整体搬到别的磁盘。
            </div>
          ) : (
            <>
              {/* 当前路径。等宽 + 单行截断：路径往往很长，换行会顶乱版面 */}
              <div className="flex items-center gap-2">
                <div
                  className="min-w-0 flex-1 truncate rounded-lg border border-line bg-surface px-3 py-2 font-mono text-[11.5px] text-ink"
                  title={engineDir}
                >
                  {engineDir || '未知位置'}
                </div>
                <button
                  type="button"
                  onClick={() => void openFolder()}
                  disabled={busy || !engineDir}
                  title="在资源管理器中打开"
                  className="btn shrink-0"
                >
                  <FolderOpen size={13} strokeWidth={2.1} />
                  打开
                </button>
                <button
                  type="button"
                  onClick={() => void chooseTarget()}
                  disabled={busy}
                  className="btn shrink-0"
                >
                  <HardDrive size={13} strokeWidth={2.1} />
                  更换位置
                </button>
              </div>

              {note && (
                <p className="mt-2 text-[11.5px] leading-snug text-bad">{note}</p>
              )}

              {/* ── 确认：让用户再看一眼「从哪搬到哪」 ── */}
              {phase === 'confirm' && (
                <div className="mt-3 animate-rise-in rounded-lg border border-accent-line bg-accent-soft px-3.5 py-3">
                  <p className="text-[12.5px] font-medium text-ink">确认迁移引擎？</p>
                  <div className="mt-2 flex flex-col gap-1 font-mono text-[11px] leading-relaxed text-ink-dim">
                    <span className="break-all">从　{engineDir}</span>
                    <span className="break-all">搬到 {targetDir}</span>
                  </div>
                  <ul className="mt-2.5 flex flex-col gap-1 text-[11.5px] leading-snug text-ink-dim">
                    <li className="flex items-start gap-1.5">
                      <ShieldCheck size={12} strokeWidth={2.2} className="mt-[3px] shrink-0 text-accent" />
                      同一磁盘内是瞬间移动；跨磁盘需逐个文件复制，可能要几分钟。
                    </li>
                    <li className="flex items-start gap-1.5">
                      <ShieldCheck size={12} strokeWidth={2.2} className="mt-[3px] shrink-0 text-accent" />
                      期间请勿关闭窗口或关机 —— 中断会在目标盘留下一份不完整的副本。
                    </li>
                    <li className="flex items-start gap-1.5">
                      <ShieldCheck size={12} strokeWidth={2.2} className="mt-[3px] shrink-0 text-accent" />
                      完成后会用新位置真实运行一次校验；不通过则自动回滚到原位置。
                    </li>
                  </ul>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => void startMove()}
                      className="btn btn-primary"
                    >
                      <HardDrive size={13} strokeWidth={2.1} />
                      开始迁移
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setPhase('idle')
                        setTargetDir('')
                      }}
                      className="btn btn-ghost"
                    >
                      取消
                    </button>
                  </div>
                </div>
              )}

              {/* ── 迁移中 ── */}
              {phase === 'moving' && (
                <div className="mt-3 animate-rise-in">
                  <div className="flex flex-col gap-2">
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-line">
                      <div
                        className="h-full rounded-full bg-accent transition-[width] duration-300 ease-out"
                        style={{ width: `${Math.round(pct * 100)}%` }}
                      />
                    </div>
                    <div className="flex items-baseline justify-between">
                      <span className="text-[12px] text-ink-dim">{message || '准备中…'}</span>
                      <span className="num text-[12px] text-ink-faint">
                        {(pct * 100).toFixed(0)}%
                      </span>
                    </div>
                  </div>
                  <div
                    ref={logRef}
                    className="mt-3 h-32 overflow-y-auto rounded-lg border border-line bg-surface p-3 font-mono text-[11px] leading-relaxed"
                  >
                    {logs.length === 0 ? (
                      <span className="text-ink-faint">等待输出…</span>
                    ) : (
                      logs.map((l, i) => (
                        <div key={i} className="break-all text-ink-dim">
                          {l}
                        </div>
                      ))
                    )}
                  </div>
                  <p className="mt-2 flex items-start gap-1.5 text-[11.5px] leading-snug text-ink-faint">
                    <AlertTriangle size={12} strokeWidth={2.2} className="mt-[3px] shrink-0" />
                    迁移期间请勿关闭窗口；此界面不提供中断，以免留下不完整的引擎副本。
                  </p>
                </div>
              )}

              {/* ── 成功 ── */}
              {phase === 'done' && (
                <div className="mt-3 flex animate-rise-in items-start gap-2.5 rounded-lg border border-line bg-surface px-3.5 py-3">
                  <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-accent-soft text-accent">
                    <Check size={13} strokeWidth={3} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-[12.5px] text-ink">{outcome || '迁移完成。'}</p>
                    <p className="mt-0.5 break-all font-mono text-[11px] text-ink-faint">
                      新位置：{targetDir}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      setPhase('idle')
                      setTargetDir('')
                    }}
                    className="btn btn-ghost shrink-0"
                  >
                    知道了
                  </button>
                </div>
              )}

              {/* ── 失败 ── */}
              {phase === 'failed' && (
                <div className="mt-3 animate-rise-in">
                  <div className="flex items-start gap-2.5 rounded-lg border border-line bg-surface px-3.5 py-3">
                    <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-bad-soft text-bad">
                      <AlertTriangle size={13} strokeWidth={2.4} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-[12.5px] font-medium text-ink">迁移未完成</p>
                      <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[11.5px] leading-relaxed text-ink-dim">
                        {error || '未知错误。'}
                      </pre>
                    </div>
                  </div>
                  <div className="mt-2.5 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => void chooseTarget()}
                      className="btn"
                    >
                      <HardDrive size={13} strokeWidth={2.1} />
                      换个位置重试
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setPhase('idle')
                        setTargetDir('')
                      }}
                      className="btn btn-ghost"
                    >
                      关闭
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </Section>

        {/* ═══════════════ 03 功能档位 ═══════════════ */}
        <Section
          index="03"
          title="功能档位"
          aside={
            <span className="text-[10.5px] text-ink-faint">
              {missing.length > 0 ? `缺少 ${missing.length} 项能力` : '已装齐'}
            </span>
          }
        >
          <p className="text-[12.5px] leading-relaxed text-ink-dim">
            {!ready
              ? '先安装引擎，主界面的扒谱功能才可用。'
              : missing.length > 0
                ? '当前引擎缺少部分能力，可在引导页按档位加装 —— 已装的部分会跳过，不必重新下载。'
                : '当前引擎能力齐备。若需重装或换档位，也可从引导页重新选择。'}
          </p>

          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={onUpgrade}
              disabled={busy}
              className={clsx('btn', ready && missing.length > 0 ? 'btn-primary' : '')}
            >
              {missing.length > 0 ? (
                <Sparkles size={13} strokeWidth={2.1} />
              ) : (
                <Package size={13} strokeWidth={2.1} />
              )}
              {!ready ? '安装引擎' : missing.length > 0 ? '加装缺失功能' : '重新选择档位'}
            </button>
            {status !== null && (
              <button
                type="button"
                onClick={onRefresh}
                disabled={busy}
                className="btn btn-ghost"
              >
                <RefreshCw size={13} strokeWidth={2.1} />
                重新检测
              </button>
            )}
          </div>
        </Section>

        {/* ═══════════════ 04 使用指引 ═══════════════ */}
        <Section
          index="04"
          title="使用指引"
          aside={<span className="text-[10.5px] text-ink-faint">命令行 · AI 助手</span>}
        >
          <p className="text-[12.5px] leading-relaxed text-ink-dim">
            除了在界面里点，引擎还能被<span className="text-ink">命令行</span>
            和<span className="text-ink">AI 助手</span>调用——
            批量处理整个文件夹、或让助手代劳时用得上。
            指引里的命令与配置都是按你这台机器的实际安装位置生成的，可一键复制。
          </p>

          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" onClick={onShowGuide} disabled={busy} className="btn btn-primary">
              <BookOpen size={13} strokeWidth={2.1} />
              查看新手指引
            </button>
          </div>
        </Section>

        {/* ═══════════════ 05 帮助与关于 ═══════════════ */}
        <Section
          index="05"
          title="帮助与关于"
          aside={<span className="text-[10.5px] text-ink-faint">用浏览器打开</span>}
        >
          <p className="text-[12.5px] leading-relaxed text-ink-dim">
            说明文档把每个参数讲透了 ——「往左拖会怎样、往右拖会怎样、
            什么时候才该动它」，与软件里每个旋钮旁边的提示同源。
            官网首页有最新版本的下载入口。
          </p>

          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void openLink(DOCS_URL)}
              className="btn btn-primary"
            >
              <BookOpen size={13} strokeWidth={2.1} />
              说明文档
              <ExternalLink size={11} strokeWidth={2.2} />
            </button>
            <button type="button" onClick={() => void openLink(SITE_URL)} className="btn">
              <Globe size={13} strokeWidth={2.1} />
              官网首页
              <ExternalLink size={11} strokeWidth={2.2} />
            </button>
            <button
              type="button"
              onClick={() => void openLink(GITHUB_URL)}
              className="btn btn-ghost"
            >
              源码仓库
              <ExternalLink size={11} strokeWidth={2.2} />
            </button>
          </div>

          {helpNote && (
            <p className="mt-2.5 text-[11.5px] leading-relaxed break-all text-bad">{helpNote}</p>
          )}
        </Section>
      </div>
    </div>
  )
}
