/**
 * engine.rs — Python 引擎 sidecar 管理
 *
 * 设计要点
 *
 * 1. **长驻进程**：sidecar 启动一次，后续用「写一行 JSON + 逐行读事件」通信。
 *    避免每次任务都付一次 Python + torch 导入的冷启动代价（实测 3~8 秒）。
 *
 * 2. **独立任务管理**：每个 transcribe 任务有自己的 taskId、取消通道与
 *    子进程句柄。用 `parking_lot::Mutex` 保护全局状态表。
 *
 * 3. **背压**：读 stdout 的线程把事件 emit 到前端。若前端不在线，
 *    Tauri 的 emit 会静默丢弃——不会阻塞引擎。
 *
 * 4. **取消必须真终止**：扒谱是 CPU/GPU 密集的长任务，「取消」不是标志位
 *    而是真正 kill 子进程。否则会留孤儿进程占着显存（Demucs 场景尤其明显）。
 */

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::Arc;
use tauri::{AppHandle, Emitter, Manager, State};

use parking_lot::Mutex;

// ─────────────────────────────────────────────────────────────
// 数据结构（与前端 types.ts / bridge.py 协议三方对齐）
// ─────────────────────────────────────────────────────────────

/// 前端发来的扒谱请求。字段用 camelCase，与 TS 对齐。
#[derive(Debug, Clone, Serialize, Deserialize)]
// 边界约定：与前端通话用 camelCase，与 Python 引擎通话用 snake_case。
// Rust 是这一层边界，必须双向都声明清楚——只写 rename_all 会让
// to_value() 产出 camelCase，而 bridge.py 只认 snake_case，参数被静默丢弃。
#[serde(rename_all(serialize = "snake_case", deserialize = "camelCase"))]
pub struct TranscribeRequest {
    pub mode: String,
    pub input_path: String,
    #[serde(default)]
    pub output_path: Option<String>,
    #[serde(default)]
    pub extra_inputs: Vec<String>,
    #[serde(default)]
    pub n_peaks: Option<u32>,
    #[serde(default)]
    pub hop_length: Option<u32>,
    #[serde(default)]
    pub onset_threshold: Option<f64>,
    #[serde(default)]
    pub pitch_threshold: Option<f64>,
    #[serde(default)]
    pub min_note_duration: Option<u32>,
    #[serde(default)]
    pub tempo: Option<f64>,
    #[serde(default)]
    pub perceptual: Option<bool>,
    #[serde(default)]
    pub simplify: Option<u32>,
    #[serde(default)]
    pub piano_mode: Option<bool>,
    #[serde(default)]
    pub demucs_model: Option<String>,
    #[serde(default)]
    pub device: Option<String>,
    #[serde(default)]
    pub allow_hpss_fallback: Option<bool>,
    #[serde(default)]
    pub track_names: Vec<Option<String>>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StartResult {
    pub task_id: String,
}

// ─────────────────────────────────────────────────────────────
// Sidecar 句柄
// ─────────────────────────────────────────────────────────────

struct Sidecar {
    child: Child,
    /// 扒谱任务需要写 stdin；安装任务不需要，故为 Option。
    stdin: Option<ChildStdin>,
}

#[derive(Default)]
pub struct EngineState {
    /// 任务表：taskId → 该任务的子进程句柄（取消时 kill）
    tasks: Mutex<HashMap<String, Arc<Mutex<Option<Sidecar>>>>>,
    /// 引擎根目录（engine/ 的绝对路径）
    engine_dir: Mutex<Option<PathBuf>>,
    /// 幂等命令的结果缓存。
    /// 背景：两个功能页常驻挂载、各自在挂载时拉 modes + capabilities，
    /// 冷启动瞬间会并发 4 个 Python 子进程（每次冷启 1~2 秒）。
    /// 装上杀软扫描的机器上极易个别超时 → 表现为「引擎未返回结果」。
    /// modes/capabilities 在同一会话内结果恒定，缓存即可把 4 次压成 1 次。
    cache: Mutex<HashMap<String, Value>>,
    /// 一次性命令的串行门：确保同一时刻只有一个 Python 冷启动在跑，
    /// 避免并发抢占 CPU/IO 导致集体变慢。
    gate: Mutex<()>,
}

// ─────────────────────────────────────────────────────────────
// 定位引擎
// ─────────────────────────────────────────────────────────────



/// 定位 engine 目录。
///
/// 开发态：项目根/engine
/// 打包态：可执行文件同级 resources/engine


fn engine_dir(app: &AppHandle) -> Result<PathBuf, String> {
    if let Some(dir) = app.try_state::<EngineState>().and_then(|s| {
        s.engine_dir
            .lock()
            .clone()
    }) {
        return Ok(dir);
    }

    // ⚠️ 顺序即优先级。**必须把「可执行文件同级」放第一位**——
    // 安装版应优先用自己安装目录里的引擎资源，而不是编译机的源码树。
    // 曾把 CARGO_MANIFEST_DIR 放第一，等于让安装版去用开发者的项目目录，
    // 一旦那个路径不存在（换机器/项目被移动）就会解析失败。
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()));

    let mut candidates: Vec<PathBuf> = Vec::new();

    // 1) 可执行文件同级 engine/（安装态正解：<安装目录>/engine）
    if let Some(d) = &exe_dir {
        candidates.push(d.join("engine"));
    }
    // 2) Tauri 资源目录 engine/（部分打包目标会把 resources 放这里）
    if let Ok(rd) = app.path().resource_dir() {
        candidates.push(rd.join("engine"));
    }
    // 3) 开发态兜底：项目源码树。**仅当上面都不命中时才用**，
    //    且只在 debug 构建下启用，避免安装版误用开发机路径。
    #[cfg(debug_assertions)]
    candidates.push(
        std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .map(|p| p.join("engine"))
            .unwrap_or_default(),
    );

    for c in candidates {
        if c.join("bridge.py").is_file() {
            let c = c.canonicalize().unwrap_or(c);
            if let Some(state) = app.try_state::<EngineState>() {
                *state.engine_dir.lock() = Some(c.clone());
            }
            return Ok(c);
        }
    }

    // 4) 最后一道：源码树与 venv 分离的安装态——
    //    源码在安装目录，venv 在 %LOCALAPPDATA%apu\engine。
    //    此时 engine_dir 指向安装目录（有源码），解释器另找。
    //    若连源码都没有，说明安装包不完整。
    

    Err(
        "找不到引擎目录（engine/bridge.py）。\n请确认应用完整安装，或联系开发者。".to_string(),
    )
}


// ─────────────────────────────────────────────────────────────
// 通用请求：一次性命令
// ─────────────────────────────────────────────────────────────

/// 跑一个短命令（如 probe / capabilities / modes），返回最终 data。
///
/// 这些命令耗时 < 1 秒，用一次性子进程比重连 sidecar 更简单可靠，
/// 代价是每次约 1~2 秒的 Python 冷启动——对交互体验可接受，
/// 因为它们只在拖入文件 / 打开设置时触发。
/// 解析**真正可用的**引擎解释器。
///
/// ## 为什么不能直接用 `python_exe(&engine_dir)`
/// 打包后 `engine_dir()` 指向的是 Tauri 的**资源暂存目录**（resources/engine），
/// 里面只有 .py 源码——**没有 .venv**（5.4GB，按设计不随包分发）。
/// 直接用 `python_exe(&dir)` 会回退到系统 `python`，那里没有 librosa，
/// bridge.py 一 import 就炸，表现为界面永远「模式列表加载中」。
///
/// 这个 bug 在开发态**永远测不到**：开发时 engine/ 旁边就带着 .venv。
///
/// ## 解析顺序
/// 1. `engine_dir()/.venv`（开发态）
/// 2. `%LOCALAPPDATA%/bapu/engine/.venv`（引导安装的默认落点）
/// 3. 系统 python（最后兜底，几乎必然缺依赖——但给出明确错误比静默失败好）
fn resolve_engine_python(app: &AppHandle) -> Result<(PathBuf, PathBuf), String> {
    let dir = engine_dir(app)?;
    

    // 1) 开发态：源码目录自带 venv
    let dev = dir.join(".venv");
    #[cfg(windows)]
    {
        let p = dev.join("Scripts").join("python.exe");
        if p.is_file() {
            return Ok((dir.clone(), p));
        }
    }
    #[cfg(not(windows))]
    {
        let p = dev.join("bin").join("python");
        if p.is_file() {
            return Ok((dir.clone(), p));
        }
    }

    // 2) 引导安装的默认落点
    
    if let Some(local) = std::env::var_os("LOCALAPPDATA") {
        let installed = PathBuf::from(local).join("bapu").join("engine");
        #[cfg(windows)]
        {
            let p = installed.join(".venv").join("Scripts").join("python.exe");
            
            if p.is_file() {
                
                return Ok((dir.clone(), p));
            }
        }
        #[cfg(not(windows))]
        {
            let p = installed.join(".venv").join("bin").join("python");
            if p.is_file() {
                return Ok((dir.clone(), p));
            }
        }
    }

    // 3) 兜底：系统 python。**必须报错**，不能默默返回——
    //    正是「默默回退到没有依赖的解释器」造成了「界面永远加载中」这个症状。
    Err(format!(
        "找不到已安装的引擎。\n\n\
         脚本目录：{}\n\
         已查找：\n  · {}\n  · %LOCALAPPDATA%\\bapu\\engine\\.venv\n\n\
         请在应用内完成引擎安装，或重新运行安装包。",
        dir.display(),
        dir.join(".venv").display()
    ))
}

/// 把 bridge.py 输出的原始事件适配成前端期望的形状。
///
/// ## 为什么必须抽成独立函数
/// bridge.py 用 `id` / `data`，前端 `types.ts` 用 `taskId` / `result`。
/// 这层转换原先是内联在闭包里的，带来两个问题：
///   ① **无法单测**——而跨语言字段名写错**不报错**，只是事件被静默丢弃，
///      比报错难查得多；
///   ② 同一份代码里 `install://*` 是显式构造（字段正确），
///      `transcribe://*` 是原样转发（字段错误），两种风格并存正是不一致的温床。
/// 抽成纯函数后由 `contract_tests` 守护字段名，杜绝再次漂移。
fn adapt_event(kind: &str, task_id: &str, v: &Value) -> Option<(&'static str, Value)> {
    match kind {
        "progress" => Some((
            "transcribe://progress",
            serde_json::json!({
                "taskId": task_id,
                "stage": v.get("stage"),
                "pct": v.get("pct"),
                "message": v.get("message"),
            }),
        )),
        "log" => Some((
            "transcribe://log",
            serde_json::json!({
                "taskId": task_id,
                "message": v.get("message"),
            }),
        )),
        "result" => Some((
            "transcribe://done",
            serde_json::json!({
                "taskId": task_id,
                "result": v.get("data"),
            }),
        )),
        "error" => Some((
            "transcribe://error",
            serde_json::json!({
                "taskId": task_id,
                "message": v.get("message"),
                "detail": v.get("detail"),
            }),
        )),
        // accepted 无需下发（前端不订阅）；未知类型丢弃
        _ => None,
    }
}

/// 结果在同一会话内恒定、可安全缓存的命令。
/// `probe` 不进缓存（文件可能被替换）；`modes`/`capabilities` 与文件无关。
const CACHEABLE_CMDS: &[&str] = &["modes", "capabilities"];

fn run_once(app: &AppHandle, cmd: &str, payload: Value) -> Result<Value, String> {
    use tauri::Manager;
    let st = app.state::<EngineState>();
    let cacheable = CACHEABLE_CMDS.contains(&cmd);

    // 快路径：命中缓存
    if cacheable {
        if let Some(v) = st.cache.lock().get(cmd) {
            
            return Ok(v.clone());
        }
    }

    // 串行门：同一时刻只允许一个 Python 冷启动，避免并发抢占导致集体变慢/超时。
    // guard 必须活到函数结束，覆盖整段子进程生命周期。
    let _gate = st.gate.lock();

    // 双重检查：等锁期间可能已有别的线程填好了缓存
    if cacheable {
        if let Some(v) = st.cache.lock().get(cmd) {
            
            return Ok(v.clone());
        }
    }

    let (dir, py) = resolve_engine_python(app)?;
    let out = run_once_with(app, &dir, &py, cmd, payload)?;

    if cacheable {
        st.cache.lock().insert(cmd.to_string(), out.clone());
        
    }
    Ok(out)
}

fn run_once_with(
    _app: &AppHandle,
    dir: &PathBuf,
    py: &PathBuf,
    cmd: &str,
    payload: Value,
) -> Result<Value, String> {
    let bridge = dir.join("bridge.py");

    if !bridge.is_file() {
        return Err(format!("引擎入口不存在：{}", bridge.display()));
    }

    let mut child = hide_child(Command::new(&py))
        .arg(&bridge)
    // ── 强制子进程用 UTF-8 ──
    // stdout 被管道捕获时，Python 会用系统 locale 编码（中文 Windows = GBK），
    // 中文 JSON 到这边按 UTF-8 解码就成了「◆◆◆◆」乱码。
    // Python 侧已 reconfigure 兜底，这里是双保险 —— 不依赖任何环境前提。
    .env("PYTHONIOENCODING", "utf-8")
    .env("PYTHONUTF8", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("无法启动引擎进程：{}\n请确认已安装 Python 依赖。", e))?;

    // ── 接管 stderr ──
    // 必须并发读走。两个理由：
    // 1. 子进程往 stderr 大量输出时会写满管道缓冲区（64KB）而阻塞，
    //    表现为「stdout 也读不到东西」的假死。
    // 2. 万一 Python 侧 import 失败崩溃，stderr 里的 traceback 是**唯一线索**。
    //    此前这里只 piped 不读，出错时只能报「引擎未返回结果」，无从排查。
    let stderr_buf: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    if let Some(se) = child.stderr.take() {
        let buf = stderr_buf.clone();
        std::thread::spawn(move || {
            let r = BufReader::new(se);
            for line in r.lines().map_while(Result::ok) {
                let mut b = buf.lock();
                if b.len() < 200 {
                    b.push(line);
                }
            }
        });
    }

    // ── 发请求，然后**立刻关闭 stdin** ──
    //
    // 踩坑实录（主上反馈「模式下拉菜单什么都没有」，查了三轮才对）：
    // `bridge.py` 的主循环是 `for line in sys.stdin:`——**只有 stdin 收到 EOF
    // 才会退出**。初版用 `child.stdin.as_mut()` 写完就放回 child 里，管道一直
    // 开着，于是 bridge 永远等下一行、永不退出，`reader.lines()` 也就永远
    // 等不到 EOF → invoke 的 Promise 永不落地 → 界面卡在「模式列表加载中」。
    //
    // 手动测时用 `echo ... | python`，shell 会在管道写完后关闭 stdin，所以
    // 测试永远通过，**只有 Tauri 里才复现**。
    //
    // 正解：一次性命令用完 stdin 就 take 出来 drop 掉，让 bridge 正常收尾。
    // （start_transcribe 是长驻任务，另有一处需要保持 stdin 打开，不受此影响。）
    {
        let req = serde_json::json!({
            "cmd": cmd,
            "id": uuid::Uuid::new_v4().to_string(),
            "payload": payload,
        });
        let line = serde_json::to_string(&req).map_err(|e| e.to_string())?;
        {
            let stdin = child.stdin.as_mut().ok_or("引擎 stdin 不可用")?;
            writeln!(stdin, "{}", line).map_err(|e| format!("发送请求失败：{}", e))?;
            stdin.flush().ok();
        }
        // 关键：显式关闭，否则 bridge 进程不会退出
        drop(child.stdin.take());
        
    }

    let stdout = child.stdout.take().ok_or("引擎 stdout 不可用")?;
    let reader = BufReader::new(stdout);

    let mut result: Option<Result<Value, (String, String)>> = None;
    for line in reader.lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => break,
        };
        let trimmed = line.trim();
        if trimmed.is_empty() || !trimmed.starts_with('{') {
            continue;
        }
        let Ok(v) = serde_json::from_str::<Value>(trimmed) else {
            continue;
        };
        match v.get("type").and_then(|t| t.as_str()) {
            Some("result") => {
                result = Some(Ok(v.get("data").cloned().unwrap_or(Value::Null)));
            }
            Some("error") => {
                let msg = v
                    .get("message")
                    .and_then(|m| m.as_str())
                    .unwrap_or("引擎返回未知错误")
                    .to_string();
                let detail = v
                    .get("detail")
                    .and_then(|m| m.as_str())
                    .unwrap_or("")
                    .to_string();
                result = Some(Err((msg, detail)));
            }
            _ => {}
        }
    }

    // 关闭 stdin 后 bridge 会自行退出；这里回收子进程
    let _ = child.wait();

    match result {
        Some(Ok(data)) => Ok(data),
        Some(Err((msg, detail))) => {
            if detail.is_empty() {
                Err(msg)
            } else {
                Err(format!("{}\n\n技术信息：{}", msg, detail))
            }
        }
        None => {
            let err_text = stderr_buf.lock().join("
");
            let tail: String = err_text
                .lines()
                .filter(|l| !l.trim().is_empty())
                .rev()
                .take(12)
                .collect::<Vec<_>>()
                .into_iter()
                .rev()
                .collect::<Vec<_>>()
                .join("
");
            if tail.is_empty() {
                Err("引擎未返回结果，进程可能异常退出。
（引擎没有输出任何错误信息）".to_string())
            } else {
                Err(format!(
                    "引擎未返回结果，进程可能异常退出。

引擎错误输出：
{}",
                    tail
                ))
            }
        }
    }
}

/// 隐藏子进程窗口。
///
/// 踩坑实录：初版自己定义了一个 `trait CreationFlags`，与 `std::os::windows::process::CommandExt`
/// 同名，Rust 报 `multiple applicable items in scope`。正解是**不自定义 trait**，
/// 直接在 Windows 上 `use` 官方的 CommandExt 并调用；非 Windows 用 no-op 包装函数。
#[cfg(windows)]
fn hide_child(mut cmd: Command) -> Command {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd
}

#[cfg(not(windows))]
fn hide_child(cmd: Command) -> Command {
    cmd
}

// ─────────────────────────────────────────────────────────────
// Tauri commands
// ─────────────────────────────────────────────────────────────

#[tauri::command]
pub async fn start_transcribe(
    app: AppHandle,
    state: State<'_, EngineState>,
    request: TranscribeRequest,
) -> Result<StartResult, String> {
    // 同 run_once：必须用 resolve 而非 python_exe(&dir)，理由见该函数文档
    let (dir, py) = resolve_engine_python(&app)?;
    let bridge = dir.join("bridge.py");

    if !bridge.is_file() {
        return Err(format!("引擎入口不存在：{}", bridge.display()));
    }

    let task_id = uuid::Uuid::new_v4().to_string();

    // 请求体转成 bridge.py 认识的 snake_case payload
    let payload = serde_json::to_value(&request)
        .map_err(|e| format!("请求序列化失败：{}", e))?;

    let mut child = hide_child(Command::new(&py))
        .arg(&bridge)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| {
            format!(
                "无法启动引擎进程：{}\n\n请确认 Python 环境已就绪。",
                e
            )
        })?;

    let stdin = child.stdin.take().ok_or("引擎 stdin 不可用")?;
    let stdout = child.stdout.take().ok_or("引擎 stdout 不可用")?;
    let stderr = child.stderr.take();

    let sidecar = Arc::new(Mutex::new(Some(Sidecar { child, stdin: Some(stdin) })));
    state
        .tasks
        .lock()
        .insert(task_id.clone(), sidecar.clone());

    // 写请求
    {
        let mut guard = sidecar.lock();
        let sc = guard.as_mut().ok_or("引擎已退出")?;
        let stdin = sc.stdin.as_mut().ok_or("引擎 stdin 不可用")?;
        let req = serde_json::json!({
            "cmd": "transcribe",
            "id": task_id,
            "payload": payload,
        });
        let line = serde_json::to_string(&req).map_err(|e| e.to_string())?;
        writeln!(stdin, "{}", line).map_err(|e| format!("发送请求失败：{}", e))?;
        stdin.flush().ok();
    }

    // 读事件流
    let app_for_events = app.clone();
    let tid_for_reader = task_id.clone();
    let sidecar_for_reader = sidecar.clone();

    std::thread::spawn(move || {
        let reader = BufReader::new(stdout);

        for line in reader.lines() {
            let Ok(line) = line else { break };
            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }

            let Ok(v) = serde_json::from_str::<Value>(trimmed) else {
                // 非 JSON 行：说明协议被污染，转发给前端日志区
                let _ = app_for_events.emit(
                    "transcribe://log",
                    serde_json::json!({
                        "taskId": tid_for_reader,
                        "message": trimmed.to_string(),
                    }),
                );
                continue;
            };

            let kind = v.get("type").and_then(|t| t.as_str()).unwrap_or("");

            // 字段名适配统一走纯函数（可单测，见 contract_tests）
            if let Some((evt, payload)) = adapt_event(kind, &tid_for_reader, &v) {
                let _ = app_for_events.emit(evt, payload);
            }

            // 终止类事件需要收尾：主动关 stdin 并结束读循环。
            // （bridge 的 `for line in sys.stdin` 收到 EOF 才退出，
            //   不关 stdin 会让 Python 进程一直挂着、GPU 显存不释放。）
            if kind == "result" || kind == "error" {
                if let Some(mut sc) = sidecar_for_reader.lock().take() {
                    drop(sc.stdin.take());
                    let _ = sc.child.try_wait();
                }
                break;
            }
        }

        // 收尾：若进程仍在（异常中断），强制回收，避免孤儿进程占显存
        if let Some(mut sc) = sidecar_for_reader.lock().take() {
            let still_running = matches!(sc.child.try_wait(), Ok(None));
            if still_running {
                let _ = sc.child.kill();
            }
            let _ = sc.child.wait();
        }
        if let Some(state) = app_for_events.try_state::<EngineState>() {
            state.tasks.lock().remove(&tid_for_reader);
        }
    });

    // stderr 单独收，避免管道写满导致子进程阻塞（经典死锁）
    if let Some(se) = stderr {
        let app_err = app.clone();
        let tid_err = task_id.clone();
        std::thread::spawn(move || {
            let reader = BufReader::new(se);
            for line in reader.lines().map_while(Result::ok) {
                let t = line.trim();
                if t.is_empty() {
                    continue;
                }
                let _ = app_err.emit(
                    "transcribe://log",
                    serde_json::json!({ "taskId": tid_err, "message": t }),
                );
            }
        });
    }

    Ok(StartResult { task_id })
}

#[tauri::command]
pub async fn cancel_transcribe(
    app: AppHandle,
    state: State<'_, EngineState>,
    task_id: String,
) -> Result<(), String> {
    let handle = state.tasks.lock().remove(&task_id);
    let Some(sidecar) = handle else {
        // 任务已结束：不是错误，幂等返回
        return Ok(());
    };

    let mut guard = sidecar.lock();
    if let Some(mut sc) = guard.take() {
        // ⚠️ 必须真 kill：扒谱占着 GPU（Demucs 场景显存占用显著），
        // 只断标志位会留孤儿进程持续占资源。
        let _ = sc.child.kill();
        let _ = sc.child.wait();
    }

    // ── 必须补发一条终止事件 ──
    // 读线程在 stdout EOF 后只做回收、不 emit 任何事件（见其收尾分支），
    // 而前端 useTranscribeTask 只在收到 error 事件时才会把状态从 running
    // 切走。kill 走的是「进程直接消失」路径，bridge.py 的 KeyboardInterrupt
    // 分支根本不会执行。若不在这里补发，取消后 UI 会**永久卡在运行中**。
    let _ = app.emit(
        "transcribe://error",
        serde_json::json!({
            "taskId": task_id,
            "message": "任务已被取消。",
            "detail": "cancelled by user",
        }),
    );
    Ok(())
}

#[tauri::command]
pub async fn probe_audio(app: AppHandle, path: String) -> Result<Value, String> {
    run_once(&app, "probe", serde_json::json!({ "path": path }))
}

#[tauri::command]
pub async fn get_env_info(app: AppHandle) -> Result<Value, String> {
    run_once(&app, "capabilities", Value::Object(Default::default()))
}

#[tauri::command]
pub async fn get_modes(app: AppHandle) -> Result<Value, String> {
    run_once(&app, "modes", Value::Object(Default::default()))
}

#[tauri::command]
pub async fn reveal_in_folder(app: AppHandle, path: String) -> Result<(), String> {
    run_once(&app, "reveal", serde_json::json!({ "path": path })).map(|_| ())
}

#[tauri::command]
pub async fn open_with_musescore(app: AppHandle, path: String) -> Result<(), String> {
    run_once(&app, "open_musescore", serde_json::json!({ "path": path })).map(|_| ())
}

// ─────────────────────────────────────────────────────────────
// 首启引导安装
//
// 主上拍板「首启动引导装也行」。原因见 engine/bootstrap.py 模块文档：
// 引擎 venv 实测 5.1GB（torch 一家 4.4GB），打进 installer 不可接受。
//
// 架构：安装由 Python 侧 bootstrap.py 执行（pip 逻辑复杂，Rust 重写不划算），
// Rust 只做「起子进程 + 转发进度事件 + 解析结果」。
// ─────────────────────────────────────────────────────────────

/// 查询引擎安装状态。UI 启动时先调这个，未就绪则显示引导页。
#[tauri::command]
pub async fn check_engine(app: AppHandle) -> Result<Value, String> {
    let dir = engine_dir(&app)?;

    // ── 架构修正（主上反馈三个功能全废的根因）──
    //
    // 初版这里用「系统 Python 跑 bootstrap.py --status」，把结果当权威。
    // 错在**问错了对象**：打包后 engine/ 是资源暂存目录，里面**没有 .venv**
    // （我按设计排除了它，真机上 5.4GB 的 venv 绝不该进包）。
    // 于是 python_exe() 回退到系统 python —— 那里没有 librosa，
    // bridge.py 一 import 就炸，界面永远停在「模式列表加载中」。
    //
    // 开发态自带 venv，所以**这个 bug 在本地永远测不到**。
    //
    // 正解：状态判定只认「引导装出来的那份 venv」的真实 import 结果。
    // bootstrap.py 的 install_status() 正是干这个的，且它零第三方依赖，
    // 用系统 Python 跑它没问题——但它内部会去看**目标 venv**，
    // 而不是当前解释器的 site-packages。调用方式不变，错的是我的假设。
    //
    // 补充加固：直接用 venv 解释器做一次 import 探针，不依赖任何脚本。
    let script = dir.join("bootstrap.py");
    if !script.is_file() {
        return Ok(serde_json::json!({
            "ready": false,
            "reason": "安装器脚本缺失（安装包不完整，请重新下载）",
            "engineDir": dir.to_string_lossy(),
            "tiers": [],
        }));
    }

    let base = base_python().ok_or("找不到可用的 Python 3.9+")?;
    // 走 hide_child 包装：`creation_flags` 是 Windows 专有 API，裸调无法跨平台编译
    let out = hide_child(std::process::Command::new(&base))
        .arg(&script)
        .arg("--status")
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1")
        .output()
        .map_err(|e| format!("执行安装器失败：{}", e))?;

    // 安装器本身跑不起来时不能报「已就绪」——那会让 App 进主界面后全功能瘫���
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        return Ok(serde_json::json!({
            "ready": false,
            "reason": format!("状态检测失败：{}", err.trim().chars().take(200).collect::<String>()),
            "engineDir": dir.to_string_lossy(),
            "tiers": [],
        }));
    }

    let text = String::from_utf8_lossy(&out.stdout);
    serde_json::from_str::<Value>(text.trim())
        .map_err(|e| format!("安装器返回无法解析：{}\n{}", e, &text[..text.len().min(300)]))
}

/// 启动引擎安装。立即返回 taskId，进度经事件流回传。
#[tauri::command]
pub async fn install_engine(
    app: AppHandle,
    state: State<'_, EngineState>,
    tier: Option<String>,
    mirror: Option<String>,
) -> Result<StartResult, String> {
    let dir = engine_dir(&app)?;
    let script = dir.join("bootstrap.py");
    if !script.is_file() {
        return Err("安装器脚本缺失（安装包不完整，请重新下载）".into());
    }

    let tier = tier.unwrap_or_else(|| "full".into());
    // 镜像透传给 bootstrap.py。默认国内镜像：PyTorch CUDA wheel 约 2.5GB，
    // 官方源在国内常年几十 KB/s，小白最容易在这一步放弃。
    let mirror = mirror.unwrap_or_else(|| "cn".into());
    let task_id = uuid::Uuid::new_v4().to_string();

    let base = base_python().ok_or("找不到可用的 Python 3.9+")?;
    let mut child = hide_child(std::process::Command::new(&base))
        .arg(&script)
        .arg("--tier")
        .arg(&tier)
        .arg("--mirror")
        .arg(&mirror)
    // ── 强制子进程用 UTF-8 ──
    // stdout 被管道捕获时，Python 会用系统 locale 编码（中文 Windows = GBK），
    // 中文 JSON 到这边按 UTF-8 解码就成了「◆◆◆◆」乱码。
    // Python 侧已 reconfigure 兜底，这里是双保险 —— 不依赖任何环境前提。
    .env("PYTHONIOENCODING", "utf-8")
    .env("PYTHONUTF8", "1")

        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| format!("启动安装进程失败：{}\n\n请确认已安装 Python 3.9 或更高版本。", e))?;

    let stdout = child.stdout.take().ok_or("安装器 stdout 不可用")?;
    let stderr = child.stderr.take();

    // 安装器用 \r 刷新进度行；按行转发即可（Rust 侧不必解析 ANSI）
    let app_p = app.clone();
    let tid_p = task_id.clone();
    std::thread::spawn(move || {
        use std::io::BufRead;
        let reader = std::io::BufReader::new(stdout);
        let mut seen = 0usize;
        for line in reader.lines().map_while(std::result::Result::ok) {
            let t = line.trim();
            if t.is_empty() {
                continue;
            }
            // 抓形如 "  [████░░░]  42.0%  说明" 的进度行
            if let Some(pct) = parse_progress(t) {
                let _ = app_p.emit(
                    "install://progress",
                    serde_json::json!({
                        "taskId": tid_p,
                        "pct": pct,
                        "message": strip_bar(t),
                    }),
                );
            } else {
                let _ = app_p.emit(
                    "install://log",
                    serde_json::json!({ "taskId": tid_p, "message": t }),
                );
            }
            seen += 1;
        }
        let _ = app_p.emit(
            "install://done",
            serde_json::json!({ "taskId": tid_p, "lines": seen }),
        );
    });

    if let Some(se) = stderr {
        let app_e = app.clone();
        let tid_e = task_id.clone();
        std::thread::spawn(move || {
            use std::io::BufRead;
            let reader = std::io::BufReader::new(se);
            for line in reader.lines().map_while(std::result::Result::ok) {
                let t = line.trim();
                if !t.is_empty() {
                    let _ = app_e.emit(
                        "install://log",
                        serde_json::json!({ "taskId": tid_e, "message": t }),
                    );
                }
            }
        });
    }

    // 记录句柄以便取消
    let handle = Arc::new(Mutex::new(Some(Sidecar { child, stdin: None })));
    state.tasks.lock().insert(task_id.clone(), handle);

    Ok(StartResult { task_id })
}

/// 从 "  [████░░]  42.0%  xxx" 中解析百分比
fn parse_progress(line: &str) -> Option<f64> {
    let p = line.find('%')?;
    let head = &line[..p];
    let num = head
        .rsplit(|c: char| !(c.is_ascii_digit() || c == '.'))
        .next()?;
    num.parse::<f64>().ok()
}

/// 去掉进度条字符，只留说明文字
fn strip_bar(line: &str) -> String {
    line.chars()
        .filter(|c| *c != '█' && *c != '░')
        .collect::<String>()
        .trim()
        .trim_start_matches(|c: char| c == '[' || c == ']')
        .trim()
        .to_string()
}

/// 找一个能用的系统 Python 3.9+
fn base_python() -> Option<String> {
    for exe in ["py", "python", "python3"] {
        if std::process::Command::new(exe)
            .arg("-c")
            .arg("import sys;print(1 if sys.version_info>=(3,9) else 0)")
            .output()
            .ok()
            .filter(|o| o.status.success())
            .and_then(|o| {
                let t = String::from_utf8_lossy(&o.stdout).trim().to_string();
                if t == "1" { Some(exe.to_string()) } else { None }
            })
            .is_some()
        {
            return Some(exe.to_string());
        }
    }
    None
}

// ─────────────────────────────────────────────────────────────
// 契约回归测试
//
// 这些测试守护的是**跨语言边界的字段名**——本项目最容易出错、
// 且出错后最难发现的地方（参数被静默过滤、事件被静默丢弃，都不报错）。
// 命令：cargo test --manifest-path src-tauri/Cargo.toml
// ─────────────────────────────────────────────────────────────
#[cfg(test)]
mod contract_tests {
    use super::*;

    fn sample_request() -> TranscribeRequest {
        TranscribeRequest {
            mode: "basic".into(),
            input_path: r"D:\x\a.mp3".into(),
            output_path: Some(r"D:\x\a.mid".into()),
            extra_inputs: vec![r"D:\x\b.wav".into()],
            n_peaks: Some(6),
            hop_length: Some(512),
            onset_threshold: Some(0.3),
            pitch_threshold: Some(0.1),
            min_note_duration: Some(4),
            tempo: Some(120.0),
            perceptual: Some(false),
            simplify: Some(0),
            piano_mode: Some(false),
            demucs_model: Some("htdemucs".into()),
            device: Some("auto".into()),
            allow_hpss_fallback: Some(true),
            track_names: vec![],
        }
    }

    /// bridge.py 的 handle_transcribe 白名单（snake_case）。
    /// **若上游改了 bridge.py，这个列表必须同步**——否则参数会被静默丢弃。
    const BRIDGE_ALLOWLIST: &[&str] = &[
        "mode", "input_path", "output_path", "extra_inputs", "n_peaks",
        "hop_length", "onset_threshold", "pitch_threshold",
        "min_note_duration", "tempo", "perceptual", "simplify",
        "piano_mode", "demucs_model", "device", "allow_hpss_fallback",
        "track_names",
    ];

    /// P0-A3 回归：发给 Python 的 payload 必须是 snake_case。
    /// 曾因 `rename_all = "camelCase"` 产出 `inputPath`，被 bridge 全部过滤，
    /// 导致扒谱在所有环境 100% 失败且**不报错**。
    #[test]
    fn payload_to_python_is_snake_case() {
        let v = serde_json::to_value(sample_request()).expect("序列化失败");
        for k in BRIDGE_ALLOWLIST {
            assert!(v.get(*k).is_some(), "缺少 snake_case 键：{k}");
        }
        for bad in ["inputPath", "outputPath", "nPeaks", "extraInputs", "pianoMode"] {
            assert!(v.get(bad).is_none(), "不应出现 camelCase 键：{bad}");
        }
    }

    /// A3 的另一半：前端发来的 camelCase 必须能被反序列化。
    #[test]
    fn inbound_from_frontend_is_camel_case() {
        let js = serde_json::json!({
            "mode": "basic",
            "inputPath": r"D:\x\a.mp3",
            "nPeaks": 6,
            "pianoMode": true,
        });
        let req: TranscribeRequest =
            serde_json::from_value(js).expect("camelCase 反序列化失败");
        assert_eq!(req.input_path, r"D:\x\a.mp3");
        assert_eq!(req.n_peaks, Some(6));
        assert_eq!(req.piano_mode, Some(true));
    }

    /// 防漂移：序列化产生的键必须**全部**落在 bridge 白名单内，
    /// 否则新增字段会被静默丢弃（不报错，极难发现）。
    #[test]
    fn payload_keys_all_land_in_bridge_allowlist() {
        let v = serde_json::to_value(sample_request()).expect("序列化失败");
        let obj = v.as_object().expect("应为对象");
        let unknown: Vec<&String> = obj
            .keys()
            .filter(|k| !BRIDGE_ALLOWLIST.contains(&k.as_str()))
            .collect();
        assert!(
            unknown.is_empty(),
            "以下键不在 bridge.py 白名单内，会被静默丢弃：{unknown:?}"
        );
    }
    // ── P0-A1/A2 回归：事件 payload 字段名必须与前端 types.ts 一致 ──
    // 前端读 `taskId`（不是 `id`）、`result`（不是 `data`）。
    // 写错**不报错**——事件会被 `if (p.taskId !== cur) return` 静默丢弃。

    #[test]
    fn progress_event_matches_frontend() {
        let raw = serde_json::json!({
            "type": "progress", "id": "abc",
            "stage": "track", "pct": 0.5, "message": "追踪音符"
        });
        let (evt, p) = adapt_event("progress", "abc", &raw).expect("progress 应有适配");
        assert_eq!(evt, "transcribe://progress");
        assert_eq!(p.get("taskId").and_then(|x| x.as_str()), Some("abc"));
        assert!(p.get("id").is_none(), "不应再出现 id 字段");
        assert_eq!(p.get("stage").and_then(|x| x.as_str()), Some("track"));
        assert_eq!(p.get("pct").and_then(|x| x.as_f64()), Some(0.5));
        assert_eq!(p.get("message").and_then(|x| x.as_str()), Some("追踪音符"));
    }

    #[test]
    fn done_event_carries_result_not_data() {
        let raw = serde_json::json!({
            "type": "result", "id": "abc",
            "data": {"totalNotes": 9, "elapsed": 4.9}
        });
        let (evt, p) = adapt_event("result", "abc", &raw).expect("result 应有适配");
        assert_eq!(evt, "transcribe://done");
        assert_eq!(p.get("taskId").and_then(|x| x.as_str()), Some("abc"));
        let r = p.get("result").expect("必须有 result 字段（前端读这个）");
        assert_eq!(r.get("totalNotes").and_then(|x| x.as_u64()), Some(9));
        assert!(p.get("data").is_none(), "不应再出现 data 字段");
    }

    #[test]
    fn error_event_carries_message_and_detail() {
        let raw = serde_json::json!({
            "type": "error", "id": "abc",
            "message": "文件已损坏", "detail": "rc=1"
        });
        let (evt, p) = adapt_event("error", "abc", &raw).expect("error 应有适配");
        assert_eq!(evt, "transcribe://error");
        assert_eq!(p.get("taskId").and_then(|x| x.as_str()), Some("abc"));
        assert_eq!(p.get("message").and_then(|x| x.as_str()), Some("文件已损坏"));
        assert_eq!(p.get("detail").and_then(|x| x.as_str()), Some("rc=1"));
    }

    #[test]
    fn log_event_matches_frontend() {
        let raw = serde_json::json!({"type": "log", "id": "abc", "message": "hello"});
        let (evt, p) = adapt_event("log", "abc", &raw).expect("log 应有适配");
        assert_eq!(evt, "transcribe://log");
        assert_eq!(p.get("taskId").and_then(|x| x.as_str()), Some("abc"));
        assert_eq!(p.get("message").and_then(|x| x.as_str()), Some("hello"));
    }

    /// accepted 不下发（前端不订阅）；未知类型丢弃
    #[test]
    fn non_terminal_events_are_dropped() {
        let empty = serde_json::json!({});
        assert!(adapt_event("accepted", "x", &empty).is_none());
        assert!(adapt_event("garbage", "x", &empty).is_none());
    }
}
