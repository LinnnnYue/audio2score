/**
 * SongTranscribe.tsx — 功能页 1「歌曲扒谱」
 *
 * 需求：拖入一个音频 → 下拉选模式（四项）→ 参数 → 扒谱。
 * 四项由 describe_modes() 中 page===1 的条目给出，前端不硬编码文案。
 */

import { Disc3, Play, Save } from 'lucide-react'
import { useEffect } from 'react'
import clsx from 'clsx'
import { DropZone } from '../components/DropZone'
import { EnvBanner } from '../components/EnvBanner'
import { ModeNote, ModeSelect } from '../components/ModeSelect'
import { ParamPanel } from '../components/ParamPanel'
import { ProgressPanel } from '../components/ProgressPanel'
import { ResultCard } from '../components/ResultCard'
import { Section } from '../components/Section'
import { Tooltip } from '../components/Tooltip'
import { previewOutput, separationLabel, useWorkspace } from '../lib/useWorkspace'

export function SongTranscribe() {
  const ws = useWorkspace({ page: 1, defaultMode: 'full_auto' })
  const { task, activeModeInfo } = ws

  useEffect(() => {
    void ws.load()
    // load 内部已memo，只在挂载时跑一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const running = task.isRunning
  const idle = task.status === 'idle'

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <EnvBanner env={ws.env} needsSeparation={activeModeInfo?.separates ?? false} />

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto grid w-full max-w-[1180px] grid-cols-1 gap-x-8 gap-y-7 px-8 py-7 lg:grid-cols-[minmax(0,1fr)_336px]">
          {/* ================= 左列：输入 → 模式 → 参数 ================= */}
          <div className="flex min-w-0 flex-col gap-7">
            <Section index="01" title="源音频">
              <DropZone
                files={ws.files}
                onFiles={ws.setFiles}
                disabled={running}
                max={1}
              />
            </Section>

            <Section
              index="02"
              title="扒谱模式"
              aside={
                ws.activeModeInfo && (
                  <span className="text-[10.5px] text-ink-faint">
                    共 {ws.modes.length} 项
                  </span>
                )
              }
            >
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

            <Section
              index="03"
              title="扒谱参数"
              aside={
                <Tooltip content="参数影响识别精度与耗时。不确定就用默认值。">
                  <span className="cursor-help text-[10.5px] text-ink-faint">默认即可</span>
                </Tooltip>
              }
            >
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

          {/* ================= 右列：作业状态 ================= */}
          <div className="flex min-w-0 flex-col gap-7">
            <div className="lg:sticky lg:top-7">
              <Section index="04" title="作业状态">
                {/* 面板是真正的工具面板，允许有框 */}
                <div className="panel px-4 py-4">
                  {task.status === 'done' && task.result ? (
                    <ResultCard
                      result={task.result}
                      separationLabel={separationLabel(task.result.separationMethod)}
                      onReset={ws.resetAll}
                    />
                  ) : task.status === 'error' ? (
                    <ProgressPanel task={task} onCancel={ws.cancel} stageLabels={ws.stageLabels} />
                  ) : running ? (
                    <ProgressPanel task={task} onCancel={ws.cancel} stageLabels={ws.stageLabels} />
                  ) : (
                    <IdlePanel
                      modeLabel={ws.activeModeInfo?.label ?? null}
                      trackCount={ws.activeModeInfo?.tracks ?? null}
                    />
                  )}
                </div>
              </Section>

              {/* 输出位置 */}
              <div className="mt-3 flex items-center gap-2 rounded-[var(--r-sm)] border border-line px-3 py-2">
                <Save size={12} strokeWidth={1.9} className="shrink-0 text-ink-faint" />
                <span className="num min-w-0 flex-1 truncate text-[11px] text-ink-faint">
                  {previewOutput(ws.files, ws.outputPath) ?? '输出到源文件同目录'}
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

              {/* 提交 */}
              <button
                type="button"
                onClick={ws.submit}
                disabled={!ws.canSubmit}
                className={clsx('btn btn-primary btn-lg mt-3 w-full', 'disabled:hover:bg-accent')}
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

/** 空态：不是营销页，是"现在会发生什么"的说明 */
function IdlePanel({
  modeLabel,
  trackCount,
}: {
  modeLabel: string | null
  trackCount: number | null
}) {
  return (
    <div className="flex animate-rise-in flex-col items-start gap-2.5 py-1">
      <span className="flex h-9 w-9 items-center justify-center rounded-[var(--r-sm)] bg-accent-soft text-accent">
        <Disc3 size={17} strokeWidth={1.7} />
      </span>
      <div>
        <p className="text-[13px] font-medium text-ink">待命</p>
        <p className="mt-1 text-[11.5px] leading-relaxed text-ink-faint">
          放入音频、选好模式后即可开始。当前模式
          {modeLabel ? (
            <>
              「<span className="text-ink-dim">{modeLabel}</span>」
            </>
          ) : (
            '尚未选择'
          )}
          ，将导出 {trackCount ?? '—'} 条 MIDI 音轨。
        </p>
      </div>
    </div>
  )
}
