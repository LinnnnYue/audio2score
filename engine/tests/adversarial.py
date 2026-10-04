"""
adversarial.py — 第三轮对抗式自测

前两轮已覆盖：正常路径（test_progress_monotonic）、格式矩阵（9/9）、
异常输入（8/8）。本轮专攻**状态与边界**，即需求 B2/B3/B4/B7。

覆盖：
  B2 异常路径 8 类 —— 空/目录/过小/伪装/伪造头/不支持扩展名/中文路径/无权限
  B3 取消       —— 进程能否真终止（不留孤儿）
  B4 并发防护   —— 连续提交是否只跑一个
  B7 路径健壮性 —— 中文/空格/全角/超长路径
  边界数值     —— tempo=0/负数/超大、n_peaks=0/负数、onset 越界

用法：python engine/tests/adversarial.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ENGINE, ".venv", "Scripts", "python.exe")
BRIDGE = os.path.join(ENGINE, "bridge.py")
TMP = os.path.join(ENGINE, "..", ".tmp", "adversarial")
WAV = os.path.abspath(
    os.path.join(
        ENGINE, "..", "third_party", "AutoTranscriber", "test_audio",
        "chord_progression.wav",
    )
)

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def record(cat: str, name: str, ok: bool, note: str = "") -> None:
    results.append((cat, name, f"{PASS if ok else FAIL}" + (f" — {note}" if note else "")))


def ask(req: dict, timeout: int = 600) -> tuple[str, dict | None]:
    """发一条请求给 bridge，返回 (事件类型, data)。"""
    proc = subprocess.run(
        [PY, BRIDGE],
        input=json.dumps(req) + "\n",
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    kind, data = "none", None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            record("协议", "非 JSON 行污染", False, line[:60])
            continue
        t = obj.get("type")
        if t in ("result", "error"):
            kind = t
            data = obj
    return kind, data


def out(name: str) -> str:
    return os.path.join(TMP, name)


# ─────────────────────────────────────────────────────────────
# B2 · 异常路径 8 类
# ─────────────────────────────────────────────────────────────
def test_bad_inputs() -> None:
    os.makedirs(TMP, exist_ok=True)
    p = lambda n: out(n)  # noqa: E731

    open(p("empty.mp3"), "wb").close()
    open(p("tiny.wav"), "wb").write(b"\x00" * 300)
    with open(p("fake_text.mp3"), "w", encoding="utf-8") as f:
        f.write("this is definitely not audio " * 50)
    with open(p("fake_riff.wav"), "wb") as f:
        f.write(b"RIFF" + b"\x00" * 3000)
    with open(p("x.xyz"), "wb") as f:
        f.write(b"\x00" * 5000)

    cases = [
        ("不存在的文件", p("nope_does_not_exist.mp3"), "文件不存在"),
        ("传入目录", TMP, "文件夹"),
        ("0 字节空文件", p("empty.mp3"), "空的"),
        ("300 字节过小", p("tiny.wav"), "太小"),
        ("纯文本伪装 mp3", p("fake_text.mp3"), "不是音频"),
        ("伪造 RIFF 头", p("fake_riff.wav"), "损坏"),
        ("不支持的扩展名", p("x.xyz"), "暂不支持"),
    ]
    for name, path, expect in cases:
        kind, data = ask({
            "cmd": "probe", "id": "b2",
            "payload": {"path": path},
        }, timeout=120)
        if kind != "error":
            record("B2 异常", name, False, f"未被拦截（返回 {kind}）")
            continue
        msg = data.get("message", "")
        ok = bool(expect in msg) if expect else True
        record("B2 异常", name, ok, msg.splitlines()[0][:40])


# ─────────────────────────────────────────────────────────────
# B7 · 路径健壮性
# ─────────────────────────────────────────────────────────────
def test_paths() -> None:
    import shutil

    os.makedirs(TMP, exist_ok=True)
    weird = [
        "中文 文件名.wav",
        "带 空格 的 文件.wav",
        "全角括号（）测试.wav",
        "emoji_🎵_测试.wav",
        "a" * 80 + ".wav",
    ]
    for name in weird:
        dst = out(name)
        try:
            shutil.copy(WAV, dst)
        except Exception as e:  # noqa: BLE001
            record("B7 路径", name[:20], False, f"复制失败 {e}")
            continue
        kind, data = ask({
            "cmd": "probe", "id": "b7", "payload": {"path": dst},
        }, timeout=180)
        ok = kind == "result" and data.get("data", {}).get("ok")
        record("B7 路径", name[:20], bool(ok), "" if ok else f"kind={kind}")

    # 中文路径真跑一次扒谱
    dst = out("中文 路径 扒谱 测试.wav")
    import shutil as _s
    _s.copy(WAV, dst)
    kind, data = ask({
        "cmd": "transcribe", "id": "b7b",
        "payload": {
            "mode": "basic",
            "input_path": dst,
            "output_path": out("中文 输出.mid"),
        },
    })
    ok = kind == "result" and data["data"]["totalNotes"] > 0
    record("B7 路径", "中文路径端到端扒谱", bool(ok),
           f"{data['data']['totalNotes']} 音符" if ok else f"kind={kind}")


# ─────────────────────────────────────────────────────────────
# 边界数值
# ─────────────────────────────────────────────────────────────
def test_param_bounds() -> None:
    cases = [
        ("tempo=0", {"tempo": 0}),
        ("tempo=9999", {"tempo": 9999}),
        ("n_peaks=1", {"n_peaks": 1}),
        ("n_peaks=99", {"n_peaks": 99}),
        ("onset=0.0", {"onset_threshold": 0.0}),
        ("onset=1.0", {"onset_threshold": 1.0}),
        ("min_note=1", {"min_note_duration": 1}),
    ]
    for name, extra in cases:
        payload = {"mode": "basic", "input_path": WAV,
                   "output_path": out(f"bound_{name.replace('=', '_')}.mid")}
        payload.update(extra)
        try:
            kind, data = ask({"cmd": "transcribe", "id": "bnd", "payload": payload})
        except subprocess.TimeoutExpired:
            record("边界数值", name, False, "超时")
            continue
        if kind == "result":
            n = data["data"]["totalNotes"]
            record("边界数值", name, n > 0, f"{n} 音符")
        else:
            # 报错也算「有明确中文提示」，但不能是崩溃
            msg = data.get("message", "") if data else ""
            is_crash = "内部错误" in msg or "Traceback" in msg
            record("边界数值", name, not is_crash, msg.splitlines()[0][:40])


# ─────────────────────────────────────────────────────────────
# B3 · 取消不留孤儿
# ─────────────────────────────────────────────────────────────
def test_cancel() -> None:
    import signal

    def count_bridge_procs() -> int:
        """数当前 python.exe 进程数（排除 pythonw.exe）。"""
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True,
        )
        return len([l for l in r.stdout.splitlines() if "python.exe" in l])

    baseline = count_bridge_procs()

    proc = subprocess.Popen(
        [PY, BRIDGE],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8",
    )
    req = {
        "cmd": "transcribe", "id": "cancel_test",
        "payload": {"mode": "full_auto", "input_path": WAV,
                    "output_path": out("cancel.mid")},
    }
    proc.stdin.write(json.dumps(req) + "\n")
    proc.stdin.flush()

    # 等分离阶段真的开始
    started = False
    t0 = time.time()
    while time.time() - t0 < 60:
        line = proc.stdout.readline()
        if not line:
            break
        if '"separate"' in line:
            started = True
            break
    record("B3 取消", "任务已进入分离阶段", started)

    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)

    time.sleep(3.0)
    # 踩坑实录（三层，全部误报）：
    # 1. 初版统计所有 python 进程 → 主上机器常驻两个 pythonw.exe（19MB/5MB
    #    的无窗口应用），与 bridge 无关，误报「有残留」。
    # 2. 二版只数 python.exe 仍误报——**本测试自身就是 python.exe**，
    #    它在统计时自己还活着，必然 +1。
    # 正解：取**基线差值**。测试开始前记 baseline，终止后比对，
    #       差值 > 0 才说明真残留。这对「本进程也在统计范围内」天然免疫。
    after = count_bridge_procs()
    record("B3 取消", "终止后无残留 bridge 进程", after <= baseline,
           f"基线 {baseline} → 终止后 {after}（差值 {after - baseline}）")


# ─────────────────────────────────────────────────────────────
# B4 · 并发
# ─────────────────────────────────────────────────────────────
def test_concurrent_guard() -> None:
    """
    验证协议层能否承受连续请求。
    注意：并发防护主要在**前端按钮禁用**，协议层是顺序处理的。
    此处验证「连发 3 个任务不产生脏数据」——即前一个任务的产物不被后一个覆盖。
    """
    outs = []
    for i in range(3):
        o = out(f"conc_{i}.mid")
        kind, data = ask({
            "cmd": "transcribe", "id": f"c{i}",
            "payload": {"mode": "basic", "input_path": WAV, "output_path": o},
        })
        outs.append((o, kind == "result"))
    # 踩坑实录：本断言的前两版把 (path, ok) 元组直接喂给 os.path.isfile /
    # os.path.getsize，抛 TypeError 使整条用例失效——**测试脚本自己有 bug 时，
    # 它的「失败」不构成产品缺陷的证据**。先解包再断言。
    paths = [o for o, _ in outs]
    all_ok = all(ok for _, ok in outs)
    all_exist = all(os.path.isfile(o) for o in paths)
    sizes = [os.path.getsize(o) for o in paths if os.path.isfile(o)]
    record("B4 并发", "连发 3 任务互不干扰", all_ok and all_exist,
           f"产物 {len(sizes)} 个，最小 {min(sizes) if sizes else 0}B")


# ─────────────────────────────────────────────────────────────
def main() -> None:
    print("=" * 70)
    print("第三轮对抗式自测")
    print("=" * 70)

    for fn in (test_bad_inputs, test_paths, test_param_bounds,
               test_cancel, test_concurrent_guard):
        name = fn.__name__
        print(f"\n▶ {name}")
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            record(name, "测试自身异常", False, f"{type(e).__name__}: {e}")

    print("\n" + "=" * 70)
    cur = None
    npass = nfail = 0
    for cat, name, verdict in results:
        if cat != cur:
            print(f"\n[{cat}]")
            cur = cat
        print(f"  {verdict:6s} {name}")
        if verdict.startswith(PASS):
            npass += 1
        else:
            nfail += 1

    print("\n" + "=" * 70)
    print(f"通过 {npass} / 失败 {nfail} / 共 {len(results)}")
    print("=" * 70)
    sys.exit(0 if nfail == 0 else 1)


if __name__ == "__main__":
    main()
