"""
probe_demucs_model_source — demucs 模型下载来源治理的实测探针

## 要回答的问题
线上（主上老公的机器）首次分离时报：
    '[WinError 10060] 由于连接方在一段时间后没有正确答复…' thrown while
    requesting HEAD https://huggingface.co/adefossez/HTDemucs/resolve/main/htdemucs.yaml
    Retrying in 1s [Retry 1/5].
一直重试到 5/5 后结束。而本机开发环境**从不复现**。

本探针回答两件事：
  1. 「不复现」是不是因为本机 HF 缓存命中，网络路径从未被走到？（S1 复现现场）
  2. 修复后的三跳加载器是否真的先走官方直链、完全绕开 HF？（S2 / S3）

## 场景（各自独立子进程 —— env 必须在 import huggingface_hub 之前设好）
    S1  旧调用方式 `demucs.pretrained.get_model('htdemucs')`
        → 预期：发出 huggingface.co 请求、重试，耗时明显偏长
    S2  新加载器 `separator._load_separator_model('htdemucs')`
        → 预期：**零** huggingface.co 请求，直接落到 dl.fbaipublicfiles.com
    S3  本地权重目录（`<engine>/models/*.th`）
        → 预期：零网络、秒级返回

## 隔离手法（关键）
    HF_HOME=<tmp>     把 HF 缓存挪到临时目录 → 等效于「一台从没下过模型的机器」
    TORCH_HOME=<tmp>  把 torch hub 缓存挪走 → 迫使真实下载，而不是吃旧缓存
这样**不需要动用户真实缓存**，也不会读它、污染它 —— 本机之所以不复现，
恰恰是因为真实缓存里有 `models--adefossez--HTDemucs`。

## 用法
    <engine>/.venv/Scripts/python.exe engine/tests/manual/probe_demucs_model_source.py

父进程会自动定位 `.venv` 的 python 来跑子进程，因此用任意解释器启动都可以。
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[2]
PROBE_ROOT = Path(tempfile.gettempdir()) / "probe_demucs_model_source"

HF_HOST = "huggingface.co"
OFFICIAL_HOST = "dl.fbaipublicfiles.com"
TH_NAME = "955717e8-8726e21a.th"

# 缩短 HF 单次超时，使 S1 的「5 次重试」在可接受的墙钟时间内跑完
# （线上默认 10s，5 次 + 退避要几分钟）。
_FAST_TIMEOUT_ENV = {
    "HF_HUB_ETAG_TIMEOUT": "3",
    "HF_HUB_DOWNLOAD_TIMEOUT": "3",
    "HF_HUB_DISABLE_TELEMETRY": "1",
}


def _venv_python() -> str:
    """优先用引擎 venv 的解释器（只有它装了 demucs/torch）。"""
    p = ENGINE / ".venv" / "Scripts" / "python.exe"
    return str(p) if p.is_file() else sys.executable


# ── 子进程：真正加载模型 ──────────────────────────────────────────────────

def _child(scenario: str) -> int:
    sys.path.insert(0, str(ENGINE))

    import separator  # noqa: E402 — 必须在 sys.path 装配后

    started = time.time()

    def say(msg: str) -> None:
        print(msg, flush=True)

    # 捕获 huggingface_hub 发出的请求日志（`http_backoff` 的 warning 级）
    hits: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                msg = record.getMessage()
            except Exception:  # noqa: BLE001
                return
            if HF_HOST in msg:
                hits.append(msg)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger().addHandler(_Capture())

    def on_progress(stage: str, pct: float, text: str) -> None:
        say(f"  progress[{stage} {pct:.2f}] {text}")

    if scenario == "s3":
        override = Path(os.environ["PROBE_MODELS_DIR"])
        separator.models_dir = lambda: override  # type: ignore[assignment]
        say(f"models_dir(override) = {override}")
        say(f"  目录内 .th：{sorted(p.name for p in override.glob('*.th'))}")
    else:
        say(f"models_dir = {separator.models_dir()}")

    ok = False
    err = ""
    try:
        if scenario == "s1":
            # 旧调用方式：demucs 原生入口，会先试 HuggingFace Hub
            from demucs.pretrained import get_model

            net = get_model("htdemucs")
        else:
            net = separator._load_separator_model("htdemucs", on_progress)
        ok = net is not None
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {str(e)[:400]}"

    elapsed = time.time() - started
    say(f"RESULT ok={ok} elapsed={elapsed:.1f}s hf_requests={len(hits)}")
    for msg in hits[:2]:
        say(f"  hf_log: {msg[:200]}")
    if err:
        say(f"  error: {err}")
    return 0


# ── 父进程：编排三个场景并给判定 ──────────────────────────────────────────

def _run(scenario: str, env_extra: dict[str, str], timeout: int) -> str:
    env = dict(os.environ)
    env.update(_FAST_TIMEOUT_ENV)
    env.update(env_extra)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.run(
            [_venv_python(), str(Path(__file__).resolve()), "--child", scenario],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(ENGINE),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"RESULT ok=False elapsed=?.?s hf_requests=? (TIMEOUT after {timeout}s)"
    out = proc.stdout or ""
    if proc.returncode != 0 and "RESULT" not in out:
        out += f"\n[stderr]\n{(proc.stderr or '')[:800]}"
    return out


def _parse(out: str) -> dict:
    info = {"ok": False, "elapsed": 0.0, "hf": -1}
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("RESULT "):
            continue
        for token in line[len("RESULT "):].split():
            if token.startswith("ok="):
                info["ok"] = token[3:] == "True"
            elif token.startswith("elapsed="):
                try:
                    info["elapsed"] = float(token[8:].rstrip("s"))
                except ValueError:
                    pass
            elif token.startswith("hf_requests="):
                try:
                    info["hf"] = int(token[12:])
                except ValueError:
                    pass
    return info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--child", help="内部使用：以指定场景在当前进程内执行")
    ap.add_argument("--only", help="只跑指定场景（逗号分隔，如 s2,s3）；默认全跑")
    args = ap.parse_args()

    if args.child:
        return _child(args.child)

    only = {x.strip().lower() for x in (args.only or "").split(",") if x.strip()}

    def want(scenario: str) -> bool:
        return not only or scenario in only

    if PROBE_ROOT.exists():
        shutil.rmtree(PROBE_ROOT, ignore_errors=True)
    for name in ("s1", "s2", "s3"):
        (PROBE_ROOT / name).mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print("探针：demucs 模型下载来源治理")
    print(f"  探针根目录 : {PROBE_ROOT}")
    print(f"  引擎 venv  : {_venv_python()}")
    print(f"  真实 HF 缓存目录（本探针不使用）: {Path.home() / '.cache' / 'huggingface'}")
    print("=" * 78)

    # ── S1：复现「先撞 HF」 ──
    r1 = {"ok": False, "elapsed": 0.0, "hf": -1}
    if want("s1"):
        print("\n[S1] 旧调用方式 —— 复现线上现象（约 3 分钟，仅根因诊断时需要）")
        print("-" * 78)
        out1 = _run("s1", {
            "HF_HOME": str(PROBE_ROOT / "s1" / "hf"),
            "TORCH_HOME": str(PROBE_ROOT / "s1" / "torch"),
        }, timeout=180)
        print(out1)
        r1 = _parse(out1)

    # ── S2：验证修复后的三跳加载器 ──
    r2 = {"ok": False, "elapsed": 0.0, "hf": -1}
    if want("s2"):
        print("\n[S2] 新加载器 —— 验证探活 + 官方直链、绕开 HF")
        print("-" * 78)
        out2 = _run("s2", {
            "HF_HOME": str(PROBE_ROOT / "s2" / "hf"),
            "TORCH_HOME": str(PROBE_ROOT / "s2" / "torch"),
        }, timeout=300)
        print(out2)
        r2 = _parse(out2)

    # ── S3：本地权重目录（离线）──
    r3 = {"ok": False, "elapsed": 0.0, "hf": -1}
    if want("s3"):
        print("\n[S3] 本地权重目录 —— 验证完全离线")
        print("-" * 78)
        local_models = PROBE_ROOT / "s3" / "models"
        local_models.mkdir(parents=True, exist_ok=True)
        src_th = None
        for cand in (
            PROBE_ROOT / "s2" / "torch" / "hub" / "checkpoints" / TH_NAME,
            PROBE_ROOT / "s1" / "torch" / "hub" / "checkpoints" / TH_NAME,
        ):
            if cand.is_file():
                src_th = cand
                break
        if src_th is None:
            print("  (S1/S2 都没能拿到 .th，S3 跳过 —— 先看 S1/S2 的失败原因)")
        else:
            shutil.copyfile(src_th, local_models / TH_NAME)
            print(f"  已把权重放入本地目录：{local_models / TH_NAME} "
                  f"({(src_th.stat().st_size / 1048576):.1f} MB)")
            out3 = _run("s3", {
                "HF_HOME": str(PROBE_ROOT / "s3" / "hf"),
                "TORCH_HOME": str(PROBE_ROOT / "s3" / "torch"),
                "PROBE_MODELS_DIR": str(local_models),
            }, timeout=120)
            print(out3)
            r3 = _parse(out3)

    # ── 判定 ──
    print("\n" + "=" * 78)
    print("判定")
    print("=" * 78)
    rows = [
        ("s1", "S1 旧方式（复现）", r1, "应发出 huggingface.co 请求并重试"),
        ("s2", "S2 新加载器（修复）", r2, "应 hf_requests=0 且加载成功"),
        ("s3", "S3 本地目录（离线）", r3, "应 hf_requests=0、秒级成功"),
    ]
    for key, name, r, expect in rows:
        if not want(key):
            continue
        print(f"  {name:22s} ok={str(r['ok']):5s} elapsed={r['elapsed']:6.1f}s "
              f"hf_requests={r['hf']:2d}   ({expect})")

    verdicts = []
    if want("s1"):
        verdicts.append(("S1 复现线上现象", r1["hf"] >= 1))
    if want("s2"):
        verdicts.append(("S2 绕开 HF 且成功", r2["hf"] == 0 and r2["ok"]))
    if want("s3"):
        verdicts.append(("S3 本地离线可用", r3["ok"] and r3["hf"] == 0 and r3["elapsed"] < 10))

    print()
    all_pass = True
    for label, passed in verdicts:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        all_pass = all_pass and passed
    print()
    print("结论：" + ("三项全部通过 —— 根因确认且修复生效。" if all_pass
                    else "存在未通过项，逐条看上面子进程输出。"))
    print(f"探针产物保留在 {PROBE_ROOT}（可手动删除）")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
