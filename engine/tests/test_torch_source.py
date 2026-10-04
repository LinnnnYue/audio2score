"""
torch 安装源与 Python 版本约束的回归自测。

## 为什么需要这份测试

主上实测「选国内源和官方源都装不上 torch」，报错只有一句：

    ERROR: Could not find a version that satisfies the requirement torch
           (from versions: none)

这条报错有**两个完全不同的根因，症状却逐字相同**：

  1. 镜像不是 PEP 503 索引
     阿里云 `pytorch-wheels/cu124` 是扁平文件目录（find-links 风格），
     当 `--index-url` 用时 pip 去请求 `<index>/torch/` 得到 404。
  2. 本机 Python 版本超出 torch wheel 覆盖范围
     torch cu124 只发布到 cp313，而 Windows 上 `py` 的默认版本是本机
     最后安装的那个（实测为 3.14）。此时**换任何源都失败**。

换源只能救第 1 个。用户（和开发者）都会因为报错文案相同而误判成网络问题，
在「换源」上反复空转 —— 这正是本次事故的真实经过。

所以这两条约束都必须被测试钉死：镜像是 PEP 503、Python 锁在支持区间。

用法：
    python engine/tests/test_torch_source.py          # 仅离线断言（默认）
    python engine/tests/test_torch_source.py --net    # 附加镜像可达性探测
"""
import importlib.util
import os
import subprocess
import sys
import urllib.request

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    mark = "PASS" if cond else "FAIL"
    line = "  [{}] {}".format(mark, name)
    if detail and not cond:
        line += "\n         -> {}".format(detail)
    print(line)


def load_bootstrap():
    """以模块方式加载 bootstrap.py（dataclass 要求先注册进 sys.modules）。"""
    path = os.path.join(ENGINE, "bootstrap.py")
    spec = importlib.util.spec_from_file_location("bootstrap", path)
    m = importlib.util.module_from_spec(spec)
    sys.modules["bootstrap"] = m
    spec.loader.exec_module(m)
    return m


print("=" * 74)
print("torch 安装源 / Python 版本约束 回归自测")
print("=" * 74)

bs = load_bootstrap()

# ── 1. Python 版本约束 ──
print("\n【1】Python 版本约束")
check("TORCH_PY_MIN 为 (3,10)", bs.TORCH_PY_MIN == (3, 10), str(bs.TORCH_PY_MIN))
check("TORCH_PY_MAX 为 (3,13)", bs.TORCH_PY_MAX == (3, 13), str(bs.TORCH_PY_MAX))
check(
    "区间下限不高于上限",
    bs.TORCH_PY_MIN <= bs.TORCH_PY_MAX,
    "{} vs {}".format(bs.TORCH_PY_MIN, bs.TORCH_PY_MAX),
)

cands = bs.iter_base_python_candidates()
flat = [" ".join(c) for c in cands]
for minor in range(bs.TORCH_PY_MAX[1], bs.TORCH_PY_MIN[1] - 1, -1):
    want = "py -3.{}".format(minor)
    check("候选含 {}".format(want), want in flat, "候选={}".format(flat[:12]))

check(
    "候选含裸 py（默认版本可能是合规的）",
    "py" in flat,
    "候选={}".format(flat[:12]),
)
check(
    "候选含 python / python3",
    "python" in flat and "python3" in flat,
    "候选={}".format(flat[:12]),
)


# ── 2. 探测函数必须拒绝超界版本 ──
print("\n【2】探测函数的版本过滤")
rejects = []
sel = bs.find_base_python(rejects)
if sel is not None:
    v = bs._python_version_of(sel)
    check(
        "find_base_python 返回的版本落在支持区间内",
        v is not None and bs.TORCH_PY_MIN <= v <= bs.TORCH_PY_MAX,
        "选中={} 版本={}".format(sel, v),
    )
else:
    # 本机确实没有合规 Python：此时必须留下可解释的 rejects
    check(
        "无合规 Python 时给出被拒版本清单",
        len(rejects) > 0,
        "rejects 为空，用户将无法得知原因",
    )
    print("  (本机无合规 Python，跳过版本断言)")

# 逐项验证：凡是被拒的候选，其版本都必须确实超界。
# 反向意义：若某个合规版本被误拒，用户就会用不上本可用的环境。
for c, v in rejects:
    try:
        maj, _, mino = v.partition(".")
        tv = (int(maj), int(mino))
    except ValueError:
        continue
    check(
        "被拒候选 {} 的版本 {} 确实超界".format(c, v),
        not (bs.TORCH_PY_MIN <= tv <= bs.TORCH_PY_MAX),
        "合规版本被误拒，用户会用不上可用环境",
    )


# ── 3. 镜像源必须满足 PEP 503 ──
print("\n【3】镜像源配置")
check("存在 cn 与 official 两个源", {"cn", "official"} <= set(bs.MIRRORS),
      str(list(bs.MIRRORS)))
check("DEFAULT_MIRROR 指向已定义的源", bs.DEFAULT_MIRROR in bs.MIRRORS,
      bs.DEFAULT_MIRROR)

for key, cfg in bs.MIRRORS.items():
    idx = cfg.get("torch_index") or ""
    check("{} 配置了 torch_index".format(key), bool(idx), str(cfg))
    # 阿里云 pytorch-wheels 是扁平目录，当 index-url 必 404 —— 钉死不许回退
    check(
        "{} 的 torch_index 不是扁平目录源（aliyun）".format(key),
        "mirrors.aliyun.com" not in idx,
        "aliyun 的 pytorch-wheels 是 find-links 目录，用 --index-url 必然 404",
    )
    check(
        "{} 的 torch_index 为 https".format(key),
        idx.startswith("https://"),
        idx,
    )

check(
    "回退链至少有两个可用源",
    len([m for m in bs.MIRRORS.values() if m.get("torch_index")]) >= 2,
    "源回退是本项目对「镜像偶发限流」的兜底，少于两个等于没有兜底",
)


# ── 4. 报错归因必须把「找不到包」与「网络中断」分开 ──
print("\n【4】报错归因")
check(
    "网络关键词不含 could not find a version",
    "could not find a version" not in bs._NET_HINTS,
    "归入网络类会把用户引向「检查网络」，而真实根因可能是 Python 版本",
)
check(
    "网络关键词不含 no matching distribution",
    "no matching distribution" not in bs._NET_HINTS,
    "同上",
)
check(
    "_NO_DIST_HINTS 含 could not find a version",
    "could not find a version" in bs._NO_DIST_HINTS,
    str(bs._NO_DIST_HINTS),
)
check(
    "_NO_DIST_HINTS 含 no matching distribution",
    "no matching distribution" in bs._NO_DIST_HINTS,
    str(bs._NO_DIST_HINTS),
)


# ── 5. pip 命令构造：torch 走 --index-url，且不与 -i 打架 ──
print("\n【5】pip 命令构造")
captured = []


class _FakeDone:
    returncode = 0
    stdout = ""
    stderr = ""


def _fake_run(cmd, **kw):
    """替身：记录 pip 命令但不真的执行。"""
    captured.append(cmd)
    return _FakeDone()


_real_run = bs.subprocess.run
try:
    bs.subprocess.run = _fake_run
    bs._pip_install(
        "PYEXE", ["torch", "torchaudio", "--index-url", bs.MIRRORS["cn"]["torch_index"]],
        None, "torch", 0.0, 1.0, [],
        pypi=bs.MIRRORS["cn"]["pypi"], label="PyTorch",
    )
finally:
    bs.subprocess.run = _real_run

ok_cmd = bool(captured)
check("_pip_install 触发了 subprocess", ok_cmd, "未捕获到命令")
if ok_cmd:
    cmd = captured[0]
    check("命令使用 --index-url", "--index-url" in cmd, " ".join(cmd))
    check(
        "带 --index-url 时不再追加 -i（两个 index 会打架）",
        "-i" not in cmd and "--index-url" in cmd,
        " ".join(cmd),
    )
    check(
        "index-url 指向配置中的国内源",
        bs.MIRRORS["cn"]["torch_index"] in cmd,
        " ".join(cmd),
    )


# ── 6. install_status 输出契约（前端据此渲染警告）──
print("\n【6】install_status 输出契约")
st = bs.install_status()
check("含 pythonCompat 字段", "pythonCompat" in st, str(list(st)))
if "pythonCompat" in st:
    pc = st["pythonCompat"]
    check("pythonCompat 含 ok", isinstance(pc.get("ok"), bool), str(pc))
    check("pythonCompat 含 detail", isinstance(pc.get("detail"), str), str(pc))
    if not pc.get("ok"):
        check("不兼容时 detail 非空", bool(pc.get("detail", "").strip()), str(pc))
tiers = st.get("tiers", [])
check("含 tiers", bool(tiers), str(tiers)[:120])
check(
    "每个档位都带 needsTorch",
    all("needsTorch" in t for t in tiers),
    str([(t.get("id"), t.get("needsTorch")) for t in tiers]),
)
basic = next((t for t in tiers if t.get("id") == "basic"), None)
full = next((t for t in tiers if t.get("id") == "full"), None)
check("basic 档 needsTorch=False", basic is not None and basic["needsTorch"] is False,
      str(basic))
check("full 档 needsTorch=True", full is not None and full["needsTorch"] is True,
      str(full))

# 模拟「本机只有 3.14」：此时必须给出可解释的文案，而不是一句空警告。
# 这是主上真实踩到的场景（`py` 默认 3.14），必须有回归。
print("\n【6b】无合规 Python 时的文案（模拟）")
_real_finded = bs.find_base_python
try:
    def _fake_find(rejects=None):
        if rejects is not None:
            rejects.extend([("py", "3.14"), ("python", "3.14")])
        return None

    bs.find_base_python = _fake_find
    bs._PY_COMPAT_CACHE = None
    pc = bs.python_compat(fresh=True)
    check("ok=False", pc["ok"] is False, str(pc))
    check("detail 非空", bool(pc["detail"].strip()), str(pc))
    check("detail 提到 3.14（实际检测到的版本）", "3.14" in pc["detail"], pc["detail"])
    check("detail 给出可行出路（基础档或安装 Python）",
          ("基础" in pc["detail"]) or ("安装 Python" in pc["detail"]), pc["detail"])
finally:
    bs.find_base_python = _real_finded
    bs._PY_COMPAT_CACHE = None


# ── 7. 联网探测（可选）──
if "--net" in sys.argv:
    print("\n【7】镜像 PEP 503 可达性（联网）")
    for key, cfg in bs.MIRRORS.items():
        idx = cfg["torch_index"].rstrip("/")
        url = idx + "/torch/"
        code = None
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=20) as r:
                code = r.status
        except Exception as e:  # noqa: BLE001
            code = "ERR {}".format(type(e).__name__)
            # HEAD 可能被拒，退回 GET
            try:
                with urllib.request.urlopen(url, timeout=20) as r:
                    code = r.status
            except Exception as e2:  # noqa: BLE001
                code = "ERR {}".format(type(e2).__name__)
        check("{} 的 <index>/torch/ 可访问".format(key), code == 200,
              "{} -> {}".format(url, code))
else:
    print("\n【7】镜像可达性探测已跳过（加 --net 启用）")


# ── 汇总 ──
print("\n" + "=" * 74)
print("通过 {} 项，失败 {} 项".format(len(PASS), len(FAIL)))
if FAIL:
    print("\n失败项：")
    for f in FAIL:
        print("  · {}".format(f))
print("=" * 74)
sys.exit(1 if FAIL else 0)
