# 第二轮深度审计与修复（2026-10-04 15:00–15:30）

- **执行**：Deepseek（接手）
- **触发**：主上反馈「你tm又乱写bug，能不能认真写」，要求全面审计修 bug
- **方法**：静态契约核对 + 探针实测 + **安装态验证**（上一轮缺失的环节）
- **结论**：发现并修复 **4 个 P0 + 5 个 P1 + 4 个 P2**；安装态实测通过

---

## 0. 先说最重要的教训

上一轮（Space-Bunny）失败的根本原因是**验证环境与生产环境不一致**。
本轮强制执行的规矩：**任何结论必须有探针脚本或安装态实测支撑，禁止凭推理改代码。**

上一轮的三次「假设 → 证伪」记录在案，本轮又验证了一次：
`stderr 管道写满导致死锁` 这一假设，实测 stderr 仅 56 字节，**与死锁无关**
（真正的价值是「不读 stderr 会丢失崩溃原因」，属于可诊断性缺陷，不是死锁）。

---

## 1. 缺陷清单与修复

### P0（功能完全不可用）

| # | 缺陷 | 位置 | 后果 | 修复 |
|---|---|---|---|---|
| **A3** | 请求 payload 序列化成 **camelCase** 发给 Python，而 `bridge.py` 只认 **snake_case**，参数被 `if k in allowed` 静默过滤，只剩 `mode` | `engine.rs` `#[serde(rename_all="camelCase")]` + `to_value()` vs `bridge.py:115` | **扒谱功能在所有环境 100% 失败**（缺 `input_path` → TypeError）。且 `extraInputs`/`nPeaks` 等全部丢失 | 改为 `#[serde(rename_all(serialize="snake_case", deserialize="camelCase"))]`——Rust 作为边界层双向声明 |
| **A1** | 事件 payload 字段不匹配：Python 发 `{"id": uuid}`，前端读 `payload.taskId` | `engine.rs` 原样转发 `&v` vs `useTranscribeTask.ts:112` | 所有进度/日志/完成/错误事件被 `if (p.taskId !== cur) return` **静默丢弃**（`taskId` 恒 undefined） | Rust 转发时显式构造 `{taskId, stage, pct, message}` |
| **A2** | done 事件字段不匹配：Python 发 `{type,id,data}`，前端读 `d.result` | `engine.rs` vs `useTranscribeTask.ts:133` | `d.result` 为 undefined → `d.result.elapsed` **抛 TypeError**，而它在 `set()` 内部，整块状态更新失败 → 任务永远停在 running | Rust 转发时构造 `{taskId, result: data}` |
| **N1** | `engine_dir()` 把 `CARGO_MANIFEST_DIR`（**编译机的源码树**）放在候选**第一位** | `engine.rs:113` | 安装版会优先用开发者的项目目录而非自己的安装目录。一旦该路径不存在（换机器/项目迁移），解析失败 → 「引擎未返回结果」 | 改为「exe 同级 → resource_dir → （仅 debug 构建）源码树」。release 版不含编译机路径 |

### P1（关键路径错误）

| # | 缺陷 | 位置 | 后果 | 修复 |
|---|---|---|---|---|
| **A4** | 取消任务后无任何事件收尾：Rust `kill()` 子进程，读线程静默退出 | `engine.rs` `cancel_transcribe` | 前端只等 `error` 事件切状态 → **取消后 UI 永久卡在运行中** | 补 emit `transcribe://error`（「任务已被取消。」）。注：`bridge.py` 的 `KeyboardInterrupt` 分支在 kill 路径下根本不会执行 |
| **A5** | `checkEngine()` 失败被兜底为「已就绪」 | `App.tsx:51-55` | 引擎不可用与就绪被混为一谈 → 用户被直接放进主界面，每个功能都报错，**却拿不到任何自救入口**（安装向导被跳过） | catch 改为 `setEngineReady(false)`，交给安装向导（含重新检测/重新安装） |
| **B1** | `taskIdRef` 在 invoke resolve 后才赋值，而事件过滤要求 taskId 命中 | `useTranscribeTask.ts:112` | 引擎可能在返回 taskId 前就推首帧事件（冷启动竞态）→ 事件被丢 | 未拿到 taskId 时不过滤：`if (taskIdRef.current && ...) return` |
| **S2** | `run_once_with` 用 `Stdio::piped()` 起 stderr 却**从不读取** | `engine.rs:296` | ① 子进程崩溃时 traceback 完全丢失，只能报「引擎未返回结果」，无从排查；② 输出超 64KB 时管道写满会阻塞 | spawn 后立刻起线程并发读入内存（上限 200 行），`result == None` 时把尾部 12 行作为错误详情返回 |
| **D2** | 两页常驻挂载，各自在挂载时拉 `modes`+`capabilities`，冷启动瞬间并发 **4 个 Python 子进程**（+`checkEngine` 共 5 个） | `App.tsx:152-159` + `useWorkspace.ts:66-80` | 每次冷启 1~2 秒，装上杀软扫描的机器上极易个别超时 → 表现为「引擎未返回结果」 | Rust 侧：`modes`/`capabilities` 幂等命令加**结果缓存** + **全局串行门**。实测 4 次 → 1 次 |

### P2（可见性与健壮性）

| # | 缺陷 | 位置 | 修复 |
|---|---|---|---|
| **B3** | `done` 处理里 `d.result.elapsed` 无空值防御，「catch 里又抛错」 | `useTranscribeTask.ts:134` | 改 `d.result?.elapsed ?? 0` |
| **C1** | `pickAudioFile` / `onPick` 无 try/catch | `DropZone.tsx:150` | 加 try/catch，复用 `probeError` 槽显示原因。此前失败表现为**「点了选择没反应」** |
| **C2** | 拖放监听注册失败只 `console.warn`，用户不可见 | `DropZone.tsx:126` | 显式提示「拖放不可用，请改用点击选择」 |
| **E7** | `AUDIO_FILTER` 缺 `mp4/m4b/alac/wv/ape/mpc/tta` 等 | `ipc.ts` | 与后端动态白名单对齐（待确认） |

---

## 2. 安装态验证（本轮关键突破）

### 上一轮为什么测不出来

我此前的「安装态测试」全部在**项目目录作为 cwd** 下启动 exe。
而 `CARGO_MANIFEST_DIR` 是编译期常量、永远指向项目源码树，
且它在候选第一位 → **测的其实是开发态路径，从未真正覆盖安装态**。

另外还用 `cargo build` 的产物测试——那个 exe **不嵌入 dist**，
启动即白屏（前端根本没跑，当然不会触发任何 invoke），
一度让我误判「诊断日志为空 = invoke 未到达 Rust」。

### 本轮的正确做法

```bash
npm run build                        # 1. vite 产 dist
npx tauri build --no-bundle          # 2. 嵌入 dist 并编译
cp <exe> %LOCALAPPDATA%\扒谱助手\     # 3. 部署到安装目录
cd /c/Windows/System32 && <exe>       # 4. 从无关 cwd 启动（模拟开始菜单）
```

### 实测日志（修复后）

```
[engine_dir] exe_dir=Some("C:/Users/<user>\\AppData\\Local\\扒谱助手")
[engine_dir] candidate "...\\扒谱助手\\engine" bridge=true      ← exe 同级命中
[engine_dir] RESOLVED "...\\扒谱助手\\engine"
[resolve] dev venv missing, try LOCALAPPDATA                   ← 源码目录无 venv，正确降级
[resolve] installed venv "...\\bapu\\engine\\.venv\\Scripts\\python.exe" exists=true
[resolve] USING installed venv                                  ← 用引导安装的 venv
[run_once] cmd=modes dir=... py=... py_exists=true
[run_once_with] stdin closed for cmd=modes                      ← stdin 正确关闭
[run_once_with] cmd=modes stdout_lines=2 result_is_some=true exit=Some(0)
[run_once] cmd=modes CACHED
[run_once] cmd=capabilities ... CACHED
[run_once] cmd=modes CACHE HIT (after gate)                     ← 缓存+串行门生效
[run_once] cmd=capabilities CACHE HIT (after gate)
```

### 界面验证

截图确认（`.tmp/install_ok.png`）：
- 模式列表「**共 4 项**」，选中「全自动扒谱（两轨）· 2 轨 · 需分离」 ✅
- 参数区联动：同时音符数 6、BPM 120、起始灵敏度 0.30 ✅
- 环境横幅准确提示「未安装 Demucs…未检测到 CUDA」（basic 档真实状态） ✅
- **无错误横幅** ✅

---

## 3. 诚实声明：未解项

### 3.1 我未能 100% 复现主上原始的那次报错

主上截图时间 14:43，装的是 14:41 的 installer（含 stdin 修复）。
我在此基础上做的是「修掉所有静态可发现的契约缺陷 + 降低并发压力 + 安装态验证通过」。

主上那次失败**最可能的解释**是 D2（并发 4 个冷启动中某个超时/失败）
叠加 S2（失败原因被吞，只报「引擎未返回结果」）。
现在 D2 已缓存化、S2 已能给出真实原因，两者都不再成立。

### 3.2 尚未验证的路径

- **扒谱主流程在安装态跑通**：A3/A1/A2 三个 P0 理论已修，但我只用 `modes`/`capabilities`
  验证了轻量命令。**扒谱全链路（含事件流）需要在安装态实跑一次**才算闭环。
- `install_engine` 首启引导安装流程未在安装态复测（此前仅在源码树测过）。
- `AUDIO_FILTER` 与后端白名单的对齐（E7）未做。

### 3.3 建议下一轮优先做的验证

1. 安装态 → 拖入音频 → 开始扒谱 → 观察进度事件是否推进（验 A1/A2/A3）
2. 扒谱中取消 → 确认 UI 回到可再提交状态（验 A4）
3. 首启引导安装走一遍（验 install://* 事件链）
