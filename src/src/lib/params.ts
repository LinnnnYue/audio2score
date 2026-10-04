/**
 * params.ts — 扒谱参数的类型与默认值
 *
 * 独立于组件文件：ParamPanel 只导出组件，Fast Refresh 才能正常工作。
 * 字段与 engine TranscribeRequest 一一对应。
 */

export interface Params {
  /** null = 用引擎按模式调好的默认值（pipeline.DEFAULT_N_PEAKS） */
  nPeaks: number | null
  hopLength: number
  onsetThreshold: number
  pitchThreshold: number
  minNoteDuration: number
  tempo: number
  simplify: number
  perceptual: boolean
  pianoMode: boolean
}

export const DEFAULT_PARAMS: Params = {
  nPeaks: null,
  hopLength: 512,
  onsetThreshold: 0.3,
  pitchThreshold: 0.1,
  minNoteDuration: 4,
  tempo: 120,
  simplify: 0,
  // 引擎实测：感知模式会重写音符，默认必须关
  perceptual: false,
  pianoMode: false,
}
