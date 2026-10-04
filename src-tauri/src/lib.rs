//! 扒谱桌面应用 — 库入口
//!
//! 业务逻辑（引擎 sidecar 管理）在此，bin 侧 `main.rs` 只做薄壳转发。

pub mod engine;

use engine::{
    cancel_transcribe, get_env_info, get_modes, open_with_musescore, probe_audio,
    reveal_in_folder, start_transcribe,
};

/// 构建并运行 Tauri 应用。由 `main.rs` 调用。
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(engine::EngineState::default())
        .invoke_handler(tauri::generate_handler![
            start_transcribe,
            cancel_transcribe,
            probe_audio,
            get_env_info,
            get_modes,
            reveal_in_folder,
            open_with_musescore,
        ])
        .setup(|_app| {
            use tauri::Manager;

            // ── 无边框窗口（主上审美基线的锚定项，不可退回系统标题栏）──
            //
            // 踩坑实录：曾写 `win.set_corner_radius(CornerRadius::Rounded(12.0))`，
            // 编译报 `no method named set_corner_radius`。查 tauri 2.12.1 源码
            // 确认 `WebviewWindow` 只有 `set_decorations`，**无圆角 API**——
            // 那是凭记忆编的，不存在的接口。
            // 正解：圆角与阴影交给前端 CSS（#app 的 border-radius + box-shadow），
            //        Rust 侧只负责去掉系统装饰并开透明。
            if let Some(win) = _app.get_webview_window("main") {
                let _ = win.set_decorations(false);
                let _ = win.set_shadow(true);
                // 极窄窗口下布局会塌，给一个下限
                let _ = win.set_min_size(Some(tauri::LogicalSize::new(1024.0, 700.0)));
            }

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("启动失败");
}
