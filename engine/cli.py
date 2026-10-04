"""
cli — 扒谱命令行工具

## 定位
同一个引擎核心，三种接法：
    GUI  → Tauri → engine/bridge.py（JSON 行协议）
    CLI  → 直接本文件（argparse）
    MCP  → engine/mcp_server.py（stdio JSON-RPC）→ 本文件的核心函数

三者共用 `pipeline.transcribe`，故行为完全一致。**不重复实现任何业务逻辑。**

## 设计原则
1. **双击之外的最后退路**——主上想在终端里直接扒谱，或写批处理脚本
2. **退出码即契约**——0 成功，1 业务失败，2 参数错误，3 环境未就绪
   （供 shell 脚本与 CI 判定，不靠解析 stdout）
3. **中文面向用户文案**——错误信息人可读，不抛堆栈
4. **--json 供机器消费**——CI / 上层脚本要结构化输出

## 用法速查
```bash
# 最简：拖进去扒谱，输出到同目录同名 .mid
python engine/cli.py 歌曲.mp3

# 指定模式与输出
python engine/cli.py 歌曲.mp3 -m vocals -o 旋律.mid

# 多文件批量
python engine/cli.py *.mp3 -m accompaniment

# 进第二页：已分离好的音频直入
python engine/cli.py 人声.wav -m pre_separated --extra 伴奏.wav

# 机器消费
python engine/cli.py 歌曲.mp3 --json

# 看能力（是否装齐、GPU 在不在）
python engine/cli.py --caps
```
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

# ── 退出码契约（供 shell / CI 判定，勿改）──
EXIT_OK = 0
EXIT_FAIL = 1        # 业务失败（文件坏了、扒不出音符等）
EXIT_USAGE = 2       # 参数错误（argparse 自身也用 2）
EXIT_ENV = 3         # 环境未就绪（依赖缺失、ffmpeg 缺失）


def _print_err(msg: str) -> None:
    sys.stderr.write(f"\n[扒谱] {msg}\n")


def _mode_choices() -> list[str]:
    from pipeline import describe_modes

    return [m["mode"] for m in describe_modes()]


def _mode_labels() -> dict[str, str]:
    from pipeline import describe_modes

    return {m["mode"]: m["label"] for m in describe_modes()}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bapu",
        description="扒谱助手 · 命令行版 — 输入音频，输出 MuseScore 可用的 MIDI 乐谱。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
可用模式:
  full_auto      全自动扒谱（人声 + 伴奏双轨）
  accompaniment  只扒伴奏
  vocals         只扒人声旋律
  basic          基本扒谱（乐器 / 单音轨）
  basic_multi    基本扒谱（多音轨）
  pre_separated  已分离音频直入（配合 --extra 传入伴奏）

示例:
  bapu 歌曲.mp3                          最简用法
  bapu 歌曲.mp3 -m vocals -o 旋律.mid    只扒人声
  bapu *.mp3 -m accompaniment            批量扒伴奏
  bapu 人声.wav -m pre_separated --extra 伴奏.wav
  bapu 歌曲.mp3 --json                   输出 JSON 供脚本消费
  bapu --caps                            查看环境能力

退出码: 0 成功 / 1 失败 / 2 参数错 / 3 环境未就绪
""",
    )
    p.add_argument("input", nargs="?", help="输入音频路径（wav/mp3/flac/m4a/aac/ogg/opus/wma/aiff/wv）")
    p.add_argument(
        "-m", "--mode", default="basic",
        choices=_mode_choices(),
        help="扒谱模式（默认 basic，不分离）",
    )
    p.add_argument("-o", "--output", help="输出 .mid 路径（默认与输入同名）")

    g = p.add_argument_group("已分离音频直入（-m pre_separated）")
    g.add_argument("--extra", action="append", default=[],
                   help="已分离好的其他音轨路径，可重复（如伴奏）")

    g = p.add_argument_group("扒谱参数")
    g.add_argument("--n-peaks", type=int, help="每帧最大同时音符数（和弦 5-8，人声 1-2）")
    g.add_argument("--hop", type=int, default=512, help="帧移，越小时间分辨率越高（默认 512）")
    g.add_argument("--onset", type=float, default=0.3, help="起始检测灵敏度 0~1（默认 0.3）")
    g.add_argument("--pitch", type=float, default=0.1, help="音高检测阈值 0~1（默认 0.1）")
    g.add_argument("--min-note", type=int, default=4, help="最小音符时长（帧，默认 4）")
    g.add_argument("--tempo", type=float, default=120.0, help="MIDI 速度 BPM（默认 120）")
    g.add_argument("--simplify", type=int, default=0, help="音符精简强度 0=关 2/3/5 递增")
    g.add_argument("--perceptual", action="store_true",
                   help="感知模式（默认关。开启会重写音符，多声部可能丢失）")
    g.add_argument("--piano", action="store_true", help="钢琴优化：更高时间分辨率 + 中值滤波")

    g = p.add_argument_group("分离选项（需要 -m full_auto/accompaniment/vocals）")
    g.add_argument("--model", default="htdemucs", help="Demucs 模型（默认 htdemucs）")
    g.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"],
                   help="分离设备（默认 auto，有 CUDA 就用）")
    g.add_argument("--no-fallback", action="store_true",
                   help="禁用中频分离降级（Demucs 失败即报错，不降级）")

    g = p.add_argument_group("输出")
    g.add_argument("--json", action="store_true", help="以 JSON 输出结果（供脚本消费）")
    g.add_argument("--quiet", "-q", action="store_true", help="不打印进度")
    g.add_argument("--caps", action="store_true", help="打印环境能力后退出")
    p.add_argument("--version", action="version", version="扒谱助手 CLI 0.1.0")
    return p


def _default_output(input_path: str) -> str:
    base = os.path.splitext(os.path.abspath(input_path))[0]
    return base + ".mid"


def _guard_output_path(out: str) -> str | None:
    """
    输出路径护栏。返回错误文案，None 表示通过。

    ### 踩坑实录（自己造的坑）
    CLI 首次实测时，输入用 `../third_party/AutoTranscriber/test_audio/chord_progression.wav`，
    默认输出落到同目录 → **在 vendored 只读树里生成了 .mid**，
    `git -C third_party/AutoTranscriber status` 立刻显示 `?? test_audio/chord_progression.mid`。
    这违反了红线 B-1（上游树零改动），而且任何时候都可能污染用户的目录结构。

    故加两道护栏：
      1. 硬拦 vendored 只读树（third_party/）与引擎自身目录
      2. 软提示：输入在只读位置时，默认输出改到当前工作目录
    """
    engine_dir = os.path.abspath(_ENGINE_DIR)
    out_abs = os.path.abspath(out)

    # 护栏 1：禁止写进引擎自身目录与 vendored 树
    for forbidden, label in (
        (engine_dir, "引擎目录"),
        (os.path.join(os.path.dirname(engine_dir), "third_party"), "上游只读目录"),
    ):
        f = os.path.abspath(forbidden)
        if out_abs == f or out_abs.startswith(f + os.sep):
            return (
                f"拒绝把输出写进{label}：\n  {out_abs}\n"
                f"请用 -o 指定其他位置。"
            )

    # 护栏 2：输入在只读位置时，默认输出改到当前工作目录
    return None


def _resolve_output(args: argparse.Namespace) -> str | None:
    """决定最终输出路径。返回 None 表示应当退出（已在 stderr 报错）。"""
    engine_dir = os.path.abspath(_ENGINE_DIR)
    read_only = (
        engine_dir,
        os.path.join(os.path.dirname(engine_dir), "third_party"),
    )

    if args.output:
        err = _guard_output_path(args.output)
        if err:
            _print_err(err)
            return None
        out = os.path.abspath(args.output)
    else:
        base = os.path.splitext(os.path.abspath(args.input))[0]
        out = base + ".mid"
        # 输入位于只读位置时，输出改落当前工作目录，避免污染
        for f in read_only:
            f = os.path.abspath(f)
            if os.path.abspath(args.input).startswith(f + os.sep):
                name = os.path.splitext(os.path.basename(args.input))[0]
                out = os.path.abspath(name + ".mid")
                if not args.json:
                    sys.stderr.write(
                        f"[扒谱] 输入位于只读位置，输出改写到当前目录：{out}\n"
                    )
                break

    if args.mode == "pre_separated" and args.extra:
        b, ext = os.path.splitext(out)
        out = f"{b}_multi{ext or '.mid'}"
    return out


def _print_caps() -> None:
    from separator import capabilities
    from format_guard import SUPPORTED_EXTS, find_ffmpeg

    caps = capabilities()
    lines = [
        "═══ 扒谱助手 · 环境能力 ═══",
        "",
        f"  音源分离 Demucs : {'可用' if caps['demucs'] else '不可用（将降级为中频分离）'}",
        f"  GPU 加速 CUDA   : {'可用' if caps['cuda'] else '不可用（分离走 CPU，较慢）'}",
        f"  ffmpeg         : {'可用' if caps['ffmpeg'] else '缺失（部分音频格式无法读取）'}",
        f"  分离设备       : {caps['device']}",
        "",
        "  支持格式 : " + " ".join(SUPPORTED_EXTS),
        "",
    ]
    if caps["notes"]:
        lines.append("  提示：")
        for n in caps["notes"]:
            lines.append(f"    · {n}")
        lines.append("")
    lines.append("═══")
    sys.stdout.write("\n".join(lines) + "\n")


def _do_transcribe(args: argparse.Namespace) -> tuple[int, dict[str, Any] | None]:
    """执行扒谱，返回 (退出码, 结果 dict)。"""
    from pipeline import TranscribeError, TranscribeRequest, transcribe

    inputs = [args.input] + list(args.extra)
    for path in inputs:
        if not os.path.exists(path):
            _print_err(f"文件不存在：{path}")
            return EXIT_USAGE, None

    out = _resolve_output(args)
    if out is None:
        # 踩坑实录：初版写 `return EXIT_USAGE`（只返回 int），调用方
        # `code, data = _do_transcribe(args)` 直接抛
        # `TypeError: cannot unpack non-iterable int object`——
        # 护栏本身生效了，却被自己的类型错误盖住，错误信息误导。
        return EXIT_USAGE, None

    req = TranscribeRequest(
        mode=args.mode,
        input_path=os.path.abspath(args.input),
        output_path=os.path.abspath(out),
        extra_inputs=[os.path.abspath(e) for e in args.extra],
        n_peaks=args.n_peaks,
        hop_length=args.hop,
        onset_threshold=args.onset,
        pitch_threshold=args.pitch,
        min_note_duration=args.min_note,
        tempo=args.tempo,
        perceptual=args.perceptual,
        simplify=args.simplify,
        piano_mode=args.piano,
        demucs_model=args.model,
        device=args.device,
        allow_hpss_fallback=not args.no_fallback,
    )

    labels = _mode_labels()

    def on_progress(stage: str, pct: float, msg: str) -> None:
        if not args.quiet and not args.json:
            bar = "█" * int(pct * 24) + "░" * (24 - int(pct * 24))
            sys.stderr.write(f"\r  [{bar}] {pct * 100:5.1f}%  {msg[:44]:<44}")
            sys.stderr.flush()

    try:
        res = transcribe(req, progress=on_progress)
    except TranscribeError as e:
        if not args.quiet and not args.json:
            sys.stderr.write("\r" + " " * 78 + "\r")
        msg = e.user_message
        _print_err(msg if "环境" in msg or "ffmpeg" in msg else msg)
        code = EXIT_ENV if ("ffmpeg" in msg or "未安装 Demucs" in msg) else EXIT_FAIL
        return code, None
    except KeyboardInterrupt:
        sys.stderr.write("\n")
        _print_err("已被用户中断。")
        return EXIT_FAIL, None

    if not args.quiet and not args.json:
        sys.stderr.write("\r" + " " * 78 + "\r")

    data = {
        "ok": True,
        "mode": args.mode,
        "modeLabel": labels.get(args.mode, args.mode),
        "output": res.output_path,
        "totalNotes": res.total_notes,
        "duration": round(res.duration, 2),
        "elapsed": round(res.elapsed, 2),
        "separationMethod": res.separation_method,
        "tracks": res.tracks,
        "warnings": res.warnings,
    }
    return EXIT_OK, data


def _print_result(data: dict[str, Any]) -> None:
    lines = [
        "",
        "═══ 扒谱完成 ═══",
        "",
        f"  模式   : {data['modeLabel']}",
        f"  输出   : {data['output']}",
        f"  音符   : {data['totalNotes']} 个",
        f"  音轨   : {len(data['tracks'])} 条",
        f"  耗时   : {data['elapsed']} 秒",
    ]
    if data.get("separationMethod"):
        lines.append(f"  分离   : {data['separationMethod']}")
    lines.append("")
    lines.append("  轨道明细：")
    for t in data["tracks"]:
        from midi_post import TRACK_DISPLAY_NAMES

        cn = TRACK_DISPLAY_NAMES.get(t["name"], t["name"])
        lines.append(
            f"    · {cn:<10s} ({t['name']:<14s}) program={t['program']:<3d} "
            f"{t['notes']:>5d} 音符  {t['duration']:>6.2f}s"
        )
    if data.get("warnings"):
        lines.append("")
        lines.append("  提醒：")
        for w in data["warnings"]:
            lines.append(f"    · {w}")
    lines += ["", "═══", ""]
    sys.stdout.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.caps:
        try:
            _print_caps()
        except Exception as e:  # noqa: BLE001
            _print_err(f"环境自检失败：{type(e).__name__}: {e}")
            return EXIT_ENV
        return EXIT_OK

    if not args.input:
        parser.print_help()
        return EXIT_USAGE

    # pre_separated 模式要求至少一条 extra（否则只有人声轨）
    if args.mode == "pre_separated" and not args.extra:
        _print_err(
            "「已分离音频直入」模式需要用 --extra 传入已分离好的伴奏文件。\n"
            "例：bapu 人声.wav -m pre_separated --extra 伴奏.wav\n"
            "若你想让工具自动分离，请改用 -m full_auto / vocals / accompaniment"
        )
        return EXIT_USAGE

    # 需要分离的模式且无 demucs
    if args.mode in {"full_auto", "accompaniment", "vocals"} and not args.no_fallback:
        try:
            from separator import has_demucs

            if not has_demucs() and "--no-fallback" not in sys.argv:
                if not args.json:
                    _print_err(
                        "提示：未检测到 Demucs，将使用中频分离降级（人声/伴奏分得不够干净）。\n"
                        "如需正常质量：pip install demucs"
                    )
        except Exception:  # noqa: BLE001
            pass

    code, data = _do_transcribe(args)
    if data is None:
        return code

    if args.json:
        sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    else:
        _print_result(data)
    return code


if __name__ == "__main__":
    sys.exit(main())
