/**
 * ParamPanel.tsx — 扒谱参数区
 *
 * 全部参数与 engine TranscribeRequest 一一对应：
 *   n_peaks / hop_length / onset_threshold / pitch_threshold
 *   min_note_duration / tempo / simplify / perceptual / piano_mode
 *
 * 设计取向：工具面板感 —— 参数密而不乱，左标签右控件，等宽数字对齐，
 * 高级项折叠（默认收起）避免首屏信息过载。
 *
 * 关键约束：感知模式（perceptual）默认关，且必须带 tooltip 说明
 * 「开启会重写音符，可能丢失和声声部」——这是引擎实测踩过的坑。
 */

import { ChevronDown, RotateCcw } from 'lucide-react'
import { useState } from 'react'
import clsx from 'clsx'
import type { TranscribeMode } from '../lib/types'
import { Tooltip } from './Tooltip'

export interface Params {
  /** null = 用引擎按模式调好的默认值 */
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

/** 各模式的引擎默认值（pipeline.DEFAULT_N_PEAKS），仅用于 UI 展示「自动」 */
const DEFAULT_N_PEAKS: Record<TranscribeMode, number> = {
  full_auto: 6,
  accompaniment: 6,
  vocals: 2,
  basic: 5,
  basic_multi: 6,
  pre_separated: 5,
}

export const DEFAULT_PARAMS: Params = {
  nPeaks: null,
  hopLength: 512,
  onsetThreshold: 0.3,
  pitchThreshold: 0.1,
  minNoteDuration: 4,
  tempo: 120,
  simplify: 0,
  perceptual: false,
  pianoMode: false,
}

interface Props {
  params: Params
  onChange: (p: Params) => void
  mode: TranscribeMode | null
  disabled?: boolean
}

export function ParamPanel({ params, onChange, mode, disabled }: Props) {
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const set = <K extends keyof Params>(k: K, v: Params[K]) => onChange({ ...params, [k]: v })

  const nPeaksHint = mode ? (params.nPeaks ?? DEFAULT_N_PEAKS[mode]) : null

  return (
    <div className={clsx('flex flex-col', disabled && 'pointer-events-none opacity-50')}>
      {/* ---- 常用参数 ---- */}
      <div className="flex flex-col gap-3.5">
        <NumField
          label="同时音高数"
          hint={`每帧最多取几个音。越高越容易误判杂音。此模式推荐 ${nPeaksHint ?? '—'}`}
          value={params.nPeaks ?? nPeaksHint ?? DEFAULT_N_PEAKS.basic}
          auto={params.nPeaks === null}
          min={1}
          max={12}
          step={1}
          onChange={(v) => set('nPeaks', v)}
          onAuto={() => set('nPeaks', null)}
        />

        <NumField
          label="BPM"
          hint="写入 MIDI 的速度。影响播放速度，不影响音符识别"
          value={params.tempo}
          min={30}
          max={300}
          step={1}
          suffix="BPM"
          onChange={(v) => set('tempo', v)}
        />

        <SliderField
          label="起音灵敏度"
          hint="越高越容易切出短音符。过低会合并相邻音符"
          value={params.onsetThreshold}
          min={0.05}
          max={0.8}
          step={0.01}
          format={(v) => v.toFixed(2)}
          onChange={(v) => set('onsetThreshold', v)}
        />

        <SliderField
          label="最小音符时长"
          hint="短于此长度的音符会被丢弃，用于压掉抖动杂音"
          value={params.minNoteDuration}
          min={1}
          max={16}
          step={1}
          format={(v) => `${v} 帧`}
          onChange={(v) => set('minNoteDuration', v)}
        />

        <SliderField
          label="精简强度"
          hint="合并时值相近的音符，0为关闭"
          value={params.simplify}
          min={0}
          max={5}
          step={1}
          format={(v) => (v === 0 ? '关闭' : String(v))}
          onChange={(v) => set('simplify', v)}
        />
      </div>

      {/* ---- 开关 ---- */}
      <div className="mt-4 flex flex-col gap-2.5 border-t border-line pt-3.5">
        <ToggleRow
          label="钢琴模式"
          hint="更高时间分辨率 + 中值滤波，钢琴与弦乐更准，其他乐器可能过度平滑"
          checked={params.pianoMode}
          onChange={(v) => set('pianoMode', v)}
        />
        <ToggleRow
          label="感知模式"
          /* 主上要求：必须原文点出风险 */
          hint="开启会重写音符，可能丢失和声声部。默认关闭，仅在多轨结果明显有杂音时尝试"
          checked={params.perceptual}
          danger={params.perceptual}
          onChange={(v) => set('perceptual', v)}
        />
      </div>

      {/* ---- 高级 ---- */}
      <div className="mt-3.5 border-t border-line pt-3">
        <button
          type="button"
          onClick={() => setAdvancedOpen((v) => !v)}
          aria-expanded={advancedOpen}
          className={clsx(
            'flex w-full items-center gap-1.5 rounded-[var(--r-sm)] px-1.5 py-1',
            'text-[11px] font-medium text-ink-faint',
            'transition-colors duration-150 ease-out',
            '[@media(hover:hover)and(pointer:fine)]:hover:bg-accent-soft',
            '[@media(hover:hover)and(pointer:fine)]:hover:text-ink-dim',
          )}
        >
          <ChevronDown
            size={12}
            strokeWidth={2}
            className={clsx('transition-transform duration-200 ease-out', advancedOpen && 'rotate-180')}
          />
          高级参数
        </button>

        <div
          className={clsx(
            'grid origin-top overflow-hidden transition-[opacity,transform] duration-200 ease-out',
            advancedOpen
              ? 'grid-rows-[1fr] pt-2.5 opacity-100'
              : 'grid-rows-[0fr] pt-0 opacity-0',
          )}
        >
          <div className="min-h-0">
            <div className="flex flex-col gap-3.5">
              <NumField
                label="帧移长度"
                hint="采样 hop。越小时间分辨率越高，耗时与内存也越高"
                value={params.hopLength}
                min={64}
                max={2048}
                step={64}
                onChange={(v) => set('hopLength', v)}
              />
              <SliderField
                label="音高阈值"
                hint="CQT 峰值显著性门槛，越高只保留最明显的音"
                value={params.pitchThreshold}
                min={0.01}
                max={0.5}
                step={0.01}
                format={(v) => v.toFixed(2)}
                onChange={(v) => set('pitchThreshold', v)}
              />
              <button
                type="button"
                onClick={() => onChange(DEFAULT_PARAMS)}
                className="btn btn-ghost h-[30px] self-start px-2 text-[11.5px]"
              >
                <RotateCcw size={12} strokeWidth={1.9} />
                恢复默认参数
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ *
 * 子控件
 * ------------------------------------------------------------------ */

function FieldShell({
  label,
  hint,
  children,
}: {
  label: string
  hint: string
  children: React.ReactNode
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-1.5">
        <span className="text-[11.5px] font-medium text-ink-dim">{label}</span>
        <Tooltip content={hint}>
          <button
            type="button"
            aria-label={`${label} 说明`}
            className={clsx(
              'flex h-[14px] w-[14px] items-center justify-center rounded-full',
              'text-[9px] font-semibold text-ink-faint',
              'border border-line transition-colors duration-150 ease-out',
              '[@media(hover:hover)and(pointer:fine)]:hover:border-accent',
              '[@media(hover:hover)and(pointer:fine)]:hover:text-accent',
            )}
          >
            ?
          </button>
        </Tooltip>
      </div>
      {children}
    </div>
  )
}

function NumField({
  label,
  hint,
  value,
  min,
  max,
  step,
  suffix,
  auto,
  onChange,
  onAuto,
}: {
  label: string
  hint: string
  value: number
  min: number
  max: number
  step: number
  suffix?: string
  auto?: boolean
  onChange: (v: number) => void
  onAuto?: () => void
}) {
  const clamp = (v: number) => Math.min(max, Math.max(min, v))
  return (
    <FieldShell label={label} hint={hint}>
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          aria-label={`${label} 减少`}
          onClick={() => onChange(clamp(value - step))}
          className="btn h-[32px] w-[28px] shrink-0 px-0 text-[14px] leading-none"
        >
          −
        </button>
        <div className="relative min-w-0 flex-1">
          <input
            type="number"
            className="field field-num text-center"
            value={value}
            min={min}
            max={max}
            step={step}
            onChange={(e) => {
              const v = Number(e.target.value)
              if (Number.isFinite(v)) onChange(clamp(v))
            }}
          />
          {suffix && (
            <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-ink-faint">
              {suffix}
            </span>
          )}
        </div>
        <button
          type="button"
          aria-label={`${label} 增加`}
          onClick={() => onChange(clamp(value + step))}
          className="btn h-[32px] w-[28px] shrink-0 px-0 text-[14px] leading-none"
        >
          +
        </button>
        {onAuto && (
          <button
            type="button"
            onClick={onAuto}
            aria-pressed={auto}
            className={clsx(
              'h-[32px] shrink-0 rounded-[var(--r-sm)] border px-2 text-[10.5px] font-medium',
              'transition-[background-color,border-color,color,transform] duration-150 ease-out',
              'active:scale-[0.96]',
              auto
                ? 'border-transparent bg-accent-soft text-accent'
                : 'border-line text-ink-faint [@media(hover:hover)and(pointer:fine)]:hover:border-[var(--border-strong)]',
            )}
          >
            自动
          </button>
        )}
      </div>
    </FieldShell>
  )
}

function SliderField({
  label,
  hint,
  value,
  min,
  max,
  step,
  format,
  onChange,
}: {
  label: string
  hint: string
  value: number
  min: number
  max: number
  step: number
  format: (v: number) => string
  onChange: (v: number) => void
}) {
  const pct = ((value - min) / (max - min)) * 100
  return (
    <FieldShell label={label} hint={hint}>
      <div className="flex items-center gap-2.5">
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          /* 轨道用渐变模拟"已填充"段，避免额外DOM */
          style={{
            background: `linear-gradient(to right, var(--accent) 0%, var(--accent) ${pct}%, var(--stage-track) ${pct}%, var(--stage-track) 100%)`,
          }}
          className="h-[32px] min-w-0 flex-1 cursor-pointer appearance-none rounded-full
            [&::-webkit-slider-runnable-track]:h-[3px] [&::-webkit-slider-runnable-track]:rounded-full
            [&::-webkit-slider-runnable-track]:bg-transparent
            [&::-webkit-slider-thumb]:mt-[-5px] [&::-webkit-slider-thumb]:h-[13px] [&::-webkit-slider-thumb]:w-[13px]
            [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full
            [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-[var(--accent)]
            [&::-webkit-slider-thumb]:bg-[var(--surface-3)]
            [&::-webkit-slider-thumb]:transition-transform [&::-webkit-slider-thumb]:duration-150
            [&::-webkit-slider-thumb]:ease-out
            active:[&::-webkit-slider-thumb]:scale-[1.15]
            [@media(hover:hover)and(pointer:fine)]:hover:[&::-webkit-slider-thumb]:scale-[1.08]"
        />
        <span className="num w-[52px] shrink-0 text-right text-[11.5px] text-ink">
          {format(value)}
        </span>
      </div>
    </FieldShell>
  )
}

function ToggleRow({
  label,
  hint,
  checked,
  onChange,
  danger,
}: {
  label: string
  hint: string
  checked: boolean
  onChange: (v: boolean) => void
  danger?: boolean
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="text-[11.5px] font-medium text-ink-dim">{label}</span>
          <Tooltip content={hint}>
            <button
              type="button"
              aria-label={`${label} 说明`}
              className={clsx(
                'flex h-[14px] w-[14px] items-center justify-center rounded-full',
                'text-[9px] font-semibold text-ink-faint border border-line',
                'transition-colors duration-150 ease-out',
                '[@media(hover:hover)and(pointer:fine)]:hover:border-accent',
                '[@media(hover:hover)and(pointer:fine)]:hover:text-accent',
              )}
            >
              ?
            </button>
          </Tooltip>
        </div>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className={clsx(
          'relative h-[18px] w-[32px] shrink-0 rounded-full',
          'transition-colors duration-200 ease-out',
          'active:scale-[0.96]',
          checked
            ? danger
              ? 'bg-bad'
              : 'bg-accent'
            : 'border border-line bg-[var(--stage-track)]',
        )}
      >
        <span
          className={clsx(
            'absolute top-[2px] h-[14px] w-[14px] rounded-full bg-white shadow-sm',
            'transition-transform duration-200 ease-out',
            checked ? 'translate-x-[16px]' : 'translate-x-[2px]',
          )}
        />
      </button>
    </div>
  )
}
