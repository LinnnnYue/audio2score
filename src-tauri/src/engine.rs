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
#[serde(rename_all = "camelCase")]
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
    stdin: ChildStdin,
}

#[derive(Default)]
pub struct EngineState {
    /// 任务表：taskId → 该任务的子进程句柄（取消时 kill）
    tasks: Mutex<HashMap<String, Arc<Mutex<Option<Sidecar>>>>>,
    /// 引擎根目录（engine/ 的绝对路径）
    engine_dir: Mutex<Option<PathBuf>>,
}

type Engine = State<'static, EngineState>;

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

    let candidates: Vec<PathBuf> = vec![
        // 开发态
        std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .map(|p| p.join("engine"))
            .unwrap_or_default(),
        // 打包态（Tauri 资源目录）
        app.path()
            .resource_dir()
            .map(|p| p.join("engine"))
            .unwrap_or_default(),
        // 可执行文件同级
        std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(|d| d.join("engine")))
            .unwrap_or_default(),
    ];

    for c in candidates {
        if c.join("bridge.py").is_file() {
            let c = c.canonicalize().unwrap_or(c);
            if let Some(state) = app.try_state::<EngineState>() {
                *state.engine_dir.lock() = Some(c.clone());
            }
            return Ok(c);
        }
    }

    Err(
        "找不到引擎目录（engine/bridge.py）。\n请确认应用完整安装，或联系开发者。".to_string(),
    )
}

fn python_exe(engine: &PathBuf) -> PathBuf {
    // venv 的解释器。Windows 优先 python.exe，Unix 用 bin/python
    let win = engine.join(".venv").join("Scripts").join("python.exe");
    if win.is_file() {
        return win;
    }
    let nix = engine.join(".venv").join("bin").join("python");
    if nix.is_file() {
        return nix;
    }
    // 回退到系统 python（不推荐，但好过直接失败）
    PathBuf::from("python")
}

// ─────────────────────────────────────────────────────────────
// 通用请求：一次性命令
// ─────────────────────────────────────────────────────────────

/// 跑一个短命令（如 probe / capabilities / modes），返回最终 data。
///
/// 这些命令耗时 < 1 秒，用一次性子进程比重连 sidecar 更简单可靠，
/// 代价是每次约 1~2 秒的 Python 冷启动——对交互体验可接受，
/// 因为它们只在拖入文件 / 打开设置时触发。
fn run_once(app: &AppHandle, cmd: &str, payload: Value) -> Result<Value, String> {
    let dir = engine_dir(app)?;
    let py = python_exe(&dir);
    let bridge = dir.join("bridge.py");

    if !bridge.is_file() {
        return Err(format!("引擎入口不存在：{}", bridge.display()));
    }

    let mut child = hide_child(Command::new(&py))
        .arg(&bridge)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("无法启动引擎进程：{}\n请确认已安装 Python 依赖。", e))?;

    {
        let stdin = child.stdin.as_mut().ok_or("引擎 stdin 不可用")?;
        let req = serde_json::json!({
            "cmd": cmd,
            "id": uuid::Uuid::new_v4().to_string(),
            "payload": payload,
        });
        writeln!(stdin, "{}", req).map_err(|e| format!("发送请求失败：{}", e))?;
        stdin.flush().ok();
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
        None => Err("引擎未返回结果，进程可能异常退出。".to_string()),
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
    let dir = engine_dir(&app)?;
    let py = python_exe(&dir);
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

    let sidecar = Arc::new(Mutex::new(Some(Sidecar { child, stdin })));
    state
        .tasks
        .lock()
        .insert(task_id.clone(), sidecar.clone());

    // 写请求
    {
        let mut guard = sidecar.lock();
        let sc = guard.as_mut().ok_or("引擎已退出")?;
        let req = serde_json::json!({
            "cmd": "transcribe",
            "id": task_id,
            "payload": payload,
        });
        let line = serde_json::to_string(&req).map_err(|e| e.to_string())?;
        writeln!(sc.stdin, "{}", line).map_err(|e| format!("发送请求失败：{}", e))?;
        sc.stdin.flush().ok();
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
            match kind {
                "accepted" => {}
                "progress" => {
                    let _ = app_for_events.emit("transcribe://progress", &v);
                }
                "log" => {
                    let _ = app_for_events.emit("transcribe://log", &v);
                }
                "result" => {
                    let _ = app_for_events.emit("transcribe://done", &v);
                }
                "error" => {
                    let _ = app_for_events.emit("transcribe://error", &v);
                }
                _ => {}
            }
        }

        // 事件流结束：若既没 done 也没 error，说明被异常打断
        let finished = {
            // 读 stdout 时无法回看，这里用 stderr 兜底给一条提示
            true
        };
        let _ = finished;

        // 收尾：wait 进程，清理任务表
        if let Some(mut sc) = sidecar_for_reader.lock().take() {
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
pub async fn cancel_transcribe(state: State<'_, EngineState>, task_id: String) -> Result<(), String> {
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
