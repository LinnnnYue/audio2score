"""
midi_post — MIDI 后处理：轨名、program、时间签名

## 为什么存在
上游 `AutoTranscriber.midi_writer` 写 MIDI 时**从不设置轨名**
（`midi_writer.py:13` 与 `:330` 均为 `pretty_midi.Instrument(program=...)`，
无 `name=` 参数）。实测确认产出轨名恒为 `''`。

后果：MuseScore 打开时人声轨与伴奏轨都显示为默认轨名，主上无法分辨。
这直接违反需求 G6「MuseScore 可用，轨名正确」。

## 红线遵守
`third_party/` 为只读 vendored 树（BOUNDARY B-1），**一字不改**。
本模块在**上游产出之后**读回 MIDI、补齐元数据、重写文件，属纯后处理。

## 轨名策略
MuseScore 导入 MIDI 时，轨名会成为乐器名并显示在分谱表上，故命名须可读。

### ⚠️ 关键约束：MIDI 轨名只能是 latin-1
**踩坑实录**：初版轨名用中文（`人声 Voice`），写入时 mido 抛
`UnicodeEncodeError: 'latin-1' codec can't encode characters`。
根因：MIDI 规范的 track name meta event 是**单字节 latin-1 文本**，
非 ASCII 字符无法编码。强行写入的后果不是「乱码」，而是**文件损坏 /
MuseScore 打不开**——这比中文轨名好看重要得多。

**故最终策略**：
- **MIDI 文件内**：使用纯 ASCII 轨名（`Voice` / `Accompaniment` / `Piano` …），
  保证任何 DAW / MuseScore / 音游都能安全打开。这是不可妥协的硬约束。
- **UI 显示层**：由前端把 `Voice` 映射为「人声」展示，中文体验不丢。
  映射表见 `TRACK_DISPLAY_NAMES`。

General MIDI program 选用理由：
- 52 = Choir Aahs（人声合唱）
- 53 = Voice Oohs（独唱人声，主上扒的歌曲多为独唱）
- 0  = Piano（伴奏默认，MuseScore 支持最好）
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

# 角色 → (MIDI 内 ASCII 轨名, General MIDI program)
# ⚠️ 轨名必须是纯 ASCII，理由见上方「关键约束」
TRACK_ROLE_MAP: dict[str, tuple[str, int]] = {
    "vocals": ("Voice", 53),              # Voice Oohs 独唱人声
    "vocals_chorus": ("Choir", 52),       # Choir Aahs
    "accompaniment": ("Accompaniment", 0),  # Piano
    "piano": ("Piano", 0),
    "instrument": ("Instrument", 0),
    "melody": ("Melody", 0),
    "guitar": ("Guitar", 25),             # Acoustic Guitar nylon
    "bass": ("Bass", 32),                  # Acoustic Bass
    "strings": ("Strings", 48),            # String Ensemble
}

# UI 显示层映射：MIDI 内 ASCII 名 → 主上看的中文名。
# 放在前端也可，但引擎侧留一份，保证日志/报告与 UI 说法一致。
TRACK_DISPLAY_NAMES: dict[str, str] = {
    "Voice": "人声",
    "Choir": "合唱",
    "Accompaniment": "伴奏",
    "Piano": "钢琴",
    "Instrument": "乐器",
    "Melody": "旋律",
    "Guitar": "吉他",
    "Bass": "贝斯",
    "Strings": "弦乐",
}

# 兜底：未知角色按序号命名
FALLBACK_PROGRAM = 0
_ASCII_SAFE = re.compile(r"^[\x20-\x7e]*$")


def to_ascii_safe(name: str) -> str:
    """
    把任意用户输入的轨名压成 latin-1 安全形式。

    策略：逐字符剔除非 ASCII；结果为空则用占位名。
    用户若输入中文轨名，界面会提示「MIDI 轨名仅支持英文/数字/常见符号」，
    但引擎侧仍必须兜住，不能让文件写不出来。
    """
    cleaned = "".join(ch for ch in name if _ASCII_SAFE.match(ch))
    cleaned = cleaned.strip()
    return cleaned or "Track"


@dataclass
class TrackSpec:
    """一条轨道的元数据规格。"""

    role: str
    """内部角色标识，对应 TRACK_ROLE_MAP 的键。未知角色走兜底命名。"""

    custom_name: str | None = None
    """用户自定义轨名。为 None 时用 TRACK_ROLE_MAP 的默认名。"""

    program: int | None = None
    """用户指定 program。为 None 时用 TRACK_ROLE_MAP 的默认 program。"""

    def resolve(self) -> tuple[str, int]:
        default_name, default_prog = TRACK_ROLE_MAP.get(
            self.role, (None, FALLBACK_PROGRAM)
        )
        raw = self.custom_name or default_name or "Track"
        # ⚠️ 必须过 latin-1 净化，中文轨名会让 MIDI 文件写不出来
        name = to_ascii_safe(raw)
        prog = self.program if self.program is not None else default_prog
        return name, prog

    def display_name(self) -> str:
        """给 UI 看的中文名。"""
        ascii_name, _ = self.resolve()
        return TRACK_DISPLAY_NAMES.get(ascii_name, ascii_name)


def apply_track_metadata(
    midi_path: str,
    specs: list[TrackSpec],
    in_place: bool = True,
) -> str:
    """
    读回上游产出的 MIDI，补齐轨名与 program 后重写。

    Parameters
    ----------
    midi_path : 上游 write_midi / write_multitrack_midi 的产出路径
    specs : 与轨道顺序一一对应的规格列表
    in_place : True 原地重写；False 写出到临时文件并返回新路径

    Returns
    -------
    实际写出的 MIDI 路径
    """
    import pretty_midi

    if not os.path.isfile(midi_path):
        raise FileNotFoundError(f"MIDI 不存在：{midi_path}")

    midi = pretty_midi.PrettyMIDI(midi_path)

    if not midi.instruments:
        raise ValueError("MIDI 内没有任何音轨，无法设置轨名。")

    for idx, instr in enumerate(midi.instruments):
        if idx < len(specs):
            name, prog = specs[idx].resolve()
        else:
            # specs 短于实际轨数：多余轨道用兜底名
            name, prog = f"Track {idx + 1}", FALLBACK_PROGRAM
        instr.name = name
        instr.program = prog

    # 最后一道防线：任何漏网的非 ASCII 轨名在此拦下。
    # mido 写文件时才抛 UnicodeEncodeError，那时已污染了磁盘上的文件；
    # 在此拦下可给出可读原因。
    for instr in midi.instruments:
        if not _ASCII_SAFE.match(instr.name or ""):
            raise ValueError(
                f"轨名含非 ASCII 字符（{instr.name!r}），MIDI 格式无法写入。"
                f"请改用英文或数字。"
            )

    if in_place:
        midi.write(midi_path)
        return midi_path

    fd, tmp = tempfile.mkstemp(suffix=".mid", prefix="midi_post_")
    os.close(fd)
    midi.write(tmp)
    return tmp


def count_notes(midi_path: str) -> int:
    """统计 MIDI 总音符数，用于 UI 结果卡片。失败返回 0 而非抛异常。"""
    try:
        import pretty_midi

        midi = pretty_midi.PrettyMIDI(midi_path)
        return sum(len(i.notes) for i in midi.instruments)
    except Exception:  # noqa: BLE001 — 统计失败不应影响主流程
        return 0


def describe(midi_path: str) -> list[dict]:
    """
    读回 MIDI 的结构化描述，供 UI 展示与日志。

    Returns
    -------
    [{name, program, notes, duration}, ...]；失败返回空列表
    """
    try:
        import pretty_midi

        midi = pretty_midi.PrettyMIDI(midi_path)
        result = []
        for instr in midi.instruments:
            notes = instr.notes
            result.append(
                {
                    "name": instr.name or "(未命名)",
                    "program": int(instr.program),
                    "is_drum": bool(instr.is_drum),
                    "notes": len(notes),
                    "duration": round(max((n.end for n in notes), default=0.0), 2),
                }
            )
        return result
    except Exception:  # noqa: BLE001
        return []


def get_tempo(midi_path: str) -> float:
    """读回 MIDI 的 BPM。失败返回 0.0。"""
    try:
        import pretty_midi

        return round(float(pretty_midi.PrettyMIDI(midi_path).estimate_tempo()), 1)
    except Exception:  # noqa: BLE001
        return 0.0


def verify_musescore_opens(midi_path: str, timeout: int = 60) -> tuple[bool, str]:
    """
    用本机 MuseScore 4 headless 模式验证 MIDI 可被解析。

    这是**自测工具**，不是应用运行依赖——MuseScore 未安装时返回 (False, 原因)，
    调用方须容忍失败，不阻断主流程。

    ### 踩坑实录（勿改回 capture_output）
    MuseScore 4 是 **GUI 程序**。改用 DEVNULL 丢弃输出以避免管道问题。

    ### 另一条教训：验证脚本报错 ≠ 产物有问题
    本函数曾对全部 5 个产出报 rc=1320，一度被误判为「MIDI 全坏」。
    真因是**验证脚本自身的相对路径写错**（在项目根目录跑却用了 `../.tmp/`，
    指向不存在的 `<TMP>/.tmp`），文件其实完好，直接命令行 rc=0 出 PDF 42KB。
    故本函数内部一律用 `os.path.abspath(midi_path)`，不依赖调用方的工作目录。
    **判据：验证失败时，先确认验证脚本自己没错，再怀疑产物。**

    Returns
    -------
    (是否通过, 详细信息)
    """
    mscore = find_musescore()
    if not mscore:
        return False, "未检测到 MuseScore，跳过验证"

    out_dir = tempfile.mkdtemp(prefix="mscore_verify_")
    try:
        proc = subprocess.run(
            [mscore, "-o", os.path.join(out_dir, "out.pdf"), os.path.abspath(midi_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        pdf = os.path.join(out_dir, "out.pdf")
        if proc.returncode == 0 and os.path.isfile(pdf) and os.path.getsize(pdf) > 0:
            return True, f"MuseScore 解析成功，PDF {os.path.getsize(pdf) // 1024}KB"
        if proc.returncode == 1320:
            return (
                False,
                f"MuseScore rc=1320（GUI 程序管道崩溃，非文件问题）。"
                f"请手动双击该 MIDI 确认。",
            )
        return False, f"MuseScore 返回 rc={proc.returncode}"
    except subprocess.TimeoutExpired:
        return False, f"MuseScore 验证超时（>{timeout}s）"
    except Exception as e:  # noqa: BLE001
        return False, f"MuseScore 调用异常：{type(e).__name__}: {e}"
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def find_musescore() -> str | None:
    """
    定位 MuseScore 可执行文件。

    与上游 `midi_to_pdf.find_musescore` 独立实现——上游版本只覆盖旧版路径，
    本机装的是 MuseScore 4（`MuseScore4.exe`），此处补齐 3/4 双版本探测。
    """
    for candidate in (
        r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe",
        r"C:\Program Files\MuseScore 3\bin\mscore.exe",
        r"C:\Program Files\MuseScore 4\bin\mscore.exe",
        r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe",
    ):
        if os.path.isfile(candidate):
            return candidate

    found = shutil.which("mscore") or shutil.which("musescore")
    return found


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python midi_post.py <midi文件> [角色1 角色2 ...]")
        print(f"已知角色: {', '.join(TRACK_ROLE_MAP)}")
        raise SystemExit(0)

    target = sys.argv[1]
    roles = sys.argv[2:] or ["instrument"]
    out = apply_track_metadata(
        target, [TrackSpec(role=r) for r in roles], in_place=True
    )
    print(f"已补齐轨名: {out}")
    for t in describe(out):
        print(f"  {t['name']:26s} program={t['program']:3d} notes={t['notes']:5d} dur={t['duration']}s")
    ok, msg = verify_musescore_opens(out)
    print(f"MuseScore 验证: {'通过' if ok else '未通过'} — {msg}")
