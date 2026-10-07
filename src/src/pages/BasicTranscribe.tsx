/**
 * BasicTranscribe.tsx — 功能页 2「音频直扒」
 *
 * 需求：把**手头已有的音频**直接扒成 MIDI，不调动 Demucs。
 * 三条输入形态——
 *   单轨（人声旋律 / 伴奏多音高）：拖 1 个音频，出 1 轨；
 *   多轨：一次拖多个文件，逐轨各出 1 轨；
 *   已分离直入：自己已分好人声 + 伴奏，放两个槽位，跳过分离。
 *
 * 两槽模式（pre_separated）用 armed 机制解决 webview 拖放事件全局的问题：
 * 主槽恒armed，副槽点击后再armed；两个槽都可点击选择文件。
 */

import { Layers, Play, Save, Split } from 'lucide-react'
import { useEffect, useState } from 'react'
import clsx from 'clsx'
import { DropZone } from '../components/DropZone'
import { EnvBanner } from '../components/EnvBanner'
import { LoadErrorBanner } from '../components/LoadErrorBanner'
import { ModeNote, ModeSelect } from '../components/ModeSelect'
import { ParamPanel } from '../components/ParamPanel'
import { ProgressPanel } from '../components/ProgressPanel'
import { ResultCard } from '../components/ResultCard'
import { Section } from '../components/Section'
import type { ModeInfo } from '../lib/types'
import { separationLabel, useWorkspace } from '../lib/useWorkspace'

export function BasicTranscribe() {
  // 默认落在「单轨直扒（人声旋律）」——主上的主场景是把分好的人声出成单轨小提琴谱。
  const ws = useWorkspace({
    page: 2,
    defaultMode: 'basic_vocals',
    twoSlotModes: ['pre_separated'],
  })
  const { task, activeModeInfo } = ws

  /** 双槽模式下，当前接收点击/拖放的槽位：0=主 1=副 */
  const [armedSlot, setArmedSlot] = useState<0 | 1>(0)

  useEffect(() => {
    void ws.load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /**
   * 切模式时重置 armed，并把文件数裁到新模式的容量。
   * 不裁的话「先选多轨直扒丢 3 个文件、再切单轨模式」会留下 3 个文件，
   * 而引擎只读第一个 —— 用户看到 3 个文件却只出 1 轨，且毫无提示。
   */
  useEffect(() => {
    setArmedSlot(0)
    ws.trimFilesToCapacity()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ws.mode])

  const running = task.isRunning
  const twoSlot = ws.needsTwoSlots
  const vocal = ws.files[0] ?? null
  const accomp = ws.files[1] ?? null

  const setVocalFiles = (next: typeof ws.files) => {
    // 主槽变化时保持副槽（用户可能先填了伴奏）
    ws.setFiles(next.length && accomp ? [next[0], accomp] : next)
    if (!next.length && accomp) ws.setFiles([])
  }
  const setAccompFiles = (next: typeof ws.files) => {
    ws.setFiles(vocal ? [vocal, ...next] : next)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <EnvBanner env={ws.env} needsSeparation={activeModeInfo?.separates ?? false} />

      <LoadErrorBanner
        message={ws.loadError}
        onRetry={() => void ws.load()}
        what="模式列表与环境信息"
      />

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto grid w-full max-w-[1180px] grid-cols-1 gap-x-8 gap-y-7 px-8 py-7 lg:grid-cols-[minmax(0,1fr)_336px]">
          {/* ================= 左列 ================= */}
          <div className="flex min-w-0 flex-col gap-7">
            <Section
              index="01"
              title={twoSlot ? '已分离的音轨' : '源音频'}
              aside={
                twoSlot ? (
                  <span className="text-[10.5px] text-ink-faint">人声 + 伴奏，跳过分离</span>
                ) : ws.isBasicMulti ? (
                  <span className="text-[10.5px] text-ink-faint">可放多个文件，每个一轨</span>
                ) : (
                  <span className="text-[10.5px] text-ink-faint">放 1 个音频</span>
                )
              }
            >
              {twoSlot ? (
                /* ---- 双槽：人声 / 伴奏 ---- */
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div
                    onClick={() => setArmedSlot(0)}
                    className={clsx(
                      'rounded-[var(--r-md)] transition-shadow duration-150 ease-out',
                      armedSlot === 0 && 'ring-1 ring-accent',
                    )}
                  >
                    <SlotLabel
                      icon={<Split size={11} strokeWidth={2} />}
                      text="人声"
                      active={armedSlot === 0}
                      filled={Boolean(vocal)}
                    />
                    <DropZone
                      files={vocal ? [vocal] : []}
                      onFiles={setVocalFiles}
                      disabled={running}
                      armed={armedSlot === 0}
                      slotLabel="人声"
                      compact
                    />
                  </div>
                  <div
                    onClick={() => setArmedSlot(1)}
                    className={clsx(
                      'rounded-[var(--r-md)] transition-shadow duration-150 ease-out',
                      armedSlot === 1 && 'ring-1 ring-accent',
                    )}
                  >
                    <SlotLabel
                      icon={<Layers size={11} strokeWidth={2} />}
                      text="伴奏"
                      active={armedSlot === 1}
                      filled={Boolean(accomp)}
                    />
                    <DropZone
                      files={accomp ? [accomp] : []}
                      onFiles={setAccompFiles}
                      disabled={running}
                      armed={armedSlot === 1}
                      slotLabel="伴奏"
                      compact
                    />
                  </div>
                </div>
              ) : (
                /* ---- 单槽 ---- */
                <DropZone
                  files={ws.files}
                  onFiles={ws.setFiles}
                  disabled={running}
                  multiple={ws.isBasicMulti}
                  max={ws.isBasicMulti ? 6 : 1}
                />
              )}
            </Section>

            <Section index="02" title="扒谱模式">
              <div className="flex flex-col gap-2">
                <ModeSelect
                  modes={ws.modes}
                  value={ws.mode}
                  onChange={ws.setMode}
                  disabled={running}
                />
                <ModeNote mode={ws.activeModeInfo} />
              </div>
            </Section>

            <Section index="03" title="扒谱参数">
              <div className="panel px-4 py-4">
                <ParamPanel
                  params={ws.params}
                  onChange={ws.setParams}
                  mode={ws.mode}
                  disabled={running}
                />
              </div>
            </Section>
          </div>

          {/* ================= 右列 ================= */}
          <div className="flex min-w-0 flex-col gap-7">
            <div className="lg:sticky lg:top-7">
              <Section index="04" title="作业状态">
                <div className="panel px-4 py-4">
                  {task.status === 'done' && task.result ? (
                    <ResultCard
                      result={task.result}
                      separationLabel={separationLabel(task.result.separationMethod)}
                      onReset={ws.resetAll}
                    />
                  ) : running || task.status === 'error' ? (
                    <ProgressPanel
                      task={task}
                      onCancel={ws.cancel}
                      stageLabels={ws.stageLabels}
                      inputPath={ws.files[0]?.path ?? null}
                      modeLabel={ws.activeModeInfo?.label ?? null}
                    />
                  ) : (
                    <IdlePanel
                      mode={ws.activeModeInfo}
                      needsTwo={twoSlot}
                      hasVocal={Boolean(vocal)}
                      hasAccomp={Boolean(accomp)}
                    />
                  )}
                </div>
              </Section>

              <div className="mt-3 flex items-center gap-2 rounded-[var(--r-sm)] border border-line px-3 py-2">
                <Save size={12} strokeWidth={1.9} className="shrink-0 text-ink-faint" />
                <span className="num min-w-0 flex-1 truncate text-[11px] text-ink-faint">
                  {ws.outputHint ?? '输出到源文件同目录'}
                </span>
                <button
                  type="button"
                  onClick={ws.chooseOutput}
                  disabled={running || ws.files.length === 0}
                  className="btn btn-ghost h-[24px] shrink-0 px-2 text-[11px]"
                >
                  另存为
                </button>
              </div>

              <button
                type="button"
                onClick={ws.submit}
                disabled={!ws.canSubmit}
                className="btn btn-primary btn-lg mt-3 w-full"
              >
                <Play size={14} strokeWidth={2} />
                {running ? '扒谱中…' : '开始扒谱'}
              </button>
              {ws.missingHint && (
                <p className="mt-2 text-center text-[11px] text-warn">{ws.missingHint}</p>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

/** 槽位标签：让用户知道拖到这里是哪个轨 */
function SlotLabel({
  icon,
  text,
  active,
  filled,
}: {
  icon: React.ReactNode
  text: string
  active: boolean
  filled: boolean
}) {
  return (
    <span className="mb-1.5 flex items-center gap-1.5">
      <span className={clsx('transition-colors duration-150 ease-out', active ? 'text-accent' : 'text-ink-faint')}>
        {icon}
      </span>
      <span className="text-[11.5px] font-medium text-ink-dim">{text}</span>
      <span
        className={clsx(
          'ml-auto text-[9.5px] font-medium',
          filled ? 'text-ok' : 'text-ink-faint',
        )}
      >
        {filled ? '已就位' : '待放入'}
      </span>
    </span>
  )
}

function IdlePanel({
  mode,
  needsTwo,
  hasVocal,
  hasAccomp,
}: {
  mode: ModeInfo | null
  needsTwo: boolean
  hasVocal: boolean
  hasAccomp: boolean
}) {
  return (
    <div className="flex animate-rise-in flex-col items-start gap-2.5 py-1">
      <span className="flex h-9 w-9 items-center justify-center rounded-[var(--r-sm)] bg-accent-soft text-accent">
        <Layers size={17} strokeWidth={1.7} />
      </span>
      <div>
        <p className="text-[13px] font-medium text-ink">待命</p>
        <p className="mt-1 text-[11.5px] leading-relaxed text-ink-faint">
          {mode ? (
            <>
              当前模式「<span className="text-ink-dim">{mode.label}</span>」
            </>
          ) : (
            '尚未选择模式'
          )}
          {needsTwo ? (
            <>
              ，需要人声
              <span className={hasVocal ? 'text-ok' : 'text-warn'}>{hasVocal ? '✓' : '（待放入）'}</span>
              与伴奏
              <span className={hasAccomp ? 'text-ok' : 'text-warn'}>{hasAccomp ? '✓' : '（待放入）'}</span>。
            </>
          ) : (
            /* 说明文案由引擎下发（ModeInfo.hint），前端不硬编码模式语义 */
            `，${mode?.hint ?? '放入音频后即可开始扒谱。'}`
          )}
        </p>
      </div>
    </div>
  )
}
