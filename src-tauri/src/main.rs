// 扒谱桌面应用 — 二进制入口（薄壳）
//
// 业务逻辑全在 lib.rs 的 `run()`。此处只做最小化处理。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    musicxml_scribe_lib::run()
}
