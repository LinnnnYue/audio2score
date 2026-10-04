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
 * ── 可视化提示（本次新增）────────────────────────────────────────
 * 原则：**不必 hover、不必点，扫一眼就懂「这个钮往哪边拖会发生什么」。**
 *   ① 方向轴（DirAxis）—— 把两个方向的后果直接标在控件两端；
 *      滑块轨道上加一根竖线标出**默认值位置**，一眼看出自己偏在哪边。
 *   ② 数量可视化（CountDots）—— 「同时音高数」用 N 个圆点画出「每格几个音」。
 *   ③ 影响范围徽章（Badge）—— 开关类标出它到底动了多少东西
 *      （钢琴模式「改 6 项」、感知模式「会重写音符」）。
 *   ④ ? 卡片（HintCard）—— 从纯文字升级为「本质 / 往左 / 往右 / 何时动」结构。
 *
 * ⚠️ 方向文案的**唯一依据是引擎源码**，不是面板旧提示语：
 *   - 起音灵敏度：onset_detection.py 里 `find_peaks(height=阈值×0.6)`，
 *     值越大门槛越高 → **检出的起音越少**。旧文案「越高越容易切出短音符」
 *     与之相反，已删除（不要改回去）。
 *   - 最小音符时长：note_tracking.py 里 `max(0.05, 帧数×hop÷22050)`，
 *     有 0.05 秒硬下限；帧移不同则同一档位的实际含义不同。
 *   - 钢琴模式：pipeline.py 里一次性覆盖 6 项（含 n_peaks=2）。
 * 面板内**只写机制，不写推荐值** —— 具体什么歌填什么值属未验证建议，
 * 由外部文档承载，不固化进 UI。
 *
 * 关键约束：感知模式（perceptual）默认关，且必须带 tooltip 说明
 * 「开启会重写音符，可能丢失和声声部」——这是引擎实测踩过的坑。
 */

import { ChevronDown, RotateCcw } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import clsx from 'clsx'
import type { TranscribeMode } from '../lib/types'
import { DEFAULT_PARAMS, type Params } from '../lib/params'
import { Tooltip } from './Tooltip'

/** 各模式的引擎默认值（pipeline.DEFAULT_N_PEAKS），仅用于 UI 展示「自动」 */
const DEFAULT_N_PEAKS: Record<TranscribeMode, number> = {
  full_auto: 6,
  accompaniment: 6,
  vocals: 2,
  basic: 5,
  // 单轨·人声旋律走 pYIN 单音高追踪，n_peaks 不参与运算；
  // 这里登记 2 只是为了让「此模式推荐 N」有据可依，与 vocals 对齐。
  basic_vocals: 2,
  basic_accompaniment: 6,
  basic_multi: 6,
  pre_separated: 5,
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
  /** 当前实际生效的同时音高数（自动时取模式默认） */
  const effPeaks = params.nPeaks ?? nPeaksHint ?? DEFAULT_N_PEAKS.basic

  return (
    <div className={clsx('flex flex-col', disabled && 'pointer-events-none opacity-50')}>
      {/* ---- 常用参数 ---- */}
      <div className="flex flex-col gap-3.5">
        <NumField
          label="同时音高数"
          hint={
            <HintCard
              what="每一格最多保留几个音。和弦越厚，需要的数越大。"
              smaller="只留最强的一两个音：旋律干净，但和弦会消失"
              bigger="和弦更完整，但泛音容易被当成额外声部，冒出无关碎音"
              when="和弦不全 → 调大；谱面出现无关碎音 → 调小"
            />
          }
          value={effPeaks}
          auto={params.nPeaks === null}
          min={1}
          max={12}
          step={1}
          onChange={(v) => set('nPeaks', v)}
          onAuto={() => set('nPeaks', null)}
          below={<CountDots value={effPeaks} />}
        />

        <NumField
          label="BPM"
          hint={
            <HintCard
              what="只写进 MIDI 的速度标记，完全不参与音符识别。"
              when="填成原曲的真实速度；填错会导致 MuseScore 里小节线全错位"
            />
          }
          value={params.tempo}
          min={30}
          max={300}
          step={1}
          suffix="BPM"
          onChange={(v) => set('tempo', v)}
          below={<NoteLine>决定小节线位置 · 不影响识别</NoteLine>}
        />

        <SliderField
          label="起音灵敏度"
          hint={
            <HintCard
              what="判定「这里算不算一个新音的开头」的门槛。它实质是一道阈值，不是灵敏度。"
              smaller="门槛低 → 检出的起音更多，音符更碎，也更容易把杂音切开"
              bigger="门槛高 → 检出的起音更少，音符更少更长、谱面更干净"
              when="副歌毛刺多 → 调大；快歌漏音 → 调小"
            />
          }
          value={params.onsetThreshold}
          min={0.05}
          max={0.8}
          step={0.01}
          format={(v) => v.toFixed(2)}
          defaultValue={DEFAULT_PARAMS.onsetThreshold}
          onChange={(v) => set('onsetThreshold', v)}
          below={<DirAxis from="音更碎 · 更敏感" to="音更少 · 更干净" />}
        />

        <SliderField
          label="最小音符时长"
          hint={
            <HintCard
              what="短于这个长度的音会被直接删掉。这是「丢内容」的头号开关。"
              smaller="保留更多短音、装饰音，但抖动杂音也可能被留下"
              bigger="杂音毛刺被清掉，但真实存在的短音符可能被误删"
              when="谱面毛刺多 → 调大；快歌漏短音 → 调小"
              warn="单位是秒（帧数 × 帧移 ÷ 22050），且有 0.05 秒下限 —— 帧移不同时，同一档位的实际含义也不同"
            />
          }
          value={params.minNoteDuration}
          min={1}
          max={16}
          step={1}
          format={(v) => `${v} 帧`}
          defaultValue={DEFAULT_PARAMS.minNoteDuration}
          onChange={(v) => set('minNoteDuration', v)}
          below={<DirAxis from="留更多短音" to="删掉短音 · 更干净" />}
        />

        <SliderField
          label="精简强度"
          hint={
            <HintCard
              what="扒完之后做一次打扫：去毛刺、合并挨得太近的同音、每个时间窗只留一个音。"
              smaller="0 表示完全关闭，忠实保留全部识别结果"
              bigger="谱面明显变简洁，但会把真实的密集音符一起合掉"
              when="音符都对但读着乱 → 1–2 档；快歌密集音符保持 0"
            />
          }
          value={params.simplify}
          min={0}
          max={5}
          step={1}
          format={(v) => (v === 0 ? '关闭' : String(v))}
          defaultValue={DEFAULT_PARAMS.simplify}
          onChange={(v) => set('simplify', v)}
          below={<DirAxis from="一个都不删" to="删得最狠" />}
        />
      </div>

      {/* ---- 开关 ---- */}
      <div className="mt-4 flex flex-col gap-3 border-t border-line pt-3.5">
        <ToggleRow
          label="钢琴模式"
          badge={<Badge>改 6 项</Badge>}
          hint={
            <HintCard
              what="一次改 6 个设置：帧移 256、起音 0.15、音高 0.2、同时音高数 2、逐帧中值滤波、精简 2 档。"
              when="钢琴、弦乐这类音头清晰的乐器"
              warn="⚠ 会覆盖你手动填的那几项 —— 尤其是「同时音高数」被压到 2，厚和弦会丢"
            />
          }
          checked={params.pianoMode}
          onChange={(v) => set('pianoMode', v)}
        />
        <ToggleRow
          label="感知模式"
          badge={<Badge tone="danger">会重写音符</Badge>}
          hint={
            /* 主上要求：必须原文点出风险 */
            <HintCard
              what="换一套算法（跟着起音走 + 感知过滤），会对识别出的音符做取舍与重写。"
              when="其他参数都试过、多轨结果仍明显有杂音时，才最后试它"
              warn="⚠ 开启会重写音符，可能丢失和声声部。默认关闭。"
            />
          }
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
            '[[@media(hover:hover)_and_(pointer:fine)]]:hover:bg-accent-soft',
            '[[@media(hover:hover)_and_(pointer:fine)]]:hover:text-ink-dim',
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
                hint={
                  <HintCard
                    what="把音频切成多大的格子。它是谱面的最小时间单位，也是精细度的总闸。"
                    smaller="格子更小 → 时间分辨率更高，能看清密集快音符；代价是耗时与内存上升"
                    bigger="格子更大 → 更快更省资源，但快音符会糊成一片、大量漏音"
                    when="快歌漏音时第一个该动的（512 → 256）"
                  />
                }
                value={params.hopLength}
                min={64}
                max={2048}
                step={64}
                onChange={(v) => set('hopLength', v)}
                below={<DirAxis from="更精细 · 更慢" to="更粗 · 更快" />}
              />
              <SliderField
                label="音高阈值"
                hint={
                  <HintCard
                    what="判断「这个峰算不算一个真音」的显著性门槛。"
                    smaller="弱音、轻声、内声部会被保留，但底噪也可能变成音符"
                    bigger="只留最明显最强的音，谱面干净但弱声部会整个消失"
                    when="和弦不全或丢内声部 → 调小；底噪变成音符 → 调大"
                  />
                }
                value={params.pitchThreshold}
                min={0.01}
                max={0.5}
                step={0.01}
                format={(v) => v.toFixed(2)}
                defaultValue={DEFAULT_PARAMS.pitchThreshold}
                onChange={(v) => set('pitchThreshold', v)}
                below={<DirAxis from="留下弱音 · 内声部" to="只留明显的音" />}
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
 * 可视化提示零件
 * ------------------------------------------------------------------ */

/** 方向轴：把「往左拖 / 往右拖」的后果标在控件两端（常驻，无需 hover） */
function DirAxis({ from, to }: { from: string; to: string }) {
  return (
    <div className="mt-1.5 flex items-center gap-2 text-[10.5px] leading-none text-ink-faint">
      <span className="shrink-0 whitespace-nowrap">{from}</span>
      <span className="h-px min-w-2 flex-1 bg-line" aria-hidden />
      <span className="shrink-0 whitespace-nowrap text-right">{to}</span>
    </div>
  )
}

/** 单行说明：用于不适合画方向轴的参数（如 BPM） */
function NoteLine({ children }: { children: ReactNode }) {
  return <div className="mt-1.5 text-[10.5px] leading-none text-ink-faint">{children}</div>
}

/** 数量可视化：N 个实心圆点 = 每格最多留 N 个音 */
function CountDots({ value, max = 12 }: { value: number; max?: number }) {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1">
      <span className="flex items-center gap-[3px]" aria-hidden>
        {Array.from({ length: max }, (_, i) => (
          <span
            key={i}
            className={clsx(
              'h-[5px] w-[5px] rounded-full transition-colors duration-150 ease-out',
              i < value ? 'bg-accent' : 'bg-line',
            )}
          />
        ))}
      </span>
      <span className="text-[10.5px] leading-none text-ink-faint">
        每格最多同时留 {value} 个音
      </span>
    </div>
  )
}

/** 影响范围徽章：开关类参数标出「它到底动了多少东西」 */
function Badge({ children, tone = 'accent' }: { children: ReactNode; tone?: 'accent' | 'danger' }) {
  return (
    <span
      className={clsx(
        'shrink-0 rounded-[5px] border px-1.5 py-[1px] text-[10px] font-medium leading-[15px]',
        tone === 'danger'
          ? 'border-[rgba(180,68,58,0.32)] bg-[var(--danger-soft)] text-bad'
          : 'border-[var(--accent-border)] bg-accent-soft text-accent',
      )}
    >
      {children}
    </span>
  )
}

/** ? 卡片：本质 / 往左 / 往右 / 何时动 / 警示 */
function HintCard({
  what,
  smaller,
  bigger,
  when,
  warn,
}: {
  what: string
  smaller?: string
  bigger?: string
  when?: string
  warn?: string
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <div>{what}</div>
      {(smaller || bigger) && (
        <div className="flex flex-col gap-1 border-t border-line pt-1.5">
          {smaller && (
            <div>
              <span className="font-semibold text-ink">往左·调小</span> · {smaller}
            </div>
          )}
          {bigger && (
            <div>
              <span className="font-semibold text-ink">往右·调大</span> · {bigger}
            </div>
          )}
        </div>
      )}
      {when && <div className="text-[10.5px] text-ink-faint">何时该动：{when}</div>}
      {warn && <div className="text-[10.5px] font-medium text-warn">{warn}</div>}
    </div>
  )
}

/* ------------------------------------------------------------------ *
 * 子控件
 * ------------------------------------------------------------------ */

function FieldShell({
  label,
  hint,
  below,
  children,
}: {
  label: string
  hint: ReactNode
  below?: ReactNode
  children: ReactNode
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-1.5">
        <span className="text-[11.5px] font-medium text-ink-dim">{label}</span>
        <Tooltip content={hint} wide>
          <button
            type="button"
            aria-label={`${label} 说明`}
            className={clsx(
              'flex h-[14px] w-[14px] items-center justify-center rounded-full',
              'text-[9px] font-semibold text-ink-faint',
              'border border-line transition-colors duration-150 ease-out',
              '[[@media(hover:hover)_and_(pointer:fine)]]:hover:border-accent',
              '[[@media(hover:hover)_and_(pointer:fine)]]:hover:text-accent',
            )}
          >
            ?
          </button>
        </Tooltip>
      </div>
      {children}
      {below}
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
  below,
}: {
  label: string
  hint: ReactNode
  value: number
  min: number
  max: number
  step: number
  suffix?: string
  auto?: boolean
  onChange: (v: number) => void
  onAuto?: () => void
  below?: ReactNode
}) {
  const clamp = (v: number) => Math.min(max, Math.max(min, v))
  return (
    <FieldShell label={label} hint={hint} below={below}>
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
                : 'border-line text-ink-faint [[@media(hover:hover)_and_(pointer:fine)]]:hover:border-[var(--border-strong)]',
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
  defaultValue,
  onChange,
  below,
}: {
  label: string
  hint: ReactNode
  value: number
  min: number
  max: number
  step: number
  format: (v: number) => string
  /** 传入默认值时，在轨道上画一根竖线标出默认位置 */
  defaultValue?: number
  onChange: (v: number) => void
  below?: ReactNode
}) {
  const pct = ((value - min) / (max - min)) * 100
  const defPct =
    defaultValue === undefined ? null : ((defaultValue - min) / (max - min)) * 100

  return (
    <FieldShell label={label} hint={hint} below={below}>
      <div className="flex items-center gap-2.5">
        {/* 轨道单独一层：input 自身透明，填充段用绝对定位的div 画。
            （若把渐变设在 input 上，会铺满 32px 高的输入框而非 3px 轨道） */}
        <div className="relative min-w-0 flex-1">
          <div className="pointer-events-none absolute inset-x-0 top-1/2 h-[3px] -translate-y-1/2 overflow-hidden rounded-full bg-[var(--stage-track)]">
            <div
              className="h-full rounded-full bg-accent transition-[width] duration-150 ease-out"
              style={{ width: `${pct}%` }}
            />
          </div>
          {/* 默认值刻度：一眼看出当前偏离默认多远 */}
          {defPct !== null && (
            <span
              title="默认值"
              aria-hidden
              className="pointer-events-none absolute top-1/2 h-[11px] w-px -translate-y-1/2 bg-[var(--border-strong)]"
              style={{ left: `${defPct}%` }}
            />
          )}
          <input
            type="range"
            min={min}
            max={max}
            step={step}
            value={value}
            onChange={(e) => onChange(Number(e.target.value))}
            className="relative h-[32px] w-full cursor-pointer appearance-none bg-transparent
              [&::-webkit-slider-runnable-track]:h-[3px] [&::-webkit-slider-runnable-track]:bg-transparent
              [&::-webkit-slider-thumb]:mt-[-5px] [&::-webkit-slider-thumb]:h-[13px] [&::-webkit-slider-thumb]:w-[13px]
              [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full
              [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-[var(--accent)]
              [&::-webkit-slider-thumb]:bg-[var(--surface-3)]
              [&::-webkit-slider-thumb]:shadow-[0_1px_3px_rgba(0,0,0,0.2)]
              [&::-webkit-slider-thumb]:transition-transform [&::-webkit-slider-thumb]:duration-150
              [&::-webkit-slider-thumb]:ease-out
              active:[&::-webkit-slider-thumb]:scale-[1.15]
              [[@media(hover:hover)_and_(pointer:fine)]]:hover:[&::-webkit-slider-thumb]:scale-[1.08]"
          />
        </div>
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
  badge,
}: {
  label: string
  hint: ReactNode
  checked: boolean
  onChange: (v: boolean) => void
  danger?: boolean
  badge?: ReactNode
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11.5px] font-medium text-ink-dim">{label}</span>
          <Tooltip content={hint} wide>
            <button
              type="button"
              aria-label={`${label} 说明`}
              className={clsx(
                'flex h-[14px] w-[14px] items-center justify-center rounded-full',
                'text-[9px] font-semibold text-ink-faint border border-line',
                'transition-colors duration-150 ease-out',
                '[[@media(hover:hover)_and_(pointer:fine)]]:hover:border-accent',
                '[[@media(hover:hover)_and_(pointer:fine)]]:hover:text-accent',
              )}
            >
              ?
            </button>
          </Tooltip>
          {badge}
        </div>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className={clsx(
          'relative mt-[2px] h-[18px] w-[32px] shrink-0 rounded-full',
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
