/**
 * useWorkspace.ts — 两个功能页共享的作业逻辑
 *
 * 抽出共用部分是为了让两页的**状态流转完全一致**（空态 → 处理中 → 完成/失败），
 * 避免两页各写一套导致某个边角状态缺失。
 *
 * 各页差异只在「要几个文件槽」：
 *   功能页1 歌曲扒谱   → 1 个主输入
 *   功能页2 基本扒谱   → 1 个主输入，或 2 个（人声 + 伴奏，已分离直入）
 */

import { useCallback, useMemo, useState } from 'react'
import * as ipc from '../lib/ipc'
import { basename, stripExt } from '../lib/format'
import type {
  EnvInfo,
  ModeInfo,
  ModesPayload,
  ProbeResult,
  TranscribeMode,
  TranscribeRequest,
} from '../lib/types'
import { DEFAULT_PARAMS, type Params } from '../components/ParamPanel'
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

  /** 拉取模式元信息 + 环境自检（单一真源，不在前端硬编码模式文案） */
  const load = useCallback(async () => {
    setLoadError(null)
    try {
      const [m, e] = await Promise.all([ipc.getModes(), ipc.getEnvInfo()])
      const payload: ModesPayload = m
      setModes(payload.modes)
      setStageLabels(payload.stageLabels ?? {})
      setEnv(e)
      // 默认选中该页第一个模式
      const first = payload.modes.find((x) => x.page === config.page)
      if (first) setMode((cur) => cur ?? first.mode)
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err))
    }
  }, [config.page])

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
    if (outputPath) req.outputPath = outputPath
    // 人声 + 伴奏直入时明确轨名顺序：[人声, 伴奏]
    if (mode === 'pre_separated') req.trackNames = ['Voice', 'Accompaniment']

    await task.start(req)
  }, [mode, files, task, needsTwoSlots, params, outputPath])

  /** 换文件时清掉旧的输出路径（默认名会跟着变） */
  const setFilesAndResetOutput = useCallback((next: DroppedFile[]) => {
    setFiles(next)
    setOutputPath('')
    task.reset()
  }, [task])

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
    needsTwoSlots,
    isBasicMulti,
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

/** 输出文件名的预览（未手动指定时按引擎默认推导） */
export function previewOutput(files: DroppedFile[], outputPath: string): string | null {
  if (outputPath) return basename(outputPath)
  if (files.length === 0) return null
  return `${stripExt(files[0].name)}.mid`
}
