/**
 * build.rs — 构建脚本
 *
 * 目前只做一件事：在**构建期**提示「包内 Python 运行时」是否就位。
 *
 * ## 为什么要在这里喊一声
 * 该运行时（src-tauri/runtime/python，约 45MB）不入 git（见 .gitignore），
 * 新克隆的仓库里没有它。缺了它，安装包依然能正常构建、正常安装，
 * 但用户在引导页点「安装」时会撞上「内置运行时缺失」——
 * 故障出现在**用户机器上**，而不是构建机上，排查成本极高。
 * 故在构建期就把这件事说清楚。
 *
 * ## 为什么只警告、不 panic
 * `cargo test`（契约测试）也会经过这里。不该因为一个二进制资源没拉，
 * 就让所有代码级验证全线阻塞——那是两件事，耦合起来只会让人绕开测试。
 */
use std::path::Path;

fn main() {
    let runtime = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("runtime")
        .join("python")
        .join("python.exe");

    if !runtime.is_file() {
        println!(
            "cargo:warning=包内 Python 运行时缺失（{}）。\
             用此产物打出的安装包，用户在引导页点「安装」会报「内置运行时缺失」。\
             请先执行：python engine/tools/fetch_runtime.py",
            runtime.display()
        );
    }

    // resources 变化要触发重打包判断
    println!("cargo:rerun-if-changed=runtime");
    println!("cargo:rerun-if-changed=tauri.conf.json");

    tauri_build::build()
}
