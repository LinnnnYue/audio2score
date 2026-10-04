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
import { AudioLines, Music4, Waves } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import { BasicTranscribe } from './pages/BasicTranscribe'
import { SongTranscribe } from './pages/SongTranscribe'
import { EngineSetup } from './components/EngineSetup'
import { ThemeSwitcher } from './components/ThemeSwitcher'
import { WindowControls } from './components/WindowControls'
import { checkEngine } from './lib/ipc'
import { applyTheme, loadTheme, persistTheme, type ThemeId } from './theme/themes'

type Tab = 'song' | 'basic'

const TABS: { id: Tab; label: string; sub: string; icon: typeof Music4 }[] = [
  { id: 'song', label: '歌曲扒谱', sub: '分离人声与伴奏', icon: Music4 },
  { id: 'basic', label: '基本扒谱', sub: '单轨 / 多轨 / 直入', icon: Waves },
]

const inTauri = (): boolean =>
  typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

export default function App() {
  const [tab, setTab] = useState<Tab>('song')
  const [theme, setTheme] = useState<ThemeId>(() => loadTheme())

  /**
   * 首启引导：引擎未就绪时先走安装向导，装完再进主界面。
   *
   * 引擎 venv 实测 5.1GB（torch 4.4GB），不随包分发，故必须有这一步。
   * 开发态自带 venv 时 checkEngine 立刻返回 ready，不打扰。
   */
  const [engineReady, setEngineReady] = useState<boolean | null>(null)

  useEffect(() => {
    let alive = true
    void checkEngine()
      .then((s) => {
        if (alive) setEngineReady(s.ready)
      })
      .catch(() => {
        // 探测失败时不阻塞主界面——让用户能进 App 再看到具体报错，
        // 总好过白屏卡死在引导页。
        if (alive) setEngineReady(true)
      })
    return () => {
      alive = false
    }
  }, [])

  /* 主题变量写入 :root（首帧与切换时都走这里） */
  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  const onTheme = useCallback((id: ThemeId) => {
    setTheme(id)
    persistTheme(id)
  }, [])

  /**
   * 引擎未就绪 → 走首启安装向导；装完 onReady 切回主界面。
   * engineReady 为 null 表示还在探测中，此时不渲染任何分支，避免闪烁。
   */
  if (engineReady === false) {
    return <EngineSetup onReady={(): void => { setEngineReady(true) }} />
  }

  /* chrome 空白处可拖动窗口；交互元素上不触发 */
  const startDrag = useCallback((e: React.MouseEvent) => {
    const t = e.target as HTMLElement
    if (t.closest('button, a, input, select, textarea, [role="button"], [data-no-drag]')) return
    if (!inTauri()) return
    getCurrentWindow()
      .startDragging()
      .catch(() => {})
  }, [])

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
          <span className="flex h-[22px] w-[22px] items-center justify-center rounded-[6px] bg-accent text-accent-contrast">
            <AudioLines size={13} strokeWidth={2.2} />
          </span>
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
                onClick={() => setTab(t.id)}
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

        {/* 主题切换 + 窗口三键 */}
        <div data-no-drag className="flex items-center gap-2">
          <ThemeSwitcher value={theme} onChange={onTheme} />
          <span className="h-4 w-px bg-line" />
          <WindowControls />
        </div>
      </header>

      {/* ================= 内容区 ================= */}
      {/* 两个页面都常驻挂载，用 hidden 切换：切页不丢状态，也不重复挂引擎订阅 */}
      <main className="relative min-h-0 flex-1 bg-bg">
        <div className={clsx('absolute inset-0 flex flex-col', tab !== 'song' && 'hidden')}>
          <SongTranscribe />
        </div>
        <div className={clsx('absolute inset-0 flex flex-col', tab !== 'basic' && 'hidden')}>
          <BasicTranscribe />
        </div>
      </main>
    </div>
  )
}
