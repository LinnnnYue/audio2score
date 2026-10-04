; installer-hooks.nsh — Tauri NSIS 安装器钩子
;
; ## 为什么 PREINSTALL / PREUNINSTALL 都要杀进程
; 本应用有长驻 Python 引擎子进程（runtime\python\python.exe）。
; 主程序崩溃或被强杀后，引擎会变成孤儿进程，继续占着安装目录里的
; .pyd / .dll —— 此时覆盖安装或卸载，NSIS 写这些文件就会弹
; “Error opening file for writing”（线上案例：runtime\python\DLLs\_bz2.pyd，
; 用户侧表现为安装到一半弹错误框，只能中止/忽略，装完也是残废）。
;
; ## 为什么按「路径」杀，而不是按「进程名」
; taskkill /IM python.exe 会误杀用户自己的 Python（本项目用户里
; 有开发者）；只杀主程序也救不了孤儿引擎 —— 父进程已死，taskkill /T
; 够不到。唯一精确的判据：进程的可执行文件位于安装目录之内。
;
; ## 实现方式（踩坑实录）
; 第一版把整段 PowerShell 塞进 -Command，过了 NSIS → CreateProcess →
; PowerShell 三层引号解析，实测 PowerShell exit=800，一个进程都没杀到。
; 教训：多层字符串拼接里嵌脚本 = 脆弱通道。改为钩子自己把 .ps1 写到
; 临时目录再用 -File 执行 —— -File 只收一个路径，零嵌套。
; 脚本内容纯 ASCII；安装目录（含中文）经进程环境变量传入，全程
; UTF-16，不经过任何代码页。脚本内一律用 $$ 转义，避免 NSIS 把
; $dir/$p 当自己的变量引用。

; ## 为什么还要管引擎目录（%LOCALAPPDATA%\bapu）
; 引擎**不在**安装目录里：默认落 `%LOCALAPPDATA%\bapu\engine`，用户还能改到别的盘。
; 只按 $INSTDIR 匹配，等于对引擎进程完全不设防 —— 线上第二例即由此而来：
; 卸载旧版 → 装新版 → 新版首启装引擎，报
;   [WinError 32] 另一个程序正在使用此文件…\pretty_midi\TimGM6mb.sf2
; 故判据扩展为「路径在 $INSTDIR 内 **或** 在 %LOCALAPPDATA%\bapu 内」。
; 注：引擎侧另有等价清理（bootstrap.py 的 kill_engine_processes），
; 两者互补 —— 钩子只在安装/卸载瞬间跑，装引擎则可能发生在应用运行中。

!macro BAPU_KILL_STALE_PROCESSES
  ; 两个根目录（含中文）写入本进程环境变量，PowerShell 子进程继承之，
  ; 全程 UTF-16，不经过任何代码页。
  System::Call 'kernel32::SetEnvironmentVariable(t "BAPU_INSTALL_DIR", t "$INSTDIR")'
  System::Call 'kernel32::SetEnvironmentVariable(t "BAPU_DATA_DIR", t "$LOCALAPPDATA\bapu")'

  ; 生成杀进程脚本（内容纯 ASCII）。
  ;
  ; ## 为什么用 Win32_Process 而不是 Get-Process 的 .Path
  ; 实测：NSIS 启动的 PowerShell 里，Get-Process 能枚举到 488 个进程，
  ; 但 .Path 属性全部为 null —— PS 5.1 的这个 ScriptProperty 要打开
  ; 目标进程句柄取映像路径，在该上下文静默失败（外部手动跑同样的
  ; 脚本却能拿到，纯环境差异）。
  ; Win32_Process.ExecutablePath 由内核在进程创建时记录，查询它不需要
  ; 逐个开句柄，稳。
  FileOpen $0 "$TEMP\bapu_kill.ps1" w
  FileWrite $0 "$$dirs = @($$env:BAPU_INSTALL_DIR, $$env:BAPU_DATA_DIR)$\r$\n"
  FileWrite $0 "$$procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {$\r$\n"
  FileWrite $0 "  $$p = $$_.ExecutablePath$\r$\n"
  FileWrite $0 "  if (-not $$p) { return $$false }$\r$\n"
  FileWrite $0 "  foreach ($$d in $$dirs) {$\r$\n"
  FileWrite $0 "    if ($$d -and $$p.StartsWith($$d, [StringComparison]::OrdinalIgnoreCase)) { return $$true }$\r$\n"
  FileWrite $0 "  }$\r$\n"
  FileWrite $0 "  return $$false$\r$\n"
  FileWrite $0 "})$\r$\n"
  FileWrite $0 "foreach ($$p in $$procs) {$\r$\n"
  FileWrite $0 "  Stop-Process -Id $$p.ProcessId -Force -ErrorAction SilentlyContinue$\r$\n"
  FileWrite $0 "}$\r$\n"
  FileClose $0

  nsExec::ExecToLog 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$TEMP\bapu_kill.ps1"'
  Pop $0 ; 取走退出码，避免栈残留
  ; 注：nsExec 在此返回 760/800 等非零值，但 ps1 确实执行完毕（排障时
  ; 靠 ps1 自写报告文件确认）。不要把这个数当成败依据。
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro BAPU_KILL_STALE_PROCESSES
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro BAPU_KILL_STALE_PROCESSES
!macroend
