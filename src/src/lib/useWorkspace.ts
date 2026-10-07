/**
 * useWorkspace.ts — 两个功能页共享的作业逻辑
 *
 * 抽出共用部分是为了让两页的**状态流转完全一致**（空态 → 处理中 → 完成/失败），
 * 避免两页各写一套导致某个边角状态缺失。
 *
 * 各页差异只在「要几个文件槽」：
 *   功能页1 歌曲扒谱   → 1 个主输入
 *   功能页2 音频直扒   → 1 个主输入（单轨 / 多轨），
 *                        或 2 个（人声 + 伴奏，已分离直入）
 */

import { useCallback, useMemo, useState } from 'react'
import * as ipc from '../lib/ipc'
import { basename, stripExt } from '../lib/format'
import {
  getDefaultOutputDir,
  joinOutputPath,
  useDefaultOutputDir,
} from '../lib/output-store'
import type {
  EnvInfo,
  ModeInfo,
  ModesPayload,
  ProbeResult,
  TranscribeMode,
  TranscribeRequest,
} from '../lib/types'
import { DEFAULT_PARAMS, type Params } from './params'
import { useTranscribeTask } from './useTranscribeTask'
import type { DroppedFile } from '../components/DropZone'

export interface WorkspaceConfig {
  /** 该页包含哪些 mode（来自 describe_modes 的 page 字段） */
  page: 1 | 2
  /** 默认选中的模式 */
  defaultMode: TranscribeMode
  /** 需要两个槽位的模式（pre_separated） */
  twoSlotModes?: TranscribeMode[]
}

export function useWorkspace(config: WorkspaceConfig) {
  const [modes, setModes] = useState<ModeInfo[]>([])
  const [stageLabels, setStageLabels] = useState<Record<string, string>>({})
  const [env, setEnv] = useState<EnvInfo | null>(null)
  const [mode, setMode] = useState<TranscribeMode | null>(null)
  const [params, setParams] = useState<Params>(DEFAULT_PARAMS)
  const [files, setFiles] = useState<DroppedFile[]>([])
  const [outputPath, setOutputPath] = useState<string>('')
  const [loadError, setLoadError] = useState<string | null>(null)

  const task = useTranscribeTask()

  /** 设置页配的默认输出目录（`''` = 未设，走「与源音频同目录」） */
  const defaultDir = useDefaultOutputDir()

  /**
   * 界面底部那句「将输出到 …」。
   * 返回 `null` = 没有特定落点，调用方显示「输出到源文件同目录」。
   */
  const outputHint = useMemo(() => {
    if (outputPath) return basename(outputPath)
    const first = files[0]
    if (!first) return null
    const name = `${stripExt(first.name)}.mid`
    return defaultDir ? joinOutputPath(defaultDir, name) : null
  }, [outputPath, files, defaultDir])

  const pageModes = useMemo(
    () => modes.filter((m) => m.page === config.page),
    [modes, config.page],
  )

  const activeModeInfo = useMemo(
    () => pageModes.find((m) => m.mode === mode) ?? null,
    [pageModes, mode],
  )

  const needsTwoSlots = useMemo(
    () => (config.twoSlotModes ?? []).includes(mode as TranscribeMode),
    [config.twoSlotModes, mode],
  )

  const isBasicMulti = mode === 'basic_multi'

  /**
   * 该模式最多接受几个输入文件。
   *   已分离直入（两槽）→ 2（人声 + 伴奏）
   *   多轨直扒         → 6（逐轨各出一轨）
   *   其余单轨直扒     → 1
   *
   * 必须由模式容量统一决定，不能只写在 DropZone 上：否则「先选多轨丢 3 个文件，
   * 再切到单轨模式」时，多出来的 2 个文件会**留在列表里但被引擎忽略** ——
   * 用户看到 3 个文件、只出 1 轨，且没有任何提示。静默吞文件是 bug。
   */
  const maxFiles = needsTwoSlots ? 2 : isBasicMulti ? 6 : 1

  /** 拉取模式元信息 + 环境自检（单一真源，不在前端硬编码模式文案） */
  const load = useCallback(async () => {
    setLoadError(null)
    try {
      const [m, e] = await Promise.all([ipc.getModes(), ipc.getEnvInfo()])
      const payload: ModesPayload = m
      setModes(payload.modes)
      setStageLabels(payload.stageLabels ?? {})
      setEnv(e)
      // 默认选中：优先 config.defaultMode（须属于本页），否则取本页第一条。
      // 刻意不写成「数组顺序即默认」这种隐式契约——引擎调整 describe_modes
      // 的条目顺序时，前端默认项会静默漂移，且没人会发现。
      const prefer = payload.modes.find(
        (x) => x.mode === config.defaultMode && x.page === config.page,
      )
      const initial = prefer ?? payload.modes.find((x) => x.page === config.page)
      if (initial) setMode((cur) => cur ?? initial.mode)
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err))
    }
  }, [config.page, config.defaultMode])

  /** 选择输出位置；取消则留空，由引擎放在输入文件同目录 */
  const chooseOutput = useCallback(async () => {
    const src = files[0]
    if (!src) return
    const suggested = `${stripExt(src.name)}.mid`
    const picked = await ipc.pickMidiOutput(suggested)
    setOutputPath(picked ?? '')
  }, [files])

  /** 提交扒谱 */
  const submit = useCallback(async () => {
    if (!mode || task.isRunning) return
    if (files.length < (needsTwoSlots ? 2 : 1)) return

    const req: TranscribeRequest = {
      mode,
      inputPath: files[0].path,
      extraInputs: files.slice(1).map((f) => f.path),
      nPeaks: params.nPeaks,
      hopLength: params.hopLength,
      onsetThreshold: params.onsetThreshold,
      pitchThreshold: params.pitchThreshold,
      minNoteDuration: params.minNoteDuration,
      tempo: params.tempo,
      simplify: params.simplify,
      perceptual: params.perceptual,
      pianoMode: params.pianoMode,
      allowHpssFallback: true,
      device: 'auto',
      demucsModel: 'htdemucs',
    }
    // 输出位置三档优先级：
    //   1. 本页「另存为」指定过 → 用它（单次覆盖，不动全局设置）
    //   2. 设置页配了默认输出目录 → 目录 + 与源音频同名的 .mid
    //   3. 都没有 → 不传，由引擎放在源音频同目录
    // 第 2 档必须**当场读**而非读 hook 值：用户可能在设置页刚改完就切回来提交。
    if (outputPath) {
      req.outputPath = outputPath
    } else {
      const dir = getDefaultOutputDir()
      if (dir) req.outputPath = joinOutputPath(dir, `${stripExt(files[0].name)}.mid`)
    }
    // 人声 + 伴奏直入时明确轨名顺序：[人声, 伴奏]
    if (mode === 'pre_separated') req.trackNames = ['Voice', 'Accompaniment']
    // 多轨直扒放了多个文件时逐轨编号，否则 N 条轨会同名「Instrument」，
    // 在 MuseScore 里完全分不清哪条对应哪个文件。
    // 轨名必须是纯 ASCII（引擎侧 midi_post 只允许 \x20-\x7e），
    // 所以不能用文件名，只能用「Instrument N」。
    if (mode === 'basic_multi' && files.length > 1) {
      req.trackNames = files.map((_, i) => `Instrument ${i + 1}`)
    }

    await task.start(req)
  }, [mode, files, task, needsTwoSlots, params, outputPath])

  /** 换文件时清掉旧的输出路径（默认名会跟着变） */
  const setFilesAndResetOutput = useCallback((next: DroppedFile[]) => {
    setFiles(next)
    setOutputPath('')
    task.reset()
  }, [task])

  /**
   * 把文件数裁到当前模式的容量，并清掉旧输出路径与任务结果。
   * 返回被裁掉的个数（0 = 没裁）。
   *
   * 为什么必须有：不裁的话，「先选多轨直扒丢 3 个文件，再切到单轨模式」会留下
   * 3 个文件在列表里，而引擎只读第一个 —— 用户看到 3 个文件却只出 1 轨，
   * 且毫无提示。这与「多轨静默吞文件」是同一类缺陷。
   */
  const trimFilesToCapacity = useCallback((): number => {
    if (files.length <= maxFiles) return 0
    setFilesAndResetOutput(files.slice(0, maxFiles))
    return files.length - maxFiles
  }, [files, maxFiles, setFilesAndResetOutput])

  const cancel = useCallback(() => void task.cancel(), [task])
  const resetAll = useCallback(() => {
    setFiles([])
    setOutputPath('')
    task.reset()
  }, [task])

  /** 提交按钮的可用性：文件齐、模式在、任务空闲 */
  const requiredSlots = needsTwoSlots ? 2 : 1
  const canSubmit = Boolean(mode) && files.length >= requiredSlots && !task.isRunning

  /** 缺文件的提示文案。空态不提示（拖放区自己说明了），只提示"放了一半" */
  const missingHint = useMemo(() => {
    if (files.length === 0) return null
    if (files.length < requiredSlots) {
      return needsTwoSlots ? '还需要伴奏音频' : null
    }
    return null
  }, [files.length, needsTwoSlots, requiredSlots])

  return {
    // 数据
    modes: pageModes,
    allModes: modes,
    stageLabels,
    env,
    mode,
    activeModeInfo,
    files,
    params,
    outputPath,
    outputHint,
    needsTwoSlots,
    isBasicMulti,
    maxFiles,
    // 状态
    task,
    loadError,
    canSubmit,
    missingHint,
    // 操作
    load,
    setMode,
    setParams,
    setFiles: setFilesAndResetOutput,
    trimFilesToCapacity,
    chooseOutput,
    submit,
    cancel,
    resetAll,
  }
}

/** 分离方式的可读标签 */
export function separationLabel(method: string | null): string | null {
  if (!method) return null
  switch (method) {
    case 'demucs':
      return 'Demucs 分离'
    case 'hpss':
      return 'HPSS 降级分离'
    case 'none':
      return null
    default:
      return method
  }
}

/** 探测失败时的兜底展示（正常路径由 DropZone 处理） */
export function probeMessage(r: ProbeResult | null): string {
  return r?.ok === false ? '该文件无法读取' : ''
}
