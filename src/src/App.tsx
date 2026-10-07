/**
 * App.tsx — 应用外壳
 *
 * 结构（自上而下）：
 *   chrome 区（可拖动）→ 标签导航 → 内容区
 * chrome 与内容区**无可见交界线**，只靠背景色差（--bg-elev vs --bg）区分。
 *
 * 主题：只改 :root CSS 变量，不重挂组件树 → 切主题时任务状态、已选文件全不丢。
 */

import { getCurrentWindow } from '@tauri-apps/api/window'
import { BookOpen, Music4, Settings as SettingsIcon, Waves } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import { BasicTranscribe } from './pages/BasicTranscribe'
import { SongTranscribe } from './pages/SongTranscribe'
import { SettingsPage } from './pages/Settings'
import { BrandMark } from './components/BrandMark'
import { EngineSetup } from './components/EngineSetup'
import { Onboarding } from './components/Onboarding'
import { ThemeSwitcher } from './components/ThemeSwitcher'
import { WindowControls } from './components/WindowControls'
import { checkEngine, type EngineStatus } from './lib/ipc'
import { DOCS_URL, openExternal } from './lib/links'
import { hasSeenOnboarding } from './lib/onboarding-store'
import { applyTheme, loadTheme, persistTheme, type ThemeId } from './theme/themes'

type Tab = 'song' | 'basic'

/**
 * 顶层视图。
 *
 * `work` 是两个功能页；`settings` 是设置页。
 * 设置页刻意**不做成浮层**：它要容纳「引擎位置迁移」这种带进度、
 * 带风险提示的长流程，浮层里放不下也容易误关。
 * 用整页替换内容区，header 保持不动，齿轮按钮高亮表示当前所在。
 */
type View = 'work' | 'settings'

const TABS: { id: Tab; label: string; sub: string; icon: typeof Music4 }[] = [
  { id: 'song', label: '歌曲扒谱', sub: '分离人声与伴奏', icon: Music4 },
  // 功能页 2：从手头已有的音频直接扒，不调动分离。
  // 名字与页 1「歌曲扒谱」对仗：「直扒」= 不分离、直接识别。
  { id: 'basic', label: '音频直扒', sub: '单轨 / 多轨 / 直入', icon: Waves },
]

const inTauri = (): boolean =>
  typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

export default function App() {
  const [tab, setTab] = useState<Tab>('song')
  const [view, setView] = useState<View>('work')
  const [theme, setTheme] = useState<ThemeId>(() => loadTheme())

  /**
   * 首启引导：引擎未就绪时先走安装向导，装完再进主界面。
   *
   * 引擎 venv 实测 5.1GB（torch 4.4GB），不随包分发，故必须有这一步。
   * 开发态自带 venv 时 checkEngine 立刻返回 ready，不打扰。
   */
  const [engineReady, setEngineReady] = useState<boolean | null>(null)
  /** 完整状态。用于判断「引擎能用但缺某个能力」（如已装基础档、缺人声分离）。 */
  const [engineStatus, setEngineStatus] = useState<EngineStatus | null>(null)
  /** 进入引导页的意图：首次运行 or 主动加装功能 */
  const [setupIntent, setSetupIntent] = useState<'first-run' | 'upgrade'>('first-run')
  /**
   * 引擎纪元。每次引擎状态被重新确认就 +1，用作内容区的 `key`。
   *
   * ## 为什么需要它
   * 各功能页在挂载时各自拉一次 `get_env_info`（Demucs / CUDA / 设备），
   * 装完引擎后这些数据全部过期，但**页面并不知道要重拉** ——
   * 用户看到的是「装完了，界面还在说没装 Demucs」。
   * 主上实测截图反馈过这个现象。
   *
   * 与其让每个页面自己订阅「安装完成」事件（易漏），不如在这里加一个
   * 显式的重挂载信号：纪元一变，整棵内容区重建，所有页面重新拉数据。
   * 触发时机只有两个（启动探测完成、安装完成），此刻用户尚无进行中的作业，
   * 清空选中文件是可接受的代价。
   */
  const [engineEpoch, setEngineEpoch] = useState(0)

  /**
   * 新手指引弹窗。
   *
   * 触发条件有两个，缺一不可：
   *   ① 引擎已就绪 —— 未就绪时用户面前是安装向导，此时弹指引只会添乱；
   *   ② 本机从未看过 —— 用 localStorage 记录，看一次就不再打扰。
   *
   * 之后可在「设置 → 新手指引」随时重看（`openGuide`）。
   */
  const [guideOpen, setGuideOpen] = useState(false)

  /**
   * 拉取引擎状态。
   *
   * @param fallbackToSetup 探测到「未就绪」时是否退回安装向导。
   *   - `true`（启动期）：要退。引擎不可用时必须给用户自救入口，
   *     绝不能把他放进主界面看着每个功能报错。
   *   - `false`（安装刚完成）：不退。此时用户刚从安装向导确认成功出来，
   *     而冷启动 `import torch`（4.4GB，还要过杀软）可能要几十秒，
   *     探针超时**不等于**引擎坏了。若据此把人弹回向导，就复现了
   *     「装完又被踢回安装页」的死循环 —— 主上踩过这个坑。
   */
  const refreshEngine = useCallback(async (fallbackToSetup: boolean) => {
    try {
      const s = await checkEngine()
      setEngineStatus(s)
      if (fallbackToSetup) setEngineReady(s.ready)
      /**
       * 引擎就绪 = 用户第一次真正「能用」的时刻，此时给一次新手指引。
       *
       * 为什么放在这里而不是 `useEffect(..., [engineReady])`：
       * 这是「探测完成」这个事件的自然延续，直接更新状态即可；
       * 若挪到渲染后的 effect 里再判断一次，既多一轮级联渲染，
       * 时机也更晚（要等这一帧画完）。静态检查对
       * `set-state-in-effect` 的提示正是在说这件事。
       */
      if (s.ready && !hasSeenOnboarding()) setGuideOpen(true)
    } catch {
      // ⚠️ 探测失败**不能**等同于「已就绪」。
      // 曾把 catch 写成 setEngineReady(true)，理由是「让用户能进主界面看到报错」，
      // 结果把「引擎不可用」与「引擎就绪」混为一谈：用户被直接放进主界面，
      // 每个功能都报「引擎未返回结果」，却拿不到任何自救入口。
      // 正解：交给安装向导——它会显示具体错误并给出「重新检测 / 重新安装」。
      if (fallbackToSetup) setEngineReady(false)
    } finally {
      // 无论成败，状态已重新确定 → 让内容区重挂载、重拉 env
      setEngineEpoch((e) => e + 1)
    }
  }, [])

  useEffect(() => {
    // 静态检查在此为保守误报：refreshEngine() 内所有 setState 都在
    // await checkEngine() 之后，属异步更新，不会造成同步级联渲染。
    // 与 EngineSetup.probe 同一处理方式。
    // oxlint-disable-next-line react/set-state-in-effect
    void refreshEngine(true)
  }, [refreshEngine])

  /* 主题变量写入 :root（首帧与切换时都走这里） */
  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  const onTheme = useCallback((id: ThemeId) => {
    setTheme(id)
    persistTheme(id)
  }, [])

  /**
   * 安装向导完成回调。
   *
   * ⚠️ 必须用 `useCallback` 固定引用，不能写成内联箭头函数。
   * 内联写法每次 App 渲染都会产生新函数 → `EngineSetup` 里
   * `probe` 的 useCallback 依赖失效 → 探测 useEffect 重跑 →
   * `intent='upgrade'` 时无条件 `setPhase('choosing')`，
   * 会把正在安装的进度界面直接打断（回到选择页）。
   * 触发条件很隐蔽：安装期间只要有任意一次 App 重渲染
   * （例如用户切主题）就会命中。
   */
  const handleSetupReady = useCallback(() => {
    setSetupIntent('first-run')
    /**
     * ⚠️ 安装完成 = 引擎状态**已被改写**，必须先让旧状态失效。
     *
     * 主上实测反馈：「装完了为什么还有这两行」——
     * 主界面顶部仍挂着「当前缺少人声与伴奏分离…加装」，
     * 功能页也还写着「未安装 Demucs / 未检测到 CUDA」。
     * 根因就是这里只翻 `engineReady`，`engineStatus` 仍停在
     * **首启那一刻**（那时确实什么都没装）。
     *
     * 置为 null 而非保留旧值：null 表示「未知」，
     * 横幅与能力提示的条件判断自然不成立，
     * 于是不会在刚装完时闪一条假警报。
     */
    setEngineStatus(null)
    setEngineReady(true)
    // 重新确认真实状态；完成后 engineEpoch 自增 → 内容区重挂载 → 重拉 env
    void refreshEngine(false)
  }, [refreshEngine])

  /**
   * 打开引导页补装功能（从设置页调用）。
   *
   * 主上要求「加装后的页面改成开局引导页那种」——
   * 不再用主界面上一条横幅直跳，而是走与首次运行**完全相同**的向导页：
   * 同样的档位说明、磁盘需求、下载源选择、实时进度与日志。
   * 只有同一套流程，用户才不必学习第二种安装界面。
   *
   * `intent='upgrade'` 让向导在引擎已就绪时**停在选择页**，
   * 否则会被 probe 直接弹回主界面，来不及选档位。
   */
  const openUpgrade = useCallback(() => {
    setSetupIntent('upgrade')
    setEngineReady(false)
  }, [])

  /**
   * 重看新手指引（设置页入口）。
   *
   * 注意这里**不重置**「已看过」标记：用户只是重看一遍，
   * 不代表下次启动要再弹。标记只在他真正看完并关闭时由指引自己写。
   */
  const openGuide = useCallback(() => setGuideOpen(true), [])
  const closeGuide = useCallback(() => setGuideOpen(false), [])

  /**
   * 「唤起系统浏览器」失败的落点。
   *
   * 应用是无边框窗口，`window.open` 弹出的新窗口没有系统标题栏、用户关不掉，
   * 所以外链一律交给系统默认浏览器（见 `lib/links.ts`）。
   * 但 opener 插件也有被系统拒绝的可能（无默认浏览器 / 被安全软件拦截）。
   *
   * ⚠️ 不许静默失败 —— 界面上不允许「无出路的终态」：
   * 失败时把链接原文摊在顶栏下方，用户可选中复制，亦可点「重试」。
   */
  const [linkFail, setLinkFail] = useState<string | null>(null)

  /** 打开外链；失败则落到提示条上（出路：重试 / 手动复制链接） */
  const openEx = useCallback((url: string) => {
    void openExternal(url)
      .then(() => setLinkFail(null))
      .catch(() => setLinkFail(url))
  }, [])

  /**
   * 引擎未就绪 → 走首启安装向导；装完 onReady 切回主界面。
   * engineReady 为 null 表示还在探测中，此时不渲染任何分支，避免闪烁。
   */
  /* chrome 空白处可拖动窗口；交互元素上不触发 */
  const startDrag = useCallback((e: React.MouseEvent) => {
    const t = e.target as HTMLElement
    if (t.closest('button, a, input, select, textarea, [role="button"], [data-no-drag]')) return
    if (!inTauri()) return
    getCurrentWindow()
      .startDragging()
      .catch(() => {})
  }, [])

  /**
   * ⚠️ 这个提前 return 必须在**所有 hooks 之后**。
   *
   * 踩坑实录（主上实测「点了加装直接变黑，没进度条没提示没界面」）：
   * 初版把这段插在了 `onTheme` 与 `startDrag` 两个 useCallback **之间**，
   * 于是 engineReady===false 时 `startDrag` 不会被调用 ——
   * hook 数量比正常渲染少 1 个，React 抛 error #300
   * （"Rendered fewer hooks than expected"），整棵树被卸载。
   * 加上窗口是 transparent，用户看到的就是**一片纯黑**。
   *
   * 教训：React 的 hooks 必须**无条件、按固定顺序**执行；
   * 任何 `if (...) return` 都只能放在全部 hooks 之后。
   */
  if (engineReady === false) {
    return <EngineSetup intent={setupIntent} onReady={handleSetupReady} />
  }

  return (
    <div className="flex h-full flex-col bg-bg">
      {/* ================= chrome ================= */}
      <header
        onMouseDown={startDrag}
        data-tauri-drag-region
        className="band relative flex h-[46px] shrink-0 select-none items-center gap-3 pl-4"
        style={{ paddingRight: 12 }}
      >
        <div className="flex items-center gap-2">
          <BrandMark size={22} className="shrink-0" />
          <span className="text-[13px] font-semibold tracking-[-0.01em] text-ink">扒谱台</span>
          <span className="num hidden text-[10px] text-ink-faint sm:inline">AutoTranscriber</span>
        </div>

        <span className="h-4 w-px bg-line" />

        {/* 标签导航 */}
        <nav className="flex items-center gap-0.5" aria-label="功能页">
          {TABS.map((t) => {
            const active = tab === t.id
            const Icon = t.icon
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => {
                  setTab(t.id)
                  // 从设置页点功能页标签 = 明确的「回去干活」意图
                  setView('work')
                }}
                aria-current={active ? 'page' : undefined}
                title={t.sub}
                className={clsx(
                  'relative flex h-[28px] items-center gap-1.5 rounded-[var(--r-sm)] px-2.5',
                  'text-[12px] font-medium',
                  'transition-[background-color,color,transform] duration-150 ease-out',
                  'active:scale-[0.97]',
                  active
                    ? 'bg-accent-soft text-accent'
                    : clsx(
                        'text-ink-faint',
                        '[[@media(hover:hover)_and_(pointer:fine)]]:hover:bg-accent-soft',
                        '[[@media(hover:hover)_and_(pointer:fine)]]:hover:text-ink-dim',
                      ),
                )}
              >
                <Icon size={13} strokeWidth={active ? 2 : 1.8} />
                {t.label}
              </button>
            )
          })}
        </nav>

        <div className="flex-1" />

        {/* 主题切换 + 设置 + 窗口三键 */}
        <div data-no-drag className="flex items-center gap-2">
          <ThemeSwitcher value={theme} onChange={onTheme} />
          {/* 说明文档直达（顺带可切到官网首页）。
              必须交给系统默认浏览器：本窗口是无边框的，window.open 开出来的
              新窗口没有系统标题栏，用户关不掉 —— 详见 lib/links.ts */}
          <button
            type="button"
            onClick={() => openEx(DOCS_URL)}
            aria-label="使用说明与官网"
            title="使用说明与官网"
            className={clsx(
              'flex h-[34px] w-[34px] items-center justify-center rounded-[var(--r-sm)]',
              'text-ink-faint',
              'transition-[background-color,color,transform] duration-150 ease-out active:scale-[0.94]',
              '[[@media(hover:hover)_and_(pointer:fine)]]:hover:bg-accent-soft',
              '[[@media(hover:hover)_and_(pointer:fine)]]:hover:text-ink',
            )}
          >
            <BookOpen size={16} strokeWidth={1.9} />
          </button>
          <button
            type="button"
            onClick={() => setView((v) => (v === 'settings' ? 'work' : 'settings'))}
            aria-label="设置"
            aria-pressed={view === 'settings'}
            title="设置"
            className={clsx(
              'flex h-[34px] w-[34px] items-center justify-center rounded-[var(--r-sm)]',
              'transition-[background-color,color,transform] duration-150 ease-out active:scale-[0.94]',
              '[[@media(hover:hover)_and_(pointer:fine)]]:hover:bg-accent-soft',
              '[[@media(hover:hover)_and_(pointer:fine)]]:hover:text-ink',
              view === 'settings' ? 'bg-accent-soft text-accent' : 'text-ink-faint',
            )}
          >
            <SettingsIcon size={16} strokeWidth={1.9} />
          </button>
          <span className="h-4 w-px bg-line" />
          <WindowControls />
        </div>
      </header>

      {/*
        外链打开失败时的出路。
        默认浏览器缺失 / 被安全软件拦截时，opener 会抛错 —— 此时不能静默：
        把链接原文摊开（`select-all` 便于一键复制），并给「重试 / 关闭」。
      */}
      {linkFail && (
        <div className="flex shrink-0 items-center gap-2 border-b border-line bg-bg-elev px-4 py-1.5 text-[11.5px]">
          <span className="shrink-0 text-ink-dim">没能唤起浏览器，请手动打开：</span>
          <code className="min-w-0 flex-1 select-all truncate rounded-[var(--r-sm)] bg-bg px-1.5 py-0.5 text-[11px] text-ink">
            {linkFail}
          </code>
          <button
            type="button"
            onClick={() => openEx(linkFail)}
            className="shrink-0 rounded-[var(--r-sm)] px-2 py-0.5 font-medium text-accent transition-colors duration-150 ease-out [[@media(hover:hover)_and_(pointer:fine)]]:hover:bg-accent-soft"
          >
            重试
          </button>
          <button
            type="button"
            onClick={() => setLinkFail(null)}
            aria-label="关闭提示"
            title="关闭"
            className="shrink-0 rounded-[var(--r-sm)] px-2 py-0.5 text-ink-faint transition-colors duration-150 ease-out [[@media(hover:hover)_and_(pointer:fine)]]:hover:bg-bg"
          >
            关闭
          </button>
        </div>
      )}

      {/* ================= 内容区 ================= */}
      {/*
        两块内容（功能页 / 设置页）都**常驻挂载**，用 hidden 切换：
        切页不丢状态，也不重复挂引擎订阅。

        ⚠️ `key={engineEpoch}` 只套在功能页这一层，**不能**套在 main 上。
        功能页重挂载的目的只有一个：逼它们重拉 env（否则装完引擎仍显示
        安装前的 Demucs / CUDA 探测结果）。但设置页恰恰是**发起改写的一方**——
        迁移一完成就把自己重建，「迁移完成 / 新位置」会当场消失，
        用户只看到一个（因为 status 还在异步路上）路径未变的旧界面。
        故：epoch 只管功能页，设置页常驻。 */}
      <main className="relative min-h-0 flex-1 bg-bg">
        <div
          key={engineEpoch}
          className={clsx('absolute inset-0 flex flex-col', view !== 'work' && 'hidden')}
        >
          {/* 两个页面都常驻挂载，用 hidden 切换：切页不丢状态 */}
          <div className={clsx('absolute inset-0 flex flex-col', tab !== 'song' && 'hidden')}>
            <SongTranscribe />
          </div>
          <div className={clsx('absolute inset-0 flex flex-col', tab !== 'basic' && 'hidden')}>
            <BasicTranscribe />
          </div>
        </div>

        <div
          className={clsx('absolute inset-0 flex flex-col', view !== 'settings' && 'hidden')}
        >
          <SettingsPage
            status={engineStatus}
            onUpgrade={openUpgrade}
            onRefresh={(): void => void refreshEngine(false)}
            onShowGuide={openGuide}
          />
        </div>
      </main>

      {/* 新手指引。放在最外层：它要覆盖 chrome 与内容区。
          条件渲染而非传 open：挂载即显示，每次打开都从第一页开始。

          引擎未就绪时 App 早已提前 return 到安装向导，故不会与之叠加——
          这也正是想要的行为：用户还没装上引擎时，「怎么用命令行」讲了也白讲。 */}
      {guideOpen && <Onboarding onClose={closeGuide} />}
    </div>
  )
}
