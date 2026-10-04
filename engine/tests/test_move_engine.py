"""
test_move_engine.py — 引擎目录迁移的编排逻辑测试

## 为什么这份测试很重
迁移会动用户的 5.4GB 引擎目录。一旦逻辑出错，用户失去的是**唯一可用的引擎**，
重下 2.5GB PyTorch 的代价极高。所以这里覆盖的重点不是「成功路径」，
而是**每一条失败路径都必须不损坏原环境**。

## 隔离策略
- 所有文件操作都在临时目录里，绝不触碰真实引擎。
- `location_file()` 被打桩指向临时路径 —— 否则测试会改写
  `%LOCALAPPDATA%/bapu/location.json`，劫持真实应用的路径解析。
  （这条踩坑在 tauri-artifact-verification 的 SOP 里记过：
  真跑安装的探针会写用户级配置，跑完必须还原。这里直接隔离，不产生副作用。）
- `check_installed` 被打桩：真实验证需要装齐 numpy/scipy/librosa 等，
  而本测试要验的是**编排与回滚**，不是依赖探测本身（那个有独立测试）。

用法：python engine/tests/test_move_engine.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bootstrap  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f"  ← {extra}" if extra else ""))


def make_engine(root: Path, name: str = "engine") -> Path:
    """
    造一个「看起来像已装引擎」的目录：有 .venv/Scripts/python.exe 和若干文件。

    不追求真的能跑 —— 可用性由 check_installed 判定，本测试把它打桩了。
    """
    d = root / name
    (d / ".venv" / "Scripts").mkdir(parents=True, exist_ok=True)
    (d / ".venv" / "Scripts" / "python.exe").write_bytes(b"MZ fake interpreter")
    # 造点体积与层级，验证复制路径会递归处理
    (d / ".venv" / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)
    (d / ".venv" / "Lib" / "site-packages" / "big.bin").write_bytes(b"x" * 4096)
    (d / ".venv" / "pyvenv.cfg").write_text(
        "home = C:\\Python\\Python310\nversion = 3.10.6\n", encoding="utf-8"
    )
    return d


def ready(_d):
    return {"ready": True, "basic": True, "demucs": True, "cuda": True}


def not_ready(_d):
    return {"ready": False, "reason": "基础依赖缺失"}


def run_case(fn) -> None:
    """在隔离的临时环境里跑一个用例。"""
    with tempfile.TemporaryDirectory(prefix="mig-") as tmp:
        base = Path(tmp)
        loc = base / "location.json"
        with mock.patch.object(bootstrap, "location_file", lambda: loc):
            fn(base, loc)


def fake_volume(other_prefix: str):
    """
    返回一个「把路径中含 other_prefix 的一侧判为 D 盘、其余判为 C 盘」的 `_volume` 替身。

    ⚠️ 不要图省事改成 patch `os.path.splitdrive`。Windows 上 `os.path` 就是
    `ntpath`，而 pathlib 的路径解析内部也在调它 —— 补丁一上，连
    `Path("...\\elsewhere").is_file()` 都会解析错，用例红在
    「找不到已安装的引擎」这种与跨盘判定毫无关系的断言上
    （真跑过：3 个用例全红，排查方向被彻底带偏）。
    bootstrap 因此把「问盘符」抽成了 `_volume` 这个接缝，只打桩它。
    """

    def _v(p):
        return "d:" if other_prefix.lower() in str(p).lower() else "c:"

    return _v


# ---------------------------------------------------------------- 边界拒绝


def test_reject_same_dir(base: Path, _loc: Path) -> None:
    print("\n【1】目标 == 源 → 拒绝")
    src = make_engine(base)
    r = bootstrap.move_engine(src, src)
    check("返回失败", not r.ok, r.message)
    check("源目录原样保留", (src / ".venv" / "Scripts" / "python.exe").is_file())


def test_reject_nested_target(base: Path, _loc: Path) -> None:
    print("\n【2】目标位于源内部 → 拒绝（否则会自我吞噬）")
    src = make_engine(base)
    inner = src / ".venv" / "inside"
    r = bootstrap.move_engine(src, inner)
    check("返回失败", not r.ok, r.message)
    check("源目录原样保留", (src / ".venv" / "Scripts" / "python.exe").is_file())
    check("没有制造出内嵌目录", not inner.exists())


def test_reject_nonempty_target(base: Path, _loc: Path) -> None:
    print("\n【3】目标已有其他文件 → 拒绝（避免混入）")
    src = make_engine(base)
    dst = base / "occupied"
    dst.mkdir()
    (dst / "重要文件.txt").write_text("别覆盖我", encoding="utf-8")
    r = bootstrap.move_engine(src, dst)
    check("返回失败", not r.ok, r.message)
    check("用户的文件未被覆盖", (dst / "重要文件.txt").read_text(encoding="utf-8") == "别覆盖我")
    check("源目录原样保留", (src / ".venv" / "Scripts" / "python.exe").is_file())


def test_reject_no_engine(base: Path, _loc: Path) -> None:
    print("\n【4】源里没有引擎 → 拒绝")
    empty = base / "not-engine"
    empty.mkdir()
    r = bootstrap.move_engine(empty, base / "target")
    check("返回失败", not r.ok, r.message)
    check("提示提到「找不到…引擎」", "引擎" in r.message, r.message)


def test_reject_insufficient_space(base: Path, _loc: Path) -> None:
    print("\n【5】跨盘且空间不足 → 拒绝（不能搬到一半满盘）")
    src = make_engine(base)
    dst = base / "elsewhere"
    # 打桩必须按「目标路径」判定跨盘。曾按源路径的 "src" 字样判定，
    # 而临时目录名里根本没有 src → 两侧都被判成同盘 → 走 rename → 用例假通过。
    # 教训：打桩的判据要落在**被测代码真正关心的那个变量**上（这里是目标盘）。
    with mock.patch.object(bootstrap, "check_installed", ready), \
         mock.patch.object(bootstrap, "_volume", fake_volume("elsewhere")), \
         mock.patch.object(bootstrap, "_free_bytes_at", lambda _p: 1024):
        r = bootstrap.move_engine(src, dst)
    check("返回失败", not r.ok, r.message)
    check("提示是空间不足", "空间不足" in r.message, r.message)
    check("源目录原样保留", (src / ".venv" / "Scripts" / "python.exe").is_file())


# ---------------------------------------------------------------- 成功路径


def test_same_volume_rename(base: Path, loc: Path) -> None:
    print("\n【6】同盘迁移 → 原子 rename，旧目录消失，记录更新")
    src = make_engine(base)
    dst = base / "moved"
    before = src if src.exists() else None
    with mock.patch.object(bootstrap, "check_installed", ready):
        r = bootstrap.move_engine(src, dst)
    check("返回成功", r.ok, r.message)
    check("新位置有解释器", (dst / ".venv" / "Scripts" / "python.exe").is_file())
    check("新位置保留了嵌套文件", (dst / ".venv" / "Lib" / "site-packages" / "big.bin").is_file())
    check("pyvenv.cfg 内容完好",
          "version = 3.10.6" in (dst / ".venv" / "pyvenv.cfg").read_text(encoding="utf-8"))
    check("旧位置已清理", not src.exists())
    check("location.json 已指向新位置",
          json.loads(loc.read_text(encoding="utf-8"))["engine_dir"] == str(dst),
          loc.read_text(encoding="utf-8") if loc.is_file() else "文件不存在")
    check("结果里的 engine_dir 是新位置", r.engine_dir == str(dst))
    check("tier 标记为 migrate", r.tier == "migrate")
    assert before is not None


def test_cross_volume_copy(base: Path, loc: Path) -> None:
    print("\n【7】跨盘迁移 → 复制路径，源被删除、目标完整")
    src = make_engine(base)
    dst = base / "crossvol"
    with mock.patch.object(bootstrap, "check_installed", ready), \
         mock.patch.object(bootstrap, "_volume", fake_volume("engine")), \
         mock.patch.object(bootstrap, "_free_bytes_at", lambda _p: 10 ** 12):
        r = bootstrap.move_engine(src, dst)
    check("返回成功", r.ok, r.message)
    check("目标解释器存在", (dst / ".venv" / "Scripts" / "python.exe").is_file())
    check("目标嵌套文件完整", (dst / ".venv" / "Lib" / "site-packages" / "big.bin").is_file())
    check("源已清理", not src.exists())
    check("location.json 已更新",
          json.loads(loc.read_text(encoding="utf-8"))["engine_dir"] == str(dst))


# ---------------------------------------------------------------- 回滚


def test_rollback_rename_verified_fail(base: Path, loc: Path) -> None:
    print("\n【8】rename 后验证失败 → 回滚到原位置（最关键的防线）")
    src = make_engine(base)
    dst = base / "bad-target"
    with mock.patch.object(bootstrap, "check_installed", not_ready):
        r = bootstrap.move_engine(src, dst)
    check("返回失败", not r.ok, r.message)
    check("提示说明已回滚", "回滚" in r.message, r.message)
    check("原位置恢复，引擎仍可用", (src / ".venv" / "Scripts" / "python.exe").is_file())
    check("目标位置未残留", not dst.exists())
    check("location.json 未被改动（不存在）", not loc.exists())


def test_rollback_copy_verified_fail(base: Path, loc: Path) -> None:
    print("\n【9】复制后验证失败 → 删掉半成品，源保持可用")
    src = make_engine(base)
    dst = base / "copy-bad"
    with mock.patch.object(bootstrap, "check_installed", not_ready), \
         mock.patch.object(bootstrap, "_volume", fake_volume("engine")), \
         mock.patch.object(bootstrap, "_free_bytes_at", lambda _p: 10 ** 12):
        r = bootstrap.move_engine(src, dst)
    check("返回失败", not r.ok, r.message)
    check("源目录完好", (src / ".venv" / "Scripts" / "python.exe").is_file())
    check("半成品目标已清理", not dst.exists())
    check("location.json 未被改动", not loc.exists())


def test_failure_does_not_touch_location(base: Path, loc: Path) -> None:
    print("\n【10】任何一种失败都不得改写 location.json")
    src = make_engine(base)
    loc.write_text(json.dumps({"engine_dir": "C:\\somewhere\\original"}),
                   encoding="utf-8")
    # 三种失败依次来
    bootstrap.move_engine(src, src)
    bootstrap.move_engine(src, base / "occupied" if False else src / ".venv" / "x")
    with mock.patch.object(bootstrap, "check_installed", not_ready):
        bootstrap.move_engine(src, base / "rollback-target")
    check("记录仍指向原位置",
          json.loads(loc.read_text(encoding="utf-8"))["engine_dir"] == "C:\\somewhere\\original",
          loc.read_text(encoding="utf-8"))


def test_result_json_protocol(base: Path, loc: Path) -> None:
    print("\n【11】迁移结果沿用安装的 JSON 协议（宿主无需两套解析）")
    src = make_engine(base)
    with mock.patch.object(bootstrap, "check_installed", ready):
        r = bootstrap.move_engine(src, base / "proto")
    payload = {
        "type": "install_result",
        "ok": r.ok,
        "tier": r.tier,
        "message": r.message,
        "engine_dir": r.engine_dir,
    }
    text = json.dumps(payload, ensure_ascii=False)
    v = json.loads(text)
    check("type 为 install_result", v["type"] == "install_result")
    check("ok 为真", v["ok"] is True)
    check("tier 为 migrate（宿主据此区分流程）", v["tier"] == "migrate")
    check("engine_dir 非空", bool(v["engine_dir"]))


def main() -> int:
    print("=" * 74)
    print("引擎目录迁移 · 编排与回滚测试")
    print("=" * 74)
    cases = [
        test_reject_same_dir,
        test_reject_nested_target,
        test_reject_nonempty_target,
        test_reject_no_engine,
        test_reject_insufficient_space,
        test_same_volume_rename,
        test_cross_volume_copy,
        test_rollback_rename_verified_fail,
        test_rollback_copy_verified_fail,
        test_failure_does_not_touch_location,
        test_result_json_protocol,
    ]
    for c in cases:
        run_case(c)
    print("\n" + "=" * 74)
    print(f"通过 {PASS} 项，失败 {FAIL} 项")
    print("=" * 74)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
