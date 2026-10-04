/**
 * Onboarding.tsx — 新手指引弹窗
 *
 * ## 为什么需要它
 * 这个应用有三种用法，而图形界面只是其中一种：**命令行**（批量/脚本）
 * 与 **MCP**（供 AI 助手调用）都从同一份引擎出发，但它们的存在对小白
 * 完全不可见——文件就在安装目录里，却没人告诉他。
 *
 * 更麻烦的是 MCP：它需要把一段**含绝对路径的 JSON** 填进别人的软件配置里，
 * 而引擎装在哪块盘、哪个目录是用户自己在引导页选的。仓库里那份
 * `mcp/bapu.json` 是开发机快照（写死了开发者路径，且反斜杠未转义），
 * 照抄必然连不上。故这份配置由后端按本机真实位置现算，这里只负责展示与复制。
 *
 * ## 与其他界面的关系（刻意不重叠）
 * - 引擎未就绪时**不显示**：此时用户该看的是安装向导，弹这个只会添乱。
 * - 首次进入主界面自动弹一次；之后可在「设置 → 新手指引」随时重看。
 * - 不用 modal 库、不接管滚动：整窗是个覆盖层，关掉即恢复。
 *
 * ## 内容来源（全部照抄真实契约，不允许臆想）
 * 八种模式名、CLI 参数、退出码、MCP 工具名分别取自
 * `engine/pipeline.py`、`engine/cli.py`、`engine/mcp_server.py`。
 * 若上游改了这些，本文件必须同步——这正是把它做成单一组件的原因。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ArrowRight,
  Bot,
  Check,
  ChevronLeft,
  ChevronRight,
  Copy,
  FolderOpen,
  Layers,
  MousePointerClick,
  Music4,
  Save,
  Sparkles,
  Terminal,
  X,
} from 'lucide-react'
import clsx from 'clsx'
import {
  exportMcpConfig,
  getIntegrationInfo,
  pickJsonSavePath,
  revealInFolder,
  type IntegrationInfo,
} from '../lib/ipc'
import { markOnboardingSeen } from '../lib/onboarding-store'

interface Props {
  /**
   * 关闭。
   *
   * 刻意**没有 `open` prop**：本组件由 App 条件渲染，挂载即显示、卸载即隐藏。
   * 这样「每次打开都回到第一页」是初始 state 的自然结果，
   * 不必在 effect 里重置（那会多出一次级联渲染，也会让「上次看到第几页」
   * 意外变成下次打开的前提）。
   */
  onClose: () => void
}

/* ── 复制按钮 ──────────────────────────────────────────────
   独立成组件是因为弹窗里有 3 处要复制，各自维护一份「已复制」状态
   比共用一个更稳：共用时复制第二处会清掉第一处的反馈，看着像失灵。 */
function CopyButton({ text, label = '复制' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false)
  const [failed, setFailed] = useState(false)
  const timer = useRef<number | null>(null)

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current)
    },
    [],
  )

  const copy = useCallback(async () => {
    let ok = false
    try {
      await navigator.clipboard.writeText(text)
      ok = true
    } catch {
      ok = false
    }
    setDone(ok)
    setFailed(!ok)
    if (timer.current !== null) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      setDone(false)
      setFailed(false)
    }, 1800)
  }, [text])

  return (
    <button
      type="button"
      onClick={() => void copy()}
      className="inline-flex h-[26px] shrink-0 items-center gap-1.5 rounded-[var(--r-sm)] border px-2.5 text-[11.5px] font-medium transition-[background-color,border-color,transform] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)] active:scale-[0.97]"
      style={{
        background: done ? 'var(--accent-soft)' : 'var(--surface)',
        borderColor: done ? 'var(--accent)' : 'var(--border)',
        color: done ? 'var(--accent)' : 'var(--text-dim)',
      }}
    >
      {done ? <Check size={12} strokeWidth={2.6} /> : <Copy size={12} strokeWidth={2} />}
      {failed ? '复制失败，请手动选中' : done ? '已复制' : label}
    </button>
  )
}

/* ── 代码块 ────────────────────────────────────────────── */
function CodeBlock({ code, copyable = true }: { code: string; copyable?: boolean }) {
  return (
    <div className="relative">
      <pre
        className="overflow-x-auto rounded-[var(--r-sm)] border px-3 py-2.5 text-[11.5px] leading-[1.7]"
        style={{
          background: 'var(--bg-elev)',
          borderColor: 'var(--border)',
          color: 'var(--text-dim)',
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
        }}
      >
        {code}
      </pre>
      {copyable && (
        <div className="absolute right-2 top-2">
          <CopyButton text={code} />
        </div>
      )}
    </div>
  )
}

/** 小标题 */
function H({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="mb-1.5 text-[12.5px] font-semibold tracking-[-0.01em] text-[var(--text)]">
      {children}
    </h3>
  )
}

/** 说一段话 */
function P({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[12.5px] leading-[1.75] text-[var(--text-dim)]">{children}</p>
  )
}

/** 键值行（参数速查 / 能力对照） */
function Rows({ items }: { items: [string, string][] }) {
  return (
    <div className="flex flex-col gap-px overflow-hidden rounded-[var(--r-sm)] border"
      style={{ borderColor: 'var(--border)' }}>
      {items.map(([k, v], i) => (
        <div
          key={k}
          className="flex items-baseline gap-3 px-3 py-1.5"
          style={{
            background: i % 2 === 0 ? 'var(--bg-elev)' : 'var(--surface)',
          }}
        >
          <code
            className="shrink-0 text-[11.5px] text-[var(--accent)]"
            style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' }}
          >
            {k}
          </code>
          <span className="text-[11.5px] leading-[1.7] text-[var(--text-dim)]">{v}</span>
        </div>
      ))}
    </div>
  )
}

/* ── 步骤定义 ────────────────────────────────────────────── */
const STEPS = [
  { id: 'welcome', title: '这是什么', icon: Sparkles },
  { id: 'gui', title: '界面扒谱', icon: MousePointerClick },
  { id: 'cli', title: '命令行', icon: Terminal },
  { id: 'mcp', title: '给 AI 用', icon: Bot },
] as const

export function Onboarding({ onClose }: Props) {
  const [step, setStep] = useState(0)
  const [info, setInfo] = useState<IntegrationInfo | null>(null)
  const [saveNote, setSaveNote] = useState('')
  const cardRef = useRef<HTMLDivElement>(null)

  /* 挂载时拉一次集成信息。
     它是两次 Python 冷启动（探测 mcp 依赖要真跑一次解释器），
     故只在打开这一次拉，不做常驻轮询。 */
  useEffect(() => {
    let alive = true
    void getIntegrationInfo()
      .then((v) => {
        if (alive) setInfo(v)
      })
      .catch(() => {
        if (alive) setInfo(null)
      })
    return () => {
      alive = false
    }
  }, [])

  /* 打开即聚焦卡片：键盘用户按 Esc / 方向键能直接生效，不必先点进来 */
  useEffect(() => {
    cardRef.current?.focus()
  }, [])

  /* Esc 关闭，左右方向键翻页 */
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        onClose()
      } else if (e.key === 'ArrowRight') {
        setStep((s) => Math.min(s + 1, STEPS.length - 1))
      } else if (e.key === 'ArrowLeft') {
        setStep((s) => Math.max(s - 1, 0))
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const finish = useCallback(() => {
    markOnboardingSeen()
    onClose()
  }, [onClose])

  const saveConfig = useCallback(async () => {
    if (!info) return
    setSaveNote('')
    try {
      const target = await pickJsonSavePath('bapu-mcp.json')
      if (!target) return
      const written = await exportMcpConfig(target)
      setSaveNote(`已保存：${written}`)
    } catch (e) {
      setSaveNote(`保存失败：${String(e)}`)
    }
  }, [info])

  const last = step === STEPS.length - 1

  return (
    <div
      className="absolute inset-0 z-50 flex items-center justify-center p-6"
      style={{ background: 'color-mix(in srgb, var(--bg) 62%, transparent)', backdropFilter: 'blur(3px)' }}
      onMouseDown={(e) => {
        // 只认「点在遮罩本身」——不做坐标计算，点在卡片里不会命中这里
        if (e.target === e.currentTarget) finish()
      }}
    >
      <div
        ref={cardRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="新手指引"
        className="flex max-h-full w-full max-w-[760px] flex-col overflow-hidden rounded-[var(--r-lg)] border outline-none"
        style={{
          background: 'var(--bg)',
          borderColor: 'var(--border)',
          boxShadow: '0 24px 64px -24px rgba(10, 30, 45, 0.42)',
        }}
      >
        {/* ── 头部：步骤指示 ── */}
        <div
          className="flex shrink-0 items-center gap-1 border-b px-4 py-3"
          style={{ borderColor: 'var(--border)' }}
        >
          {STEPS.map((s, i) => {
            const Icon = s.icon
            const active = i === step
            return (
              <button
                key={s.id}
                type="button"
                onClick={() => setStep(i)}
                aria-current={active ? 'step' : undefined}
                className={clsx(
                  'flex h-[28px] items-center gap-1.5 rounded-[var(--r-sm)] px-2.5 text-[12px] font-medium',
                  'transition-[background-color,color,transform] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)]',
                  'active:scale-[0.97]',
                )}
                style={{
                  background: active ? 'var(--accent-soft)' : 'transparent',
                  color: active ? 'var(--accent)' : 'var(--text-faint)',
                }}
              >
                <Icon size={13} strokeWidth={active ? 2.2 : 1.8} />
                {s.title}
              </button>
            )
          })}

          <div className="flex-1" />

          <button
            type="button"
            onClick={finish}
            aria-label="关闭"
            className="grid h-[26px] w-[26px] place-items-center rounded-[var(--r-sm)] text-[var(--text-faint)] transition-[background-color,color,transform] duration-150 active:scale-[0.94]"
          >
            <X size={15} strokeWidth={1.9} />
          </button>
        </div>

        {/* ── 内容区 ── */}
        <div className="min-h-0 flex-1 overflow-y-auto px-7 py-6">
          {step === 0 && <StepWelcome />}
          {step === 1 && <StepGui />}
          {step === 2 && <StepCli info={info} />}
          {step === 3 && (
            <StepMcp info={info} onSave={() => void saveConfig()} onReveal={revealInFolder} />
          )}
        </div>

        {/* ── 底部 ── */}
        <div
          className="flex shrink-0 items-center gap-2 border-t px-4 py-3"
          style={{ borderColor: 'var(--border)' }}
        >
          <span className="text-[11.5px] text-[var(--text-faint)]">
            {step + 1} / {STEPS.length}
          </span>
          {saveNote && (
            <span className="truncate text-[11.5px] text-[var(--text-dim)]" title={saveNote}>
              {saveNote}
            </span>
          )}
          <div className="flex-1" />

          <button
            type="button"
            disabled={step === 0}
            onClick={() => setStep((s) => Math.max(s - 1, 0))}
            className="inline-flex h-[30px] items-center gap-1 rounded-[var(--r-sm)] px-2.5 text-[12px] font-medium text-[var(--text-dim)] transition-[background-color,transform] duration-150 active:scale-[0.97] disabled:opacity-35"
          >
            <ChevronLeft size={14} strokeWidth={2} />
            上一页
          </button>

          {last ? (
            <button
              type="button"
              onClick={finish}
              className="inline-flex h-[30px] items-center gap-1.5 rounded-[var(--r-sm)] px-3.5 text-[12px] font-semibold transition-[background-color,transform] duration-150 active:scale-[0.97]"
              style={{ background: 'var(--accent)', color: 'var(--accent-contrast)' }}
            >
              开始使用
            </button>
          ) : (
            <button
              type="button"
              onClick={() => setStep((s) => Math.min(s + 1, STEPS.length - 1))}
              className="inline-flex h-[30px] items-center gap-1 rounded-[var(--r-sm)] px-3.5 text-[12px] font-semibold transition-[background-color,transform] duration-150 active:scale-[0.97]"
              style={{ background: 'var(--accent)', color: 'var(--accent-contrast)' }}
            >
              下一页
              <ChevronRight size={14} strokeWidth={2.2} />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

/* ══════════════════════════════════════════════════════════
 * 第 1 页 · 这是什么
 * ══════════════════════════════════════════════════════════ */
function StepWelcome() {
  const entries: { icon: typeof Music4; title: string; desc: string }[] = [
    {
      icon: MousePointerClick,
      title: '图形界面',
      desc: '拖入音频，选模式，点开始。日常用这个就够了。',
    },
    {
      icon: Terminal,
      title: '命令行',
      desc: '一条命令批量处理整个文件夹，适合写脚本或定时任务。',
    },
    {
      icon: Bot,
      title: 'AI 助手',
      desc: '接入 MCP 后，你可以直接对 AI 说「帮我扒这首歌」。',
    },
  ]

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h2 className="text-[19px] font-semibold tracking-[-0.02em] text-[var(--text)]">
          把音频变成乐谱
        </h2>
        <P>
          扒谱助手把音频（mp3 / wav / flac 等）分析成 MIDI 乐谱，
          MuseScore 等打谱软件可以直接打开、修改、打印。
          <br />
          三种用法共用同一个引擎，结果完全一致，按你的习惯挑一种就行。
        </P>
      </div>

      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-3">
        {entries.map((e) => {
          const Icon = e.icon
          return (
            <div
              key={e.title}
              className="rounded-[var(--r-md)] border p-3.5"
              style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}
            >
              <div
                className="mb-2 grid h-7 w-7 place-items-center rounded-[var(--r-sm)]"
                style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
              >
                <Icon size={15} strokeWidth={2} />
              </div>
              <div className="mb-1 text-[12.5px] font-semibold text-[var(--text)]">
                {e.title}
              </div>
              <div className="text-[11.5px] leading-[1.7] text-[var(--text-dim)]">
                {e.desc}
              </div>
            </div>
          )
        })}
      </div>

      <div
        className="rounded-[var(--r-sm)] border px-3.5 py-3"
        style={{
          background: 'color-mix(in srgb, var(--accent) 7%, transparent)',
          borderColor: 'color-mix(in srgb, var(--accent) 26%, transparent)',
        }}
      >
        <P>
          <span className="text-[var(--text)]">第一次分离人声时要多等一会儿</span>
          ：模型权重需要下载，之后就一直存在本机了。三分钟的歌，分离加扒谱大约
          20 到 60 秒，有独立显卡会更快。
        </P>
      </div>
    </div>
  )
}

/* ══════════════════════════════════════════════════════════
 * 第 2 页 · 界面扒谱
 * ══════════════════════════════════════════════════════════ */
function StepGui() {
  return (
    <div className="flex flex-col gap-5">
      <div>
        <h2 className="text-[19px] font-semibold tracking-[-0.02em] text-[var(--text)]">
          三步出谱
        </h2>
        <P>
          窗口上方两个标签页对应两类需求：
          <span className="text-[var(--text)]">歌曲扒谱</span>（先分离人声与伴奏，再识别）
          与 <span className="text-[var(--text)]">音频直扒</span>（对手头已有的音频直接识别，不分离）。
        </P>
      </div>

      <div className="flex flex-col gap-2">
        {[
          ['把音频拖进窗口', '也可以点空白处选择文件。支持 mp3 / wav / flac / m4a / ogg 等常见格式。'],
          ['选择模式', '见下表。只要单旋律选「只扒人声旋律」，想要伴奏选「只扒伴奏」。'],
          ['点开始扒谱', '完成后可直接在文件管理器里定位，或用 MuseScore 打开。'],
        ].map(([t, d], i) => (
          <div key={t} className="flex items-start gap-3">
            <span
              className="mt-0.5 grid h-[20px] w-[20px] shrink-0 place-items-center rounded-full text-[11px] font-semibold"
              style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
            >
              {i + 1}
            </span>
            <div>
              <div className="text-[12.5px] font-medium text-[var(--text)]">{t}</div>
              <div className="text-[11.5px] leading-[1.7] text-[var(--text-dim)]">{d}</div>
            </div>
          </div>
        ))}
      </div>

      <div>
        <H>
          <Layers size={12} strokeWidth={2.2} className="mr-1.5 -mt-px inline" />
          八种模式
        </H>
        <Rows
          items={[
            ['full_auto', '全自动：分离人声与伴奏，各扒一轨，导出双轨 MIDI'],
            ['accompaniment', '只扒伴奏（需分离）'],
            ['vocals', '只扒人声旋律（需分离）——只要一条主旋律时选它'],
            ['basic', '整段直扒：不分离，直接识别整段音频'],
            ['basic_vocals', '单轨直扒·人声旋律：不分离，出 1 轨。已分好的人声走这条'],
            ['basic_accompaniment', '单轨直扒·伴奏多音高：不分离，出 1 轨'],
            ['basic_multi', '多轨直扒：不分离，每个文件出 1 轨，适合已分好轨的素材'],
            ['pre_separated', '已分离音频直入：你自己分好了人声和伴奏，跳过分离步骤'],
          ]}
        />
      </div>

      <P>
        模式名前面的英文就是命令行参数，两边是同一套。
        扒出来的每个音都可以在打谱软件里改——自动识别是起点，不是终点。
      </P>
    </div>
  )
}

/* ══════════════════════════════════════════════════════════
 * 第 3 页 · 命令行
 * ══════════════════════════════════════════════════════════ */
function StepCli({ info }: { info: IntegrationInfo | null }) {
  return (
    <div className="flex flex-col gap-5">
      <div>
        <h2 className="text-[19px] font-semibold tracking-[-0.02em] text-[var(--text)]">
          命令行
        </h2>
        <P>
          同一条命令可以处理整个文件夹，适合批量和脚本。下面是
          <span className="text-[var(--text)]">你这台机器上</span>
          可直接粘贴使用的完整命令——路径已按你的引擎安装位置生成。
        </P>
      </div>

      {info?.ready ? (
        <>
          <div>
            <H>先看环境（有没有装齐、显卡能不能用）</H>
            <CodeBlock code={info.cliCaps} />
          </div>

          <div>
            <H>扒一首歌</H>
            <CodeBlock code={info.cliExample} />
            <div className="mt-1.5">
              <P>
                把引号里的音频路径换成你自己的即可。小技巧：从文件夹把文件拖进终端窗口，
                路径会自动填好。
              </P>
            </div>
          </div>

          <div>
            <H>常用参数</H>
            <Rows
              items={[
                ['-m', '模式，默认 basic。需要分离的用 vocals / accompaniment / full_auto'],
                ['-o', '输出 .mid 路径。不写就放在音频同目录、同名'],
                ['--json', '输出结构化结果，给脚本消费'],
                ['-q', '不打印进度条——写进脚本日志时更干净'],
                ['--extra', '多音轨 / 已分离直入时追加音轨文件，可重复'],
                ['--piano', '钢琴优化：更高时间分辨率 + 中值滤波'],
                ['--simplify', '音符精简强度，0 关闭，2 / 3 / 5 递增'],
                ['--device', 'auto / cuda / cpu，强制指定计算设备'],
              ]}
            />
            <div className="mt-1.5">
              <P>
                以上是最常用的几个。想看全部参数，在终端执行{' '}
                <code
                  className="text-[11.5px] text-[var(--accent)]"
                  style={{
                    fontFamily:
                      'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                  }}
                >
                  bapu --help
                </code>{' '}
                即可。
              </P>
            </div>
          </div>

          <div>
            <H>退出码（写脚本时按它判断，不用去解析输出文字）</H>
            <Rows
              items={[
                ['0', '成功'],
                ['1', '业务失败：文件损坏、扒不出音符等'],
                ['2', '参数错误：路径不存在、模式名写错'],
                ['3', '环境未就绪：依赖缺失、ffmpeg 缺失'],
              ]}
            />
          </div>
        </>
      ) : (
        <NotReady reason={info?.reason} />
      )}
    </div>
  )
}

/* ══════════════════════════════════════════════════════════
 * 第 4 页 · 给 AI 用（MCP）
 * ══════════════════════════════════════════════════════════ */
function StepMcp({
  info,
  onSave,
  onReveal,
}: {
  info: IntegrationInfo | null
  onSave: () => void
  onReveal: (p: string) => Promise<void>
}) {
  return (
    <div className="flex flex-col gap-5">
      <div>
        <h2 className="text-[19px] font-semibold tracking-[-0.02em] text-[var(--text)]">
          让 AI 助手直接调用
        </h2>
        <P>
          接入 MCP 后，你的 AI 助手（WorkBuddy、Claude 等）就能自己扒谱——
          你说「把这首歌的人声扒出来」，它就会调引擎跑完并把结果告诉你。
          引擎和显卡都还在你本机跑，AI 只负责发号施令。
        </P>
      </div>

      {!info?.ready ? (
        <NotReady reason={info?.reason} />
      ) : (
        <>
          {!info.mcpReady && (
            <div
              className="rounded-[var(--r-sm)] border px-3.5 py-3"
              style={{
                background: 'color-mix(in srgb, var(--warning) 8%, transparent)',
                borderColor: 'color-mix(in srgb, var(--warning) 30%, transparent)',
              }}
            >
              <P>
                当前安装的引擎档位
                <span className="text-[var(--text)]">不包含 MCP 支持</span>
                （基础档就不含）。配置照下面填好后仍会启动失败。请到「设置 → 功能档位」加装
                「完整 + MCP」档，再回来复制配置。
              </P>
            </div>
          )}

          <div>
            <H>AI 能用的四个能力</H>
            <Rows
              items={[
                ['transcribe', '核心扒谱，支持全部八种模式'],
                ['list_modes', '列出模式与说明，AI 自己判断该用哪个'],
                ['inspect_audio', '先探一下文件有没有损坏、多长，再决定要不要扒'],
                ['environment_status', '报告 Demucs / GPU / ffmpeg 是否就绪'],
              ]}
            />
          </div>

          <div>
            <H>把这行配置加进你的 MCP 客户端</H>
            <CodeBlock code={info.mcpConfig} />
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={onSave}
                className="inline-flex h-[28px] items-center gap-1.5 rounded-[var(--r-sm)] border px-2.5 text-[11.5px] font-medium text-[var(--text-dim)] transition-[background-color,border-color,transform] duration-150 active:scale-[0.97]"
                style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}
              >
                <Save size={12} strokeWidth={2} />
                保存为 bapu-mcp.json
              </button>
              <button
                type="button"
                onClick={() => void onReveal(info.engineDir)}
                className="inline-flex h-[28px] items-center gap-1.5 rounded-[var(--r-sm)] border px-2.5 text-[11.5px] font-medium text-[var(--text-dim)] transition-[background-color,border-color,transform] duration-150 active:scale-[0.97]"
                style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}
              >
                <FolderOpen size={12} strokeWidth={2} />
                打开引擎目录
              </button>
            </div>
          </div>

          <P>
            路径里已按你这台机器的实际安装位置生成——直接复制就能用，
            不要照抄网上或文档里的示例（那些是别人机器上的路径）。
            配置改完需要重启对应的 AI 客户端才会生效。
          </P>
        </>
      )}
    </div>
  )
}

/* ── 引擎未就绪时的统一说明 ── */
function NotReady({ reason }: { reason?: string }) {
  return (
    <div
      className="flex items-start gap-2.5 rounded-[var(--r-md)] border px-3.5 py-3"
      style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}
    >
      <ArrowRight size={14} strokeWidth={2.2} className="mt-px shrink-0 text-[var(--accent)]" />
      <P>
        引擎还没装好，这部分的命令暂时跑不起来。
        先回到引导页把引擎装上，装完这里的内容会自动出现。
        {reason ? <span className="block mt-1 text-[var(--text-faint)]">{reason}</span> : null}
      </P>
    </div>
  )
}
