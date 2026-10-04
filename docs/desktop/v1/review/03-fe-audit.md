# 前端缺陷审计报告 — 扒谱助手（Tauri 2 + React 19 + TS）

- 审计范围：`src/src/**`（前端），`src-tauri/src/**`（只读对照），`engine/bridge.py`（只读对照）
- 审计维度：invoke 链路 / 状态机竞态 / 错误处理 / 生命周期 / 类型契约
- 结论性质：**只读审计，未修改任何源码**；所有结论附 `文件:行号`
- 复现症状（主上）：安装态启动后主界面横幅
  > 模式列表与环境信息加载失败 / 引擎未返回结果，进程可能异常退出。

---

## 1. 结论摘要

### 1.1 先说已排除项（避免误判方向）

- **`inTauri()` 判据可靠**。`@tauri-apps/api@2.12.1` 的 `invoke` 内部直接调用
  `window.__TAURI_INTERNALS__.invoke(...)`（`src/node_modules/@tauri-apps/api/core.js:328`），
  而 `ipc.ts` 用的判据正是 `'__TAURI_INTERNALS__' in window`（`ipc.ts:49-50`），
  与实际运行前提**一致**。
- **反向佐证**：若 `inTauri()` 误判为 false，所有调用会走 stub 分支返回**假数据**且
  不报错（`ipc.ts:52-55`、`137`、`163`、`170`）。用户实际看到的是 Rust 兜底文案
  `引擎未返回结果，进程可能异常退出。`（`engine.rs:327`），说明 invoke **确实到达 Rust**。
  故「走 stub 假数据」不是本次症状的成因。
- **command 名 / 事件名逐字一致**（详见第 3 节）。前端 `invoke('xxx')` 与 Rust
  `#[tauri::command] pub async fn xxx` 全部对应，`transcribe://*`、`install://*` 事件名也全部一致。

### 1.2 「安装态失败、开发态正常」最可疑的 3 条（按可能性排序）

| 序 | 嫌疑 | 层次 | 为什么「只在安装态」出现 |
|----|------|------|--------------------------|
| S1 | **App.tsx 对 `checkEngine()` 的失败兜底把 `engineReady` 置为 `true`，直接跳过首启安装向导进入主界面**（`App.tsx:45-59`，尤其 `51-55`）。安装态一旦 `check_engine` 抛 Err（系统无 `py/python`、`bootstrap.py --status` 非零退出、`engine_dir()` 找不到 `bridge.py`），前端**不做任何区分**就放行到主界面，随即调用尚未就绪的引擎 → 横幅。 | **纯前端** | 开发机必有系统 Python 且 `engine/` 目录/venv 齐全，`check_engine` 正常返回，永不触发该 catch 分支。 |
| S2 | **引擎 sidecar 子进程在安装态硬崩退，stdout 无任何 JSON 事件** → `run_once_with` 的 `result` 保持 `None` → 落到 `engine.rs:327` 兜底文案。放大因素：`run_once_with` 把 stderr 设为 piped 却**从不读取**（`engine.rs:241-247`、`278`），子进程序言期崩溃的真实原因被完全吞掉，用户只看到「进程可能异常退出」。安装态解析到的解释器是 `%LOCALAPPDATA%\bapu\engine\.venv`（`engine.rs:193-209`）而脚本来自**资源暂存目录**（`engine.rs:106-146`），二者组合与开发态（脚本与 venv 同目录）不同，正是差异发生地。 | Rust/引擎（跨层） | 开发态 `resolve_engine_python` 命中 `engine/.venv`（`engine.rs:176-190`），脚本与解释器同源、依赖完整；安装态依赖落地位置不同。 |
| S3 | **`start_transcribe` 并未把请求转成 bridge 需要的 snake_case**，导致（即便引擎就绪）扒谱链路必然失败（`engine.rs:369-371` 注释声称转换、代码未实现）。此条不是本次横幅的直接原因，但会让任何一次「开始扒谱」**在所有环境**失败，是必须一并修的 P0。 | Rust↔bridge（跨层） | 与安装/开发无关；属于**全局性缺陷**，独立列此以澄清 S1/S2 之外的系统性问题。 |

> 诚实边界：S2 的「子进程为何硬崩」我无法纯静态定论（需抓取被吞掉的 stderr）。但
> 「stderr 被 piped 却不读」这一放大缺陷 100% 确定，且它使 S2 完全不可诊断。

---

## 2. 缺陷清单

严重度：**P0** 阻断（功能不可用）· **P1** 严重（关键路径错误）· **P2** 一般 · **P3** 建议

### A. invoke 链路 / 跨层契约

| # | 严重度 | 文件:行号 | 问题 | 证据 | 修复方向 |
|---|--------|-----------|------|------|----------|
| A1 | **P0** | `src/src/lib/useTranscribeTask.ts:112 / 122 / 126 / 140`（对照 `src-tauri/src/engine.rs:442,445,448,460`） | **事件 payload 字段名不匹配：引擎/Rust 发的是 `id`，前端读的是 `taskId`。** 所有 transcribe 事件被 `if (x.taskId !== taskIdRef.current) return` 静默丢弃（`taskId` 恒为 `undefined`，永不等价于真实 uuid）。 | bridge.py 协议：`{"type":"progress","id":req_id,...}`；Rust 读线程**原样转发** `app_for_events.emit("transcribe://progress", &v)`，未做 `id→taskId` 改名。前端 `ProgressEvent.taskId`（`types.ts:116-122`）。 | 二选一：① Rust 转发前把 `id` 重映射为 `taskId`、`data` 重映射为 `result`；② 前端改读 `id`/`data`。建议前者（保持前端契约 `types.ts` 不变）。 |
| A2 | **P0** | `src/src/lib/useTranscribeTask.ts:133-134`（对照 `engine/bridge.py` result 分支、`src-tauri/src/engine.rs:448`） | **done 事件载荷字段不匹配：Rust 原样转发 `{type,id,data}`，前端读 `d.result`。** `d.result` 为 `undefined`，第 134 行 `d.result.elapsed` 会**抛 TypeError**，导致 `set()` 内的状态更新整体失败 → 任务永远停在 running。 | 前端 `DoneEvent.result`（`types.ts:131-134`）；Rust emit `&v`（`engine.rs:448`）未把 `data→result`。 | 同 A1，Rust 侧统一重命名 `data`→`result`。 |
| A3 | **P0** | `src-tauri/src/engine.rs:369-371` + `engine/bridge.py`（`handle_transcribe` 的 `allowed` 集合） | **`start_transcribe` 序列化出的 payload 是 camelCase，bridge 只认 snake_case，导致 `input_path` 等字段被全部丢弃。** `serde_json::to_value(&request)` 对带 `#[serde(rename_all="camelCase")]` 的结构体输出 `inputPath/extraInputs/nPeaks...`；bridge 用 `{k:v for k,v in payload.items() if k in allowed}`（allowed 为 snake_case 集合）过滤，只剩 `mode`；`TranscribeRequest(**{mode})` 缺必填 `input_path` → TypeError。 | Rust 结构体 `#[serde(rename_all="camelCase")]`（`engine.rs:36`）；`to_value`（`engine.rs:370`）；bridge `allowed` snake_case 集合。 | Rust 在发给 bridge 前显式转回 snake_case（如再声明一个 `serde(rename_all="snake_case")` 的镜像结构体，或用 `serde_json` 手工映射）。 |
| A4 | **P1** | `src/src/lib/ipc.ts:142-145` + `src-tauri/src/engine.rs:507-522` + `src/src/lib/useTranscribeTask.ts:198-207` | **取消任务不会产生 error 事件，UI 永久卡在 running。** Rust `cancel_transcribe` 只 `kill` 子进程；读线程在 `for line in reader.lines()` 因 EOF 结束后**不 emit 任何事件**（`engine.rs:471-481` 只做回收）。而 `ipc.ts` 注释与设计均假设「取消后引擎会发 error 事件，文案为『任务已被取消。』」（`ipc.ts:141`、`ipc.ts:325`、`engine-contract.ts:33`），实际 bridge 的 `KeyboardInterrupt` 分支只有在子进程**自己**收到中断时才会跑到，`kill` 走不到。 | 见左。 | Rust 在 EOF/取消路径补 emit 一条 `transcribe://error`（`message:"任务已被取消。"`）；或前端取消后本地置为 error 态。 |
| A5 | **P1** | `src/src/App.tsx:45-59`、`51-55` | **`checkEngine()` 失败被兜底为「已就绪」，绕过首启安装向导。** catch 里无条件 `setEngineReady(true)`，把「探测异常」与「引擎就绪」等同，直接进主界面。这正是「安装态看到主界面横幅」的最省事路径（见 S1）。 | `App.tsx:51-55`。 | 区分「返回 ready:false」与「invoke 抛错」：抛错时进入 EngineSetup 的 `failed` 态并显示错误，而非放行。 |
| A6 | **P2** | `src/src/lib/ipc.ts:329-332` | `cancelInstall` 复用 `cancel_transcribe`。语义耦合但可用（安装子进程 `stdin:None`，`kill` 有效）。真正的缺陷同 A4：kill 后安装读线程虽会 emit `install://done`（`engine.rs:674-677`），但**普通 transcribe 取消无此收尾**，两条路径行为不一致。 | `ipc.ts:329-332`；`engine.rs:674-677` vs `471-481`。 | 保持，但把 A4 的收尾语义在 transcribe 路径补齐。 |
| A7 | **P3** | `src/src/lib/engine-contract.ts:13` | 契约注释称「简单标量参数由 Tauri 自动兼容两种写法」——**表述不准确**。Tauri 2 command 参数默认按 camelCase 匹配（Rust `task_id` ↔ JS `taskId`），并非「两种都收」。本仓库因参数恰好同名/单词（`path`/`request`/`tier`）而无实际影响，但注释会误导后续加参。 | Tauri v2 command arg 默认 camelCase；`ipc.ts:144`、`158`、`326`。 | 更正注释为「默认 camelCase，Rust snake_case 参数名会被自动映射」。 |

### B. 状态机与竞态

| # | 严重度 | 文件:行号 | 问题 | 证据 | 修复方向 |
|---|--------|-----------|------|------|----------|
| B1 | **P1** | `src/src/lib/useTranscribeTask.ts:173-196` | **`taskIdRef` 在 invoke 返回后才赋值，而事件过滤要求 `taskId` 命中，二者存在竞态窗口。** 引擎在 `accepted` 后可能立即推进度，但此时 `taskIdRef.current` 仍为 `null`，事件被丢弃（代码注释也自认 `engine.rs` 之前的 progress 会丢，`useTranscribeTask.ts:183-184`）。极端情况下若引擎极快完成，`done` 也可能早于赋值被丢 → 卡 running。 | `useTranscribeTask.ts:181-185`。 | invoke `start_transcribe` 应能**先拿到 taskId**（Rust 已在 `Ok(StartResult)` 前 insert 任务表），或前端在订阅回调里放宽「首帧事件」的过滤策略。 |
| B2 | **P1** | `src/src/lib/useTranscribeTask.ts:198-207` + `ipc.ts:142-145` | 取消路径见 A4：`cancel()` 只发 kill，无本地状态兜底，UI 永久 running。 | 同 A4。 | 同 A4。 |
| B3 | **P2** | `src/src/lib/useTranscribeTask.ts:128-137` | `done` 处理里 `elapsed: d.result.elapsed` 与 `result: d.result` 未做空值防御；一旦 A2 成立即抛错崩溃。属于「catch 里又抛错」的典型。 | `useTranscribeTask.ts:133-134`。 | 先修 A2；另加 `d?.result` 防御。 |
| B4 | **P2** | `src/src/lib/useTranscribeTask.ts:173-178` | `start()` 只靠 `runningRef.current` 防重入；`reset()`（`209-213`）会把 `runningRef` 置 false 但**不通知引擎**，随后可再次 `start`。若上一任务子进程仍在跑，会出现同一时刻两个任务事件交错（虽被 taskId 过滤，但引擎侧资源双开）。 | `useTranscribeTask.ts:174,209-213`。 | 重新提交前先 `cancel()` 旧任务。 |
| B5 | **P3** | `src/src/lib/useTranscribeTask.ts:165-171` | 秒表 `useEffect` 依赖 `[state.status]`，每次 status 变化重建 interval；running 期间每秒 `setState` 触发重渲染正常。无泄漏（有 clearInterval）。仅提示可在 running 内保持单 interval。 | `useTranscribeTask.ts:165-171`。 | 可保持。 |

### C. 错误处理完整性

| # | 严重度 | 文件:行号 | 问题 | 证据 | 修复方向 |
|---|--------|-----------|------|------|----------|
| C1 | **P2** | `src/src/lib/ipc.ts:247-257`、`263-270` + `src/src/components/DropZone.tsx:150-154` | `pickAudioFile()` / `pickMidiOutput()` 内部动态 `import` 与 `open()/save()` **无 try/catch**；`DropZone.onPick` 也未包裹（`DropZone.tsx:152`）。插件调用失败 → 未处理的 Promise rejection，用户界面无任何反馈。 | `DropZone.tsx:150-154`；对比 `ResultCard.tsx:99,107` 有 `.catch(()=>{})`。 | `onPick` 加 try/catch 并展示错误。 |
| C2 | **P2** | `src/src/components/DropZone.tsx:126` | `onDragDropEvent` 注册失败仅 `console.warn`，用户不可见（任务描述点名项）。安装态若该监听不可用，拖放将静默失效。 | `DropZone.tsx:122-126`。 | 失败时设置可见的 `probeError`/提示态。 |
| C3 | **P2** | `src/src/components/EngineSetup.tsx:90-108` | 安装事件订阅**无失败提示**；若 `install_engine` 返回成功但事件流断（进程早退），页面永久停在 `installing`，无超时、无退路。 | `EngineSetup.tsx:90-108`、`116-129`。 | 加超时/心跳，长时间无事件则提示并可重试。 |
| C4 | **P3** | `src/src/components/EngineSetup.tsx:94-105` | 监听注册是 `void X().then(f=>cleanups.push(f))`，若 `phase` 在 promise 落地前又变化，`cleanups` 可能错过该 unlisten（异步窗口）。影响小（phase 变化即重订阅）。 | `EngineSetup.tsx:94-105`。 | 用 disposed 标志兜底（与 `useTranscribeTask.ts:150-153` 同款写法）。 |
| C5 | **P3（已修复确认）** | — | 历史上「`loadError` 只存不显示」的 bug **已修复**：`useWorkspace.ts:77-79` 存 `loadError`，`SongTranscribe.tsx:38-42`、`BasicTranscribe.tsx:59-63` 经 `LoadErrorBanner` 渲染。本次横幅正是该组件生效的证据。 | `LoadErrorBanner.tsx:23-68`。 | 无需处理。 |

### D. 生命周期与内存

| # | 严重度 | 文件:行号 | 问题 | 证据 | 修复方向 |
|---|--------|-----------|------|------|----------|
| D1 | **P2** | `src/src/components/BasicTranscribe.tsx:46-53` + `src/src/components/DropZone.tsx:68-96,99-132` | **`DropZone` 拖放监听每次渲染都重订阅。** `BasicTranscribe` 传入的 `onFiles`（`setVocalFiles`/`setAccompFiles`）是**内联箭头函数**、每次渲染新引用 → `accept`（`useCallback` 依赖 `onFiles`）每次都变 → `useEffect([accept,armed,disabled])` 反复 `unlisten→listen`。有清理（无泄漏），但存在「解绑/重绑异步间隙丢 drop 事件」的风险，且造成监听抖动。 | `BasicTranscribe.tsx:46-53,98,120`；`DropZone.tsx:95,132`。 | 用 `useCallback` 稳定 `setVocalFiles/setAccompFiles`，或 `DropZone` 内用 ref 持有最新 `onFiles`、依赖数组去掉 `accept`。 |
| D2 | **P2** | `src/src/App.tsx:152-159` + `SongTranscribe.tsx:26-30` / `BasicTranscribe.tsx:31-34` | 两个功能页**常驻挂载**（`hidden` 切换），各自 `useWorkspace` 挂载即调 `load()` → 应用启动时并发发起 **最多 4 个 `run_once` 子进程**（2 页 × `getModes`+`getEnvInfo`），叠加 App 的 `checkEngine`。在安装态（冷启动、python 冷启 1–2s/次、可能被杀软扫描）会显著放大「加载失败」概率。 | `App.tsx:152-159`；`useWorkspace.ts:66-80`；`SongTranscribe.tsx:26`。 | 模式/环境信息上提到共享层（App 加载一次下发），或改成「懒加载：页面首次可见才 load」。 |
| D3 | **P3** | `src/src/App.tsx:61-64`、`themes.ts:225-233` | 主题切换**只改 `:root` CSS 变量**，不重挂组件树 → `App.tsx:8` 声称的「切主题不丢任务状态」成立。**已确认无此问题。** | `themes.ts:225-233`。 | 无需处理。 |
| D4 | **P3** | `src/src/lib/useTranscribeTask.ts:100-162` | 两个页面各有一个 `useTranscribeTask` 实例，各自订阅同一组**全局事件**（共 2 份监听器）。因都按 taskId 过滤，功能无碍，但存在重复订阅。 | `useTranscribeTask.ts:100-162`。 | 如需可合并为单一 store。 |

### E. 与 Rust 的类型契约

| # | 严重度 | 文件:行号 | 问题 | 证据 | 修复方向 |
|---|--------|-----------|------|------|----------|
| E1 | **P0** | 见 A1/A2 | 事件字段 `id`↔`taskId`、`data`↔`result` 双向不一致。 | 见 A1/A2。 | 见 A1/A2。 |
| E2 | **P0** | 见 A3 | 请求 payload camelCase↔snake_case 不一致。 | 见 A3。 | 见 A3。 |
| E3 | **P2** | `src/src/lib/types.ts:70-81`（`TranscribeResult`） vs `engine/bridge.py` result.data | 需注意 `result.data.tracks` 元素为 **snake_case**（`name/program/notes/duration`，`bridge.py` 原样透传 `result.tracks`），而顶层 `outputPath/totalNotes/mode/separationMethod` 为 camelCase。`types.ts:61-67` 已正确区分，**无 bug**，但极易被后续「顺手统一」改坏（`types.ts:5-7` 已警示）。 | `types.ts:61-67`。 | 保持，勿递归转换。 |
| E4 | **P2** | `src/src/lib/useWorkspace.ts:96-112` | 前端 `TranscribeRequest` 未传 `usePyin`（`pipeline` 有默认 `True`，OK）；但前端**会传** `allowHpssFallback/device/demucsModel`，而 A3 一旦成立这些也一并被 bridge 丢弃。属 A3 的下游影响。 | `useWorkspace.ts:109-111`。 | 随 A3 修复。 |

---

## 3. 契约核对表（前端 vs Rust vs bridge）

### 3.1 Commands

| command | 前端调用点 | 前端 args | Rust 签名（`engine.rs`） | Rust 参数名匹配 | 结论 |
|---|---|---|---|---|---|
| `start_transcribe` | `ipc.ts:138` | `{ request }` | `fn start_transcribe(app, state, request: TranscribeRequest)` | `request` ✅ | **名字匹配**；但**结构体 payload 字段 camelCase↔bridge snake_case 不一致（A3）** ❌ |
| `cancel_transcribe` | `ipc.ts:144` | `{ taskId }` | `fn cancel_transcribe(state, task_id: String)` | `taskId`↔`task_id` ✅（Tauri 默认 camelCase） | 名字匹配；**取消无事件收尾（A4）** ❌ |
| `probe_audio` | `ipc.ts:158` | `{ path }` | `fn probe_audio(app, path: String)` | ✅ | 一致 |
| `get_env_info` | `ipc.ts:164` | — | `fn get_env_info(app)` | ✅ | 一致（返回 `data` 已由 Rust 解包） |
| `get_modes` | `ipc.ts:171` | — | `fn get_modes(app)` | ✅ | 一致 |
| `reveal_in_folder` | `ipc.ts:177` | `{ path }` | `fn reveal_in_folder(app, path)` | ✅ | 一致 |
| `open_with_musescore` | `ipc.ts:183` | `{ path }` | `fn open_with_musescore(app, path)` | ✅ | 一致 |
| `check_engine` | `ipc.ts:320` | — | `fn check_engine(app)` | ✅ | 一致；**前端 catch 兜底绕过向导（A5）** ❌ |
| `install_engine` | `ipc.ts:326` | `{ tier }` | `fn install_engine(app, state, tier: Option<String>)` | `tier` ✅ | 一致 |

### 3.2 Events

| event | 前端订阅 | Rust emit 点 | 事件名 | payload 字段 |
|---|---|---|---|---|
| `transcribe://progress` | `ipc.ts:205` | `engine.rs:442` | ✅ 一致 | ❌ Rust 发 `{type,id,...}`，前端读 `taskId`（A1） |
| `transcribe://log` | `ipc.ts:206` | `engine.rs:445`（bridge）、`428/495`（Rust 自造，带 `taskId`） | ✅ | **不一致**：bridge 来的带 `id`（被丢），Rust 自造的带 `taskId`（可用）→ 同一事件两种字段（A1） |
| `transcribe://done` | `ipc.ts:207` | `engine.rs:448` | ✅ | ❌ Rust `{type,id,data}`，前端读 `taskId`+`result`（A1/A2） |
| `transcribe://error` | `ipc.ts:208` | `engine.rs:460` | ✅ | ❌ Rust `{type,id,message,detail}`，前端读 `taskId`（A1） |
| `install://progress` | `ipc.ts:338` | `engine.rs:658` | ✅ | ✅ `{taskId,pct,message}` 一致 |
| `install://log` | `ipc.ts:345` | `engine.rs:668/689` | ✅ | ✅ `{taskId,message}` 一致 |
| `install://done` | `ipc.ts:352` | `engine.rs:674` | ✅ | ✅ `{taskId,lines}` 一致 |

> 关键：**`install://*` 事件 Rust 侧显式构造 payload 并用了 `taskId`，字段正确；而 `transcribe://*` 事件 Rust 侧「原样转发 bridge 的 `&v`」，`id`/`data` 未改名** —— 同一份代码里两种风格，正是不一致的根源。

### 3.3 字段名核对（TranscribeRequest）

| 前端（camelCase，`types.ts:87-108`） | Rust 结构体（`engine.rs:37-70`，`rename_all="camelCase"`） | bridge `allowed`（snake_case，`bridge.py`） | 结论 |
|---|---|---|---|
| `inputPath` | `input_path`→序列化为 `inputPath` | 需要 `input_path` | ❌ bridge 收不到（A3） |
| `extraInputs` | `extra_inputs`→`extraInputs` | 需要 `extra_inputs` | ❌ 同上 |
| `nPeaks/hopLength/onsetThreshold/pitchThreshold/minNoteDuration` | 对应 camelCase | 对应 snake_case | ❌ 同上 |
| `perceptual/simplify/pianoMode/demucsModel/device/allowHpssFallback/trackNames` | 对应 camelCase | 对应 snake_case | ❌ 同上 |
| `mode` | `mode` | `mode` | ✅（唯一幸存字段） |
| `outputPath` | `output_path`→`outputPath` | 需要 `output_path` | ❌ 同上 |

### 3.4 返回值字段核对

| 命令 | 前端读取 | Rust/ bridge 实际 | 结论 |
|---|---|---|---|
| `get_modes` | `{modes, stageLabels}`（`useWorkspace.ts:70-72`） | bridge `data={modes, stageLabels}`，Rust 解包 `data` | ✅ |
| `get_env_info` | `EnvInfo{demucs,cuda,device,ffmpeg,notes}`（`types.ts:53-59`） | bridge `capabilities()` 返回同名字段 | ✅（需确认 `separator.capabilities` 字段名，见第 4 节） |
| `probe_audio` | `ProbeResult{path,name,size,duration,ok}` | bridge `data` 同名 | ✅ |
| `start_transcribe` | `{taskId}`（`types.ts:111-113`） | Rust `StartResult{task_id}`，`rename_all=camelCase`→`taskId` | ✅ |
| `check_engine` | `EngineStatus{engineDir,ready,reason,python,basic,demucs,cuda,torchVersion,mcp,tiers}`（`ipc.ts:287-298`） | bridge/Rust 状态 JSON | ⚠ 需确认 bootstrap `install_status()` 是否提供全部字段（见第 4 节） |

---

## 4. 未确认项（需运行时验证，静态无法定论）

1. **S2 的真实崩溃原因**：`run_once_with` 把子进程 stderr 设为 piped 却从不读取（`engine.rs:241-247`），安装态下 bridge/python 的崩溃 traceback 被丢弃。需在 Rust 侧临时读 stderr（或运行 `python <resource>/engine/bridge.py` 手工喂 `{"cmd":"modes",...}`）以捞取真实堆栈。
2. **安装态 `resolve_engine_python` 实际命中的解释器**：是 `%LOCALAPPDATA%\bapu\engine\.venv`（`engine.rs:193-209`）还是回退到系统 python？需打印该路径确认。
3. **`bootstrap.py --status`（`check_engine` 依赖）在安装态的返回**：`ready` 是真 `true` 还是 `check_installed` 误判（只看包存在、未验证 bridge 能否真正 import）。这决定 S1 命中「catch 分支」还是「ready:false 但引擎实坏」。
4. **`separator.capabilities()` 返回字段名**是否与 `EnvInfo`（`types.ts:53-59`）逐字一致（`demucs/cuda/device/ffmpeg/notes`）——未读该函数体，需确认。
5. **`bootstrap.py install_status()` 返回字段**是否覆盖 `EngineStatus` 全部字段（`engineDir/ready/reason/python/basic/demucs/cuda/torchVersion/mcp/tiers`）——若缺，`EngineSetup` 会读 `undefined`（不崩，但显示不全）。`engine.rs:582-587/601-606` 的兜底分支**只返回 4 个字段**，与 `EngineStatus` 其余字段不一致（虽不致命）。
6. **Tauri 2.12 在安装态是否真的注入 `window.__TAURI_INTERNALS__`**：静态已证 `invoke` 依赖它（`core.js:328`），且用户看到 Rust 文案说明已注入；但若曾出现偶发白屏，可加一条「`isTauri()`（`core.js:478-480`，判据是 `globalThis.isTauri`）双保险」。
7. **A4（取消无事件）在真实交互下的表现**：需实际点「取消任务」验证 UI 是否永久 running（当前被 A1/A2 掩盖，修完 A1/A2 后必现）。

---

## 附：缺陷计数

- **P0：4 条**（A1、A2、A3、隐含于 E1/E2 的同源项）—— 实际独立根因 3 个：①事件 `id↔taskId`；②done `data↔result`；③请求 payload camelCase↔snake_case。
- **P1：4 条**（A4 取消无事件、A5 checkEngine 绕过向导、B1 taskId 竞态、B2 同 A4）
- **P2：9 条** · **P3：6 条**
- **已确认无问题**：`inTauri()` 判据、全部 command 名、全部事件名、`loadError` 显示（历史 bug 已修）、主题切换不重挂。
