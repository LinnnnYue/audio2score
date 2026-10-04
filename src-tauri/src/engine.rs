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
// 包内独立 Python 运行时
//
// ## 为什么必须有这个
// 引导安装的本质是「用系统 Python 建 venv」。若目标机器**根本没有 Python**，
// 第一步就死：`base_python()` 遍历 PATH 全空 → 用户看到
// 「找不到可用的 Python 3.9+」，而他是小白，不知道去哪装、装哪个版本。
// 这是纯粹的「鸡生蛋」死结——安装器需要 Python 才能跑，而机器上没有 Python。
//
// ## 解法：把解释器本身随包分发
// 用 astral-sh/python-build-standalone 的 `install_only_stripped` 变体：
// 可重定位、自包含、解压即用，不写注册表、不污染系统 PATH、不需要管理员权限。
// 放在资源目录下随包走，安装包体积增加约 22MB（压缩后更少），
// 换来「零前置条件」——用户点一下就能装完，无需自备任何环境。
//
// ## 版本为什么选 3.13 而不是更新的 3.14
// torch cu124 的 Windows wheel 只覆盖 cp310–cp313（见 bootstrap.py 的
// TORCH_PY_MIN/MAX）。捆 3.14 会让完整档 100% 装不上，且报错是 pip 的
// 「Could not find a version ... (from versions: none)」——
// 与「镜像不可用」逐字相同，用户会误判成网络问题反复换源。
// ─────────────────────────────────────────────────────────────

/// 包内运行时的候选锚点目录（其下应有 `python/python.exe`）。
///
/// 与 `engine_dir()` 同样的教训：**exe 同级必须排在源码树之前**，
/// 否则安装版会去用开发者机器上的路径。
fn runtime_roots(app: &AppHandle) -> Vec<PathBuf> {
    let mut roots: Vec<PathBuf> = Vec::new();

    // 1) 资源目录（NSIS 安装态的规范位置）
    if let Ok(rd) = app.path().resource_dir() {
        roots.push(rd.join("runtime"));
    }
    // 2) exe 同级 runtime/（部分打包目标会把 resources 摊平到这里）
    if let Ok(exe) = std::env::current_exe() {
        if let Some(d) = exe.parent() {
            roots.push(d.join("runtime"));
        }
    }
    // 3) 开发态：src-tauri/runtime
    #[cfg(debug_assertions)]
    roots.push(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("runtime"));

    roots
}

/// 包内捆绑的解释器绝对路径。未捆绑 / 被裁剪掉时返回 None。
fn bundled_python(app: &AppHandle) -> Option<PathBuf> {
    for root in runtime_roots(app) {
        #[cfg(windows)]
        let exe = root.join("python").join("python.exe");
        #[cfg(not(windows))]
        let exe = root.join("python").join("bin").join("python3");

        if exe.is_file() {
            return Some(exe);
        }
    }
    None
}

/// 判定某解释器是否落在 torch 支持的版本区间内（与 bootstrap.py 的
/// TORCH_PY_MIN/MAX 保持一致：3.10 – 3.13）。
///
/// 为什么要真跑一次而不信文件名：捆绑资源可能被替换、被安全软件截断、
/// 或被打包脚本误接了别的版本。静默建出 cp314 的 venv，
/// 用户要等下载完 2.5GB 才看到失败——代价太高。
fn python_in_torch_range(exe: &std::path::Path) -> bool {
    let out = py_child(exe)
        .arg("-c")
        .arg("import sys;print('%d.%d' % sys.version_info[:2])")
        .output();

    let Ok(o) = out else { return false };
    if !o.status.success() {
        return false;
    }
    let text = String::from_utf8_lossy(&o.stdout);
    let Some(line) = text.lines().last() else {
        return false;
    };
    let mut it = line.trim().split('.');
    let (Some(maj), Some(min)) = (it.next(), it.next()) else {
        return false;
    };
    let (Ok(maj), Ok(min)) = (maj.parse::<u32>(), min.parse::<u32>()) else {
        return false;
    };
    (maj, min) >= (3, 10) && (maj, min) <= (3, 13)
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

    // 0) 用户自定义安装位置（bootstrap 安装成功后写入 location.json）
    //
    // 为什么必须放最前：用户可以把引擎装到任意盘（如 D 盘），
    // 若只认死的 %LOCALAPPDATA%/bapu/engine，用户选了别的盘也白选 ——
    // 装完了这边仍找不到 venv，仍报「引擎未就绪」。
    #[cfg(windows)]
    if let Some(local) = std::env::var_os("LOCALAPPDATA") {
        let lf = PathBuf::from(local).join("bapu").join("location.json");
        if let Ok(txt) = std::fs::read_to_string(&lf) {
            if let Ok(v) = serde_json::from_str::<Value>(&txt) {
                if let Some(custom) = v.get("engine_dir").and_then(|x| x.as_str()) {
                    let py = PathBuf::from(custom)
                        .join(".venv")
                        .join("Scripts")
                        .join("python.exe");
                    if py.is_file() {
                        return Ok((dir.clone(), py));
                    }
                }
            }
        }
    }
    

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

/// 清空幂等命令的结果缓存。
///
/// ## 什么时候必须调用
/// 「引擎内容发生变化」时**一律**要清，目前有两处：
///   1. 引导安装成功（从无到有装上 demucs / torch）
///   2. 引擎目录迁移完成（解释器路径变了）
///
/// ## 为什么不能省
/// `capabilities` 命中的是**安装前**的结论。不失效的后果是：
/// 用户装完 Demucs，功能页仍写着「未安装 Demucs / 未检测到 CUDA」，
/// 而真实环境是好的 —— 主上实测截图反馈过这个现象
/// （「装完了为什么还有这两行」）。
///
/// 缓存本意是避免冷启动时并发 4 个 Python 子进程，
/// 其注释「同一会话内结果恒定」在**安装场景下并不成立** ——
/// 安装干的就是改变引擎能力这件事。
fn invalidate_engine_cache(app: &AppHandle) {
    use tauri::Manager;
    if let Some(st) = app.try_state::<EngineState>() {
        st.cache.lock().clear();
    }
}

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

    let mut child = py_child(&py)
        .arg(&bridge)
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

/// 构造一个「已配好 UTF-8、且无窗口」的 Python 子进程命令。
///
/// ## 为什么必须收敛成唯一入口
///
/// 踩坑实录（线上故障，由主上老公的机器暴露）：
/// `PYTHONUTF8` / `PYTHONIOENCODING` 原先散落在各启动点**手工添加**，
/// 结果 7 个启动点漏了 1 个 —— 恰恰是 `start_transcribe` 的长驻 sidecar。
/// 症状极具迷惑性：
///   - 拖入文件（走 run_once，**有** env）→ 探测正常，文件顺利进列表；
///   - 一点「开始扒谱」（走 sidecar，**无** env）→ 立刻失败在「准备音频」，
///     报「文件不存在，可能已被移动或删除」。
/// 根因是 Python 用 GBK 解码了 UTF-8 的中文路径，拿到一个不存在的乱码路径。
/// 中文路径 + 中文 Windows 才触发，纯 ASCII 路径的测试**永远测不到**。
///
/// 故收敛为唯一入口：新增任何 Python 子进程都不可能再漏掉编码设置。
fn py_child(exe: impl AsRef<std::ffi::OsStr>) -> Command {
    let mut cmd = hide_child(Command::new(exe));
    // stdin/stdout/stderr 被管道接管时，Python 会退回系统 locale 编码
    // （中文 Windows = GBK）。UTF-8 模式自进程启动即生效，
    // 比「事后 reconfigure」更彻底 —— 且覆盖 stdin（reconfigure 此前只做了 out/err）。
    cmd.env("PYTHONIOENCODING", "utf-8");
    cmd.env("PYTHONUTF8", "1");
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

    // ⚠️ 必须走 py_child —— 这里是「拖入能过、一扒谱就报文件不存在」的故障点：
    // 此前此处手工漏掉了 UTF-8 环境变量，中文路径经 stdin 被 GBK 解码成乱码。
    let mut child = py_child(&py)
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

    let base = base_python(&app).ok_or(NO_BASE_PYTHON)?;
    let out = py_child(&base)
        .arg(&script)
        .arg("--status")
        .env("BAPU_BASE_PYTHON", &base)
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
    let mut v: Value = serde_json::from_str(text.trim())
        .map_err(|e| format!("安装器返回无法解析：{}\n{}", e, &text[..text.len().min(300)]))?;

    // ── 注入「基底解释器来自何处」──
    // bootstrap.py 只看得见被传给它的那一个解释器，无从判断它是包内运行时
    // 还是用户机器上的系统 Python。而这两者的用户体验天差地别：
    //   包内  → 无需任何前置条件，点一下就能装
    //   系统  → 小白机器上常常根本没有，是个死结
    // 引导页据此把「找不到 Python 3.9+」这条头号拦路虎换成
    // 一句安心的「已内置运行时，无需自备环境」。
    if let Some(obj) = v.as_object_mut() {
        obj.insert("basePython".into(), serde_json::json!(base));
        obj.insert(
            "runtimeBundled".into(),
            serde_json::json!(bundled_python(&app).is_some()),
        );
    }
    Ok(v)
}

/// 从一行子进程输出里提取 `install_result` 结构化结果。
///
/// 为什么不能判断「行首是不是花括号」：
/// bootstrap.py 的进度条用回车符原地刷新、不换行，一旦换行没补上，
/// JSON 就会与进度条残留内容挤在同一行。此时按行首判断会**直接漏掉**结果，
/// 上层只能判定「脚本异常退出」—— 用户看到的就是
/// 「安装未完成 / 安装进程异常退出，没有返回结果」（主上实测反馈过）。
///
/// 故必须按标记定位起点，而不是依赖行的开头。
fn extract_install_result(line: &str) -> Option<Value> {
    let idx = line.find("{\"type\":")?;
    let v: Value = serde_json::from_str(&line[idx..]).ok()?;
    (v.get("type").and_then(|x| x.as_str()) == Some("install_result")).then_some(v)
}

/// 启动 bootstrap.py 的一次子进程调用，并把它接进安装事件通道。
///
/// ## 为什么安装与迁移共用它
/// 两者除了命令行参数不同，其余逻辑完全一致：进度行 → `install://progress`、
/// 普通行 → `install://log`、结尾的 `install_result` JSON → `install://done`，
/// 外加**成功即失效引擎缓存**。共用一套通道，前端也就只需要一套 UI 逻辑
/// —— 事实上设置页的迁移进度就是复用安装向导那套进度显示。
///
/// 返回子进程句柄，由调用方登记进 `state.tasks`（支持取消）。
fn run_bootstrap(
    app: &AppHandle,
    script: &std::path::Path,
    extra_args: Vec<String>,
    task_id: String,
) -> Result<Arc<Mutex<Option<Sidecar>>>, String> {
    let base = base_python(app).ok_or(NO_BASE_PYTHON)?;
    let mut child = py_child(&base)
        .arg(script)
        .args(&extra_args)
        // ── 把「该用哪个基底解释器」的裁决权钉死在 Rust 这一侧 ──
        // 踩坑推演：Rust 用包内 3.13 启动 bootstrap.py，而 bootstrap 的
        // find_base_python() 自行扫 PATH —— 本机 `py` 默认是 3.14，
        // 于是它拿 3.14 去建 venv，最后一批装 torch 时报
        // 「Could not find a version that satisfies the requirement torch」。
        // 那句报错与「镜像不可用」逐字相同，用户会去换源，永远换不好。
        // 故此处显式下发，bootstrap 侧把它当候选第一位。
        .env("BAPU_BASE_PYTHON", &base)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| format!("启动引擎进程失败：{}\n\n{}", e, NO_BASE_PYTHON))?;

    let stdout = child.stdout.take().ok_or("引擎进程 stdout 不可用")?;
    let stderr = child.stderr.take();

    // 安装器用 \r 刷新进度行；按行转发即可（Rust 侧不必解析 ANSI）
    let app_p = app.clone();
    let tid_p = task_id.clone();
    std::thread::spawn(move || {
        use std::io::BufRead;
        let reader = std::io::BufReader::new(stdout);
        let mut seen = 0usize;
        // bootstrap.py 在结尾输出一行机器可读的 install_result JSON。
        // 必须解析它 —— 只看「子进程结束」无法区分成功与失败：
        // 网络中断 / 磁盘不足 / pip 报错同样是正常退出 + 一段可读文本。
        // 不解析的话，前端只会看到界面弹回选择页，用户完全不知道发生了什么。
        let mut outcome: Option<Value> = None;
        for line in reader.lines().map_while(std::result::Result::ok) {
            let t = line.trim();
            if t.is_empty() {
                continue;
            }
            // 结构化结果可能不在行首：bootstrap 的进度条用回车符原地刷新，
            // 换行前残留的内容会与 JSON 挤在同一行（实测踩过）。
            // 故交给 extract_install_result 按标记定位，而不是判断行首。
            if let Some(v) = extract_install_result(t) {
                outcome = Some(v);
                continue; // 结构化结果不当普通日志
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
        let ok = outcome
            .as_ref()
            .and_then(|v| v.get("ok"))
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        let message = outcome
            .as_ref()
            .and_then(|v| v.get("message"))
            .cloned()
            .unwrap_or(Value::Null);
        let log_tail = outcome
            .as_ref()
            .and_then(|v| v.get("log_tail"))
            .cloned()
            .unwrap_or(Value::Null);

        let _ = app_p.emit(
            "install://done",
            serde_json::json!({
                "taskId": tid_p,
                "lines": seen,
                "ok": ok,
                "message": message,
                "logTail": log_tail,
                // 没收到结构化结果 = 脚本异常退出（崩溃/被杀）
                "outcomeMissing": outcome.is_none(),
            }),
        );

        // ⚠️ 必须在 emit 之后、且只在成功时失效缓存 ——
        // 前端收到 done 就会立刻重拉 capabilities / check_engine，
        // 此刻缓存若还留着安装前的结论，它拿到的就是旧值。
        if ok {
            invalidate_engine_cache(&app_p);
        }
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

    Ok(Arc::new(Mutex::new(Some(Sidecar { child, stdin: None }))))
}

/// 启动引擎安装。立即返回 taskId，进度经事件流回传。
#[tauri::command]
pub async fn install_engine(
    app: AppHandle,
    state: State<'_, EngineState>,
    tier: Option<String>,
    mirror: Option<String>,
    target_dir: Option<String>,
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

    let mut args = vec![
        "--tier".to_string(),
        tier,
        "--mirror".to_string(),
        mirror,
    ];
    // 自定义安装位置（可空 = 用 bootstrap 的默认位置）
    if let Some(d) = &target_dir {
        if !d.trim().is_empty() {
            args.push("--dir".to_string());
            args.push(d.clone());
        }
    }

    let handle = run_bootstrap(&app, &script, args, task_id.clone())?;
    // 记录句柄以便取消
    state.tasks.lock().insert(task_id.clone(), handle);

    Ok(StartResult { task_id })
}

/// 把已安装的引擎迁移到新位置。
///
/// 刻意**不做重装**：完整档 5.4GB（torch 一家 4.4GB），
/// 用户只是想把它从 C 盘挪到别的盘，没道理让他重下 2.5GB。
/// 迁移的安全策略与回滚逻辑都在 bootstrap.py 的 `move_engine()` 里，
/// 那边有逐一覆盖的测试（含每条失败路径都不得损坏原环境）。
#[tauri::command]
pub async fn migrate_engine(
    app: AppHandle,
    state: State<'_, EngineState>,
    new_dir: String,
) -> Result<StartResult, String> {
    let target = new_dir.trim().to_string();
    if target.is_empty() {
        return Err("请先选择一个目标文件夹。".into());
    }

    let dir = engine_dir(&app)?;
    let script = dir.join("bootstrap.py");
    if !script.is_file() {
        return Err("安装器脚本缺失（安装包不完整，请重新下载）".into());
    }

    let task_id = uuid::Uuid::new_v4().to_string();
    // 只传目标位置；源位置由 bootstrap 从 location.json / 默认值自行解析 ——
    // 前端不该也不需要对「当前引擎装在哪」有第二份判断。
    let args = vec!["--move-to".to_string(), target];

    let handle = run_bootstrap(&app, &script, args, task_id.clone())?;
    state.tasks.lock().insert(task_id.clone(), handle);

    Ok(StartResult { task_id })
}

// ─────────────────────────────────────────────────────────────
// 对外集成信息（CLI / MCP）
//
// ## 为什么必须由后端生成，而不是前端或文档写死
// CLI 与 MCP 都要**绝对路径**才能用：MCP 客户端配置里必须写明
// 「用哪个解释器跑哪个脚本」。而引擎装在哪块盘、哪个目录，是用户在
// 引导页或设置页**自己选的**（完整档 5.4GB，C 盘紧张就得换盘），
// 前端和文档都无从知晓。
//
// 更关键的是：给用户看的配置必须是**他自己机器上的路径**。仓库里那份
// `mcp/bapu.json` 是开发机快照——写死了开发者的绝对路径，且 JSON 里的
// 反斜杠未转义（`\L` 不是合法转义序列，严格解析器直接报错）——
// 对用户一文不值，还会让人以为「按文档抄了却连不上」是自己操作有问题。
//
// 故此处按**运行时真实位置**拼一份，并且用 serde_json 序列化——
// 转义交给序列化器，不手写字符串，从根上杜绝上面那个坑。
// ─────────────────────────────────────────────────────────────

/// 喂给「新手指引」的集成信息。字段全为 camelCase，与前端 types 对齐。
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct IntegrationInfo {
    /// 引擎是否已就绪（未就绪时其余字段无意义，前端应引导去安装）
    pub ready: bool,
    /// 引擎源码目录（含 cli.py / mcp_server.py）
    pub engine_dir: String,
    /// 引擎解释器（venv 里的 python.exe）
    pub python: String,
    /// 探测能力用的一条命令，可整行粘贴
    pub cli_caps: String,
    /// 扒谱示例命令
    pub cli_example: String,
    /// 可直接粘进 MCP 客户端配置的完整 JSON
    pub mcp_config: String,
    /// 引擎档位是否含 mcp 依赖（基础档不含）
    pub mcp_ready: bool,
    /// 引擎目录位置无法解析时的说明
    pub reason: String,
}

/// 把路径拼成「可直接粘贴进终端」的形式。
///
/// Windows 终端里含空格的路径必须加引号，否则被拆成多个参数。
/// 本项目早期工作区路径含空格（`MusicXML 格式`）时曾踩此坑，
/// 故此处不做「路径恰好无空格」的假设，一律加引号。
fn quote_arg(p: &str) -> String {
    format!("\"{}\"", p)
}

#[tauri::command]
pub async fn get_integration_info(app: AppHandle) -> Result<IntegrationInfo, String> {
    Ok(build_integration_info(&app))
}

/// 生成集成信息。抽成独立函数是为了让「展示」与「导出」用**同一份**内容——
/// 两处各算一次迟早会漂移，而漂移的后果是用户拿到的配置文件与界面上看到的不一致。
fn build_integration_info(app: &AppHandle) -> IntegrationInfo {
    let empty = |reason: &str| IntegrationInfo {
        ready: false,
        engine_dir: String::new(),
        python: String::new(),
        cli_caps: String::new(),
        cli_example: String::new(),
        mcp_config: String::new(),
        mcp_ready: false,
        reason: reason.to_string(),
    };

    let dir = match engine_dir(app) {
        Ok(d) => d,
        Err(e) => return empty(&e),
    };
    let (_, py) = match resolve_engine_python(app) {
        Ok(v) => v,
        Err(e) => return empty(&e),
    };

    let py_s = py.to_string_lossy().into_owned();
    let cli = dir.join("cli.py");
    let mcp = dir.join("mcp_server.py");
    let cli_s = cli.to_string_lossy().into_owned();
    let mcp_s = mcp.to_string_lossy().into_owned();

    // MCP 依赖探测：基础档不含 mcp 包，此时配置粘过去也起不来。
    // 与其让用户在别的软件里看到一串 ModuleNotFoundError，不如在这里先说清。
    let mcp_ready = if mcp.is_file() {
        py_child(&py)
            .arg("-c")
            .arg("import mcp")
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false)
    } else {
        false
    };

    // 交给序列化器处理转义 —— 手写字符串是上一个 bug 的来源
    let cfg = serde_json::json!({
        "mcpServers": {
            "bapu": {
                "command": py_s.clone(),
                "args": [mcp_s.clone()],
                // 关掉 tqdm / HuggingFace 的进度条：MCP 的 stdio 通道上
                // 多出的进度输出会污染 JSON-RPC 帧。
                "env": {
                    "TQDM_DISABLE": "1",
                    "HF_HUB_DISABLE_PROGRESS_BARS": "1"
                }
            }
        }
    });
    let mcp_config =
        serde_json::to_string_pretty(&cfg).unwrap_or_else(|_| String::from("{}"));

    IntegrationInfo {
        ready: true,
        engine_dir: dir.to_string_lossy().into_owned(),
        python: py_s.clone(),
        cli_caps: format!("{} {} --caps", quote_arg(&py_s), quote_arg(&cli_s)),
        cli_example: format!(
            "{} {} \"D:\\我的音乐\\歌曲.mp3\" -m vocals",
            quote_arg(&py_s),
            quote_arg(&cli_s)
        ),
        mcp_config,
        mcp_ready,
        reason: String::new(),
    }
}

/// 把 MCP 配置导出成一个 .json 文件，返回写入的绝对路径。
///
/// ## 为什么不让前端传内容
/// 内容由后端现算（`build_integration_info`），前端只能指定「存到哪」。
/// 这样即使前端被注入，也无法借这个命令往任意文件里写任意字节——
/// 它只会写一份本机 MCP 配置。写入面越窄越好。
#[tauri::command]
pub async fn export_mcp_config(app: AppHandle, target: String) -> Result<String, String> {
    let info = build_integration_info(&app);
    if !info.ready {
        return Err(format!("引擎尚未就绪，无法生成配置：{}", info.reason));
    }

    let path = PathBuf::from(target.trim());
    if !path.is_absolute() {
        return Err("请提供一个绝对路径。".into());
    }
    // 只认 .json：挡住「手滑选中了桌面上的某个重要文件」这类误操作。
    let is_json = path
        .extension()
        .and_then(|s| s.to_str())
        .map(|s| s.eq_ignore_ascii_case("json"))
        .unwrap_or(false);
    if !is_json {
        return Err("请把文件名保存为 .json 结尾。".into());
    }
    if let Some(parent) = path.parent() {
        if !parent.is_dir() {
            return Err(format!("目标文件夹不存在：{}", parent.display()));
        }
    }

    std::fs::write(&path, info.mcp_config.as_bytes())
        .map_err(|e| format!("写入失败：{}", e))?;

    Ok(path.to_string_lossy().into_owned())
}

/// 从 "  [████░░]  42.0%  xxx" 中解析百分比
fn parse_progress(line: &str) -> Option<f64> {    let p = line.find('%')?;
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

/// 找一个能用的基底 Python（**用途单一：建 venv**）。
///
/// ## 顺序即优先级：包内运行时 > 系统 Python
/// 包内独立运行时必须排第一，因为它解决了两件系统 Python 无法保证的事：
///   ① 小白机器上**根本没有** Python —— PATH 遍历全空，安装直接死；
///   ② 有 Python 但版本不合规（本机 `py` 的默认版本实测是 3.14，
///      torch cu124 无 cp314 wheel）→ 完整档必失败。
/// 只有在包内运行时缺失或损坏时才回退 PATH —— 那是「安装包不完整」的
/// 降级路径，此时系统若恰好有合规 Python，仍能装成功，比直接报死好。
///
/// ⚠️ 检测子进程必须走 `hide_child`：裸调 `Command` 在 Windows 上会
/// 弹出黑色控制台窗口。用户点「安装」的瞬间闪一个黑框，观感等同出错。
fn base_python(app: &AppHandle) -> Option<String> {
    // 1) 包内捆绑的独立 CPython
    if let Some(p) = bundled_python(app) {
        if python_in_torch_range(&p) {
            return Some(p.to_string_lossy().into_owned());
        }
    }

    // 2) 系统 PATH 兜底
    for exe in ["py", "python", "python3"] {
        if py_child(exe)
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

/// 「找不到基底 Python」的统一文案。
///
/// 走到这里说明包内运行时也不在了（安装包被裁剪/被杀软隔离/解压中断），
/// 原文案「找不到可用的 Python 3.9+」会让小白以为自己要先去装个 Python ——
/// 而正常安装包里根本不需要他做这件事。故必须指向「重装应用」。
const NO_BASE_PYTHON: &str =
    "应用内置的 Python 运行时缺失或已损坏，系统中也未找到可用的 Python（需 3.10–3.13）。\n\n\
     内置运行时本应随应用一起分发，缺失通常意味着：\n\
     · 安装包不完整（下载中断或被杀毒软件隔离）\n\
     · 手动删除了安装目录下的 runtime 文件夹\n\n\
     解决办法：重新运行安装包覆盖安装。";

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

    /// 回归：主上实测「安装未完成 / 安装进程异常退出，没有返回结果」的根因。
    ///
    /// 进度条用回车符原地刷新、不换行，于是 JSON 会与进度条残留挤在同一行。
    /// 解析逻辑必须扛得住这种「前面挂着脏数据」的行 —— 否则安装结果直接丢失，
    /// 用户看到的就只剩一句「异常退出」，完全不知道是磁盘满还是断网。
    #[test]
    fn install_result_survives_progress_bar_remnant() {
        let clean = r#"{"type":"install_result","ok":true,"message":"好了"}"#;
        let v = extract_install_result(clean).expect("干净行应能解析");
        assert_eq!(v.get("ok").and_then(|x| x.as_bool()), Some(true));

        // 关键场景：JSON 前面挂着含回车符的进度条残留
        let cr = '\u{000D}';
        let messy = format!(
            "{cr}  [######------]  42.0%  下载 torch{cr}{}",
            r#"{"type":"install_result","ok":false,"message":"磁盘空间不足"}"#
        );
        assert!(messy.contains(cr), "构造的用例必须真的含回车符");
        let v = extract_install_result(&messy).expect("混流行也必须能解析");
        assert_eq!(v.get("ok").and_then(|x| x.as_bool()), Some(false));
        assert_eq!(
            v.get("message").and_then(|x| x.as_str()),
            Some("磁盘空间不足")
        );

        // 其他类型的 JSON 不该被误判成安装结果
        assert!(extract_install_result(r#"{"type":"progress","pct":0.5}"#).is_none());
        // 普通日志行
        assert!(extract_install_result("Collecting torch").is_none());
        // 截断的 JSON 只能返回 None，绝不能 panic
        assert!(extract_install_result(r#"{"type":"install_result","ok""#).is_none());
    }
}

// ─────────────────────────────────────────────────────────────
// 诊断报告：用户反馈问题时的「一键取证」
//
// ## 为什么需要它
// 主上原话：「有没有日志可以看？让他发过来？还是你做一个可以复制反馈
// debug 报错日志结果或文件的按钮？」
//
// 现实的痛点：真正出问题的机器在别人手上（如老公的电脑），
// 而「拖入后失败」这类问题，界面上能看到的只有一行文案 + 两行日志，
// 关键事实（系统的 ANSI 代码页、Python 的 UTF-8 模式是否生效、
// 路径里有没有非 ASCII 字符）全都看不到 —— 只能靠来回追问。
//
// 本模块把这些事实一次收集齐，落成一个可发送的文本文件。
// ─────────────────────────────────────────────────────────────

/// 前端在请求诊断报告时补上的现场上下文。
///
/// 刻意由前端补料而非 Rust 去猜：报错现场只有界面知道 ——
/// 用户拖的是哪个文件、界面上已经显示过哪些日志行。
#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DiagnosticContext {
    #[serde(default)]
    pub logs: Vec<String>,
    #[serde(default)]
    pub error_message: Option<String>,
    #[serde(default)]
    pub error_detail: Option<String>,
    #[serde(default)]
    pub input_path: Option<String>,
    #[serde(default)]
    pub mode: Option<String>,
    /// 由前端生成（Rust 侧无日期格式化能力，不为此引入 chrono）
    #[serde(default)]
    pub generated_at: Option<String>,
    #[serde(default)]
    pub app_version: Option<String>,
}

/// 探测 Python 侧的运行时环境事实。
///
/// ## 为什么值得专门跑一次子进程
/// 「路径含中文的音频扒谱失败」这类问题的根因**通常不在业务代码**，而在编码：
/// Python 读管道 stdin 时若用系统 locale 编码（中文 Windows = cp936）而非 UTF-8，
/// 中文路径就会变成乱码。此时最需要一眼看到的是 `utf8Mode` 与 `preferredEncoding`
/// 这两个值 —— 它们能立刻区分「环境没配对」与「另有他因」。
/// 所以必须是**运行时的真实值**，不能由 Rust 侧推测。
fn probe_python_env(app: &AppHandle) -> Value {
    const SCRIPT: &str = r#"
import json, sys, locale, os
out = {
    "version": "%d.%d.%d" % sys.version_info[:3],
    "executable": sys.executable,
    "utf8Mode": 1 if sys.flags.utf8_mode else 0,
    "stdoutEncoding": sys.stdout.encoding,
    "stderrEncoding": sys.stderr.encoding,
    "stdinEncoding": (sys.stdin.encoding if sys.stdin else None),
    "filesystemEncoding": sys.getfilesystemencoding(),
    "preferredEncoding": locale.getpreferredencoding(False),
    "envPYTHONUTF8": os.environ.get("PYTHONUTF8"),
    "envPYTHONIOENCODING": os.environ.get("PYTHONIOENCODING"),
}
try:
    wv = sys.getwindowsversion()
    out["windowsVersion"] = "%d.%d.%d" % (wv.major, wv.minor, wv.build)
except Exception:
    pass
print(json.dumps(out, ensure_ascii=False))
"#;

    let py = match resolve_engine_python(app) {
        Ok((_, p)) => p,
        Err(e) => return serde_json::json!({"error": format!("引擎未就绪，无法探测：{}", e)}),
    };

    match py_child(&py)
        .arg("-c")
        .arg(SCRIPT)
        .stdin(Stdio::null())
        .output()
    {
        Ok(o) if o.status.success() => {
            let text = String::from_utf8_lossy(&o.stdout);
            serde_json::from_str(text.trim()).unwrap_or_else(|e| {
                serde_json::json!({
                    "error": format!("探测输出无法解析：{}", e),
                    "raw": text.trim(),
                })
            })
        }
        Ok(o) => serde_json::json!({
            "error": "探测进程退出异常",
            "stderr": String::from_utf8_lossy(&o.stderr)
                .trim()
                .chars()
                .take(300)
                .collect::<String>(),
        }),
        Err(e) => serde_json::json!({"error": format!("无法启动探测进程：{}", e)}),
    }
}

/// 把 JSON 拍平成 `键：值` 行，供报告的「环境」段落逐行展示。
fn push_kv(out: &mut String, label: &str, v: Option<&Value>) {
    let text = match v {
        Some(Value::Null) | None => "(无)".to_string(),
        Some(Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
    };
    out.push_str(&format!("{label}：{text}\n"));
}

fn render_diagnostic_report(app: &AppHandle, ctx: &DiagnosticContext) -> String {
    let mut s = String::new();

    s.push_str("扒谱助手 · 诊断报告\n");
    s.push_str(&"=".repeat(64));
    s.push('\n');
    s.push_str(&format!(
        "生成时间：{}\n",
        ctx.generated_at.as_deref().unwrap_or("(未提供)")
    ));
    s.push_str("报告格式：1\n\n");

    // ── 应用 ──
    s.push_str("【应用】\n");
    s.push_str(&format!(
        "版本：{}\n",
        ctx.app_version
            .clone()
            .unwrap_or_else(|| app.package_info().version.to_string())
    ));
    s.push_str(&format!(
        "构建类型：{}\n",
        if cfg!(debug_assertions) { "debug（开发态）" } else { "release" }
    ));
    if let Ok(exe) = std::env::current_exe() {
        s.push_str(&format!("可执行文件：{}\n", exe.display()));
    }
    s.push('\n');

    // ── 系统 ──
    s.push_str("【系统】\n");
    s.push_str(&format!("平台：{}\n", std::env::consts::OS));
    s.push_str(&format!("架构：{}\n", std::env::consts::ARCH));
    if let Ok(os) = std::env::var("OS") {
        s.push_str(&format!("环境变量 OS：{}\n", os));
    }
    s.push('\n');

    // ── 引擎 ──
    s.push_str("【引擎】\n");
    let info = build_integration_info(app);
    s.push_str(&format!("状态：{}\n", if info.ready { "就绪" } else { "未就绪" }));
    if !info.ready {
        s.push_str(&format!("未就绪原因：{}\n", info.reason));
    }
    s.push_str(&format!("引擎目录：{}\n", info.engine_dir));
    s.push_str(&format!("解释器：{}\n", info.python));
    s.push_str(&format!(
        "包内独立运行时：{}\n",
        if bundled_python(app).is_some() { "有" } else { "无（依赖系统 Python）" }
    ));
    s.push_str(&format!("MCP 依赖：{}\n", if info.mcp_ready { "可用" } else { "不可用" }));
    if let Some(b) = base_python(app) {
        s.push_str(&format!("基底解释器：{}\n", b));
    }
    s.push('\n');

    // ── Python 环境（编码类问题的关键证据）──
    s.push_str("【Python 环境】\n");
    s.push_str("（若出现「文件不存在」「乱码」类报错，先看这里的 utf8Mode 与 preferredEncoding）\n");
    let env = probe_python_env(app);
    if env.get("error").is_some() {
        push_kv(&mut s, "探测失败", env.get("error"));
        if let Some(st) = env.get("stderr") {
            push_kv(&mut s, "stderr", Some(st));
        }
    } else {
        push_kv(&mut s, "Python 版本", env.get("version"));
        push_kv(&mut s, "解释器路径", env.get("executable"));
        push_kv(&mut s, "UTF-8 模式(utf8Mode)", env.get("utf8Mode"));
        push_kv(&mut s, "系统首选编码", env.get("preferredEncoding"));
        push_kv(&mut s, "stdin 编码", env.get("stdinEncoding"));
        push_kv(&mut s, "stdout 编码", env.get("stdoutEncoding"));
        push_kv(&mut s, "文件系统编码", env.get("filesystemEncoding"));
        push_kv(&mut s, "PYTHONUTF8 环境变量", env.get("envPYTHONUTF8"));
        push_kv(&mut s, "PYTHONIOENCODING", env.get("envPYTHONIOENCODING"));
        push_kv(&mut s, "Windows 版本", env.get("windowsVersion"));
    }
    s.push('\n');

    // ── 本次任务 ──
    if ctx.error_message.is_some() || ctx.input_path.is_some() || ctx.mode.is_some() {
        s.push_str("【本次任务】\n");
        if let Some(p) = &ctx.input_path {
            s.push_str(&format!("输入路径：{}\n", p));
            // 这条是本项目真踩过的坑的指纹：非 ASCII 路径 + 管道编码配置不当
            // = 路径被错误解码成乱码 → 报「文件不存在」。放在报告里一眼可辨。
            s.push_str(&format!(
                "路径含非 ASCII 字符：{}\n",
                if p.is_ascii() { "否" } else { "是（编码类问题的高危信号）" }
            ));
        }
        if let Some(m) = &ctx.mode {
            s.push_str(&format!("扒谱模式：{}\n", m));
        }
        if let Some(m) = &ctx.error_message {
            s.push_str(&format!("错误信息：{}\n", m));
        }
        if let Some(d) = &ctx.error_detail {
            s.push_str(&format!("技术细节：{}\n", d));
        }
        s.push('\n');
    }

    // ── 运行日志 ──
    s.push_str("【运行日志】\n");
    if ctx.logs.is_empty() {
        s.push_str("(本次没有日志)\n");
    } else {
        let start = ctx.logs.len().saturating_sub(200);
        for line in &ctx.logs[start..] {
            s.push_str("  ");
            s.push_str(line);
            s.push('\n');
        }
    }
    s.push('\n');

    s.push_str(&"-".repeat(64));
    s.push('\n');
    s.push_str("提示：本报告含本机文件路径与用户名，请只发送给信任的人。\n");

    s
}

/// 生成诊断报告文本（不落盘）。前端可拿来直接复制或先预览。
#[tauri::command]
pub async fn build_diagnostic_report(
    app: AppHandle,
    context: DiagnosticContext,
) -> Result<String, String> {
    Ok(render_diagnostic_report(&app, &context))
}

/// 把诊断报告写入用户指定的文件。
///
/// 写 **UTF-8 带 BOM**：Windows 记事本对无 BOM 的 UTF-8 会按 ANSI 解读，
/// 中文全成乱码 —— 而这份文件正是要给别人看的，不能冒这个险。
#[tauri::command]
pub async fn save_diagnostic_report(target: String, content: String) -> Result<String, String> {
    let path = PathBuf::from(target.trim());
    if !path.is_absolute() {
        return Err("请提供一个绝对路径。".into());
    }
    if let Some(parent) = path.parent() {
        if !parent.is_dir() {
            return Err(format!("目标文件夹不存在：{}", parent.display()));
        }
    }

    let mut bytes: Vec<u8> = Vec::with_capacity(content.len() + 3);
    bytes.extend_from_slice(&[0xEF, 0xBB, 0xBF]); // UTF-8 BOM
    bytes.extend_from_slice(content.as_bytes());

    std::fs::write(&path, bytes).map_err(|e| format!("写入失败：{}", e))?;
    Ok(path.to_string_lossy().into_owned())
}

// ─────────────────────────────────────────────────────────────
// 问题上报：把诊断报告送到项目方
//
// ## 为什么必须在 Rust 侧发
// tauri.conf.json 的 CSP 是 `default-src 'self'`，**没有 connect-src**，
// 前端 fetch 外部端点会被 WebView 直接拦下。Rust 侧不受 CSP 约束。
//
// ## 为什么是 PushPlus（2026-10-04 本机实测结论）
// 免费方案筛过一轮，拿实测数据说话：
//   · api.web3forms.com —— 无 Origin 的原生请求被判为「服务端调用」，
//     返回 403 “Pro plan is required”；补上 Origin 直接连接被掐断；
//     只伪装 User-Agent 则撞上 Cloudflare 人机验证。桌面客户端没有
//     执行 JS 挑战的环境，这条路结构性走不通。
//   · formsubmit.co —— 同样在 Cloudflare 后，返回“须经由 web server 打开”。
//   · pushplus.plus —— 落在阿里云杭州（121.40.246.120），HTTP 200
//     直出业务 JSON，无挑战；且它本身就是面向 Jenkins / 脚本 / 爬虫
//     这类**服务端调用方**设计的，与我们的请求形态天然相容。
//   国内节点还顺带解决了「主上与老公两台机器网络环境未必一致」的问题。
//
// ## 凭证放哪
// 只放在这里。前端代码可被 WebView 开发者工具直接查看，不该持凭证。
// （打包后两者同在一个 exe 内，逆向仍可提取 —— 故 token 泄露的最坏后果
//  必须是「被骚扰」而非「被夺权」，PushPlus 的 token 恰好符合这一档，
//  且可在个人中心随时重置。）
// ─────────────────────────────────────────────────────────────

/// 上报端点。用 batchSend 而非 send —— 后者只支持单渠道，
/// 而我们要「邮件留档 + 微信即时」，一次请求两处落脚。
const REPORT_ENDPOINT: &str = "https://www.pushplus.plus/batchSend";

/// 主上的 PushPlus token。
///
/// 凭证只落在这一行。前端可被 WebView 开发者工具查看，不该持凭证。
/// 泄露的最坏后果限于「别人能往主上邮箱/微信推消息」——骚扰级而非夺权级，
/// 且可在 PushPlus 个人中心随时重置（重置后需重新发一版客户端）。
const REPORT_TOKEN: &str = "";

/// 投递渠道。mail = 账号绑定的邮箱（留档、可检索）；wechat = 公众号（即时）。
/// 两者独立成败，一个挂了另一个照送。
const REPORT_CHANNEL: &str = "mail,wechat";

/// 单次推送内容上限。PushPlus 规定 2 万字，这里留足余量——
/// 超限会被服务端整条拒收，宁可截断也要让「错误现场」先送达。
const REPORT_MAX_CHARS: usize = 15_000;

/// 从报告正文里挑一行当推送标题，方便在收件箱里一眼分辨是哪一次故障。
///
/// 取「错误信息」那一行而非固定文案：报告是自动生成的，
/// 标题若永远是「扒谱助手问题报告」，主上收满一屏后根本无法检索。
fn report_title(report: &str) -> String {
    // 注意：报告里的冒号是全角「：」，与 render_diagnostic_report 保持一致
    const PREFIX: &str = "错误信息：";
    let raw = report
        .lines()
        .find_map(|l| l.strip_prefix(PREFIX))
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or("未能提取错误摘要");
    let short: String = raw.chars().take(60).collect();
    format!("扒谱异常：{}", short)
}

/// 把诊断报告推到项目方。未配置凭证时返回 unconfigured，由前端降级处理。
#[tauri::command]
pub async fn submit_diagnostic_report(report: String) -> Result<(), String> {
    if REPORT_TOKEN.trim().is_empty() {
        return Err("unconfigured：尚未配置上报凭证".into());
    }

    let title = report_title(&report);
    let mut content = report;
    if content.chars().count() > REPORT_MAX_CHARS {
        content = content.chars().take(REPORT_MAX_CHARS).collect();
        content.push_str("\n\n（报告过长，已截断）");
    }

    let payload = serde_json::json!({
        "token": REPORT_TOKEN,
        "title": title,
        "content": content,
        "channel": REPORT_CHANNEL,
        // txt 不转义 HTML：报告里必然出现 `<module>` 这类尖括号，
        // 走 html 模板会被吃掉半截
        "template": "txt",
    })
    .to_string();

    // ureq 是同步客户端，挪到阻塞线程池，别占住 async 运行时
    let sent = tauri::async_runtime::spawn_blocking(move || {
        ureq::post(REPORT_ENDPOINT)
            .set("Content-Type", "application/json")
            .timeout(std::time::Duration::from_secs(30))
            .send_string(&payload)
    })
    .await
    .map_err(|e| format!("上报线程异常：{}", e))?;

    match sent {
        Ok(resp) => {
            let text = resp.into_string().unwrap_or_default();
            // 注意语义：PushPlus 同步响应只代表「服务端收到请求」，
            // 真正的送达结果要另查流水号（查询接口需付费权限）。
            // 此处只拦下「明确失败」，把不确定留给「已提交」——
            // 宁可提示已提交也不要误报失败，否则小白会重复点。
            let v: Value = serde_json::from_str(&text).unwrap_or(Value::Null);

            // batchSend 的 data 是各渠道的结果数组；单渠道 send 时是流水号字符串
            let per_channel = v.get("data").and_then(Value::as_array).map(|arr| {
                arr.iter()
                    .any(|c| c.get("code").and_then(Value::as_i64) == Some(200))
            });

            let brief: String = text.chars().take(200).collect();
            match per_channel {
                // 至少一个渠道被受理
                Some(true) => Ok(()),
                // 服务端逐渠道给了结果，且全部失败
                Some(false) => Err(format!("全部渠道均被拒绝：{}", brief)),
                // 单渠道响应，看外层 code
                None => match v.get("code").and_then(Value::as_i64) {
                    Some(200) => Ok(()),
                    _ => Err(format!("服务端拒绝：{}", brief)),
                },
            }
        }
        Err(ureq::Error::Status(code, resp)) => {
            let brief: String = resp
                .into_string()
                .unwrap_or_default()
                .chars()
                .take(200)
                .collect();
            Err(format!("HTTP {}：{}", code, brief))
        }
        Err(e) => Err(format!("网络不可达：{}", e)),
    }
}
