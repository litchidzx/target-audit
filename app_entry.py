"""打包入口，供 PyInstaller 使用。

用户不会直接运行这个文件 —— 它把命令行入口包装成可执行程序。

窗口模式下（打包时加了 --windowed）没有控制台，于是有两个必须处理的坑：

  1. **stdout / stderr 变成 None**，任何 print() 都会抛 AttributeError，
     程序在启动阶段就崩，而用户看不到任何信息。所以第一步先把输出重定向到日志文件。
  2. **出错时无处可见**，用户只看到"双击没反应"。所以致命错误要弹一个系统对话框，
     同时把完整堆栈写进日志，两者都指向同一个文件。
"""

from __future__ import annotations

import multiprocessing
import sys
import traceback
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_web import main  # noqa: E402

LOG_NAME = "target-audit.log"


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def redirect_output_to_log() -> Path | None:
    """没有控制台时把输出接到日志文件上。返回日志路径。"""
    if sys.stdout is not None and sys.stderr is not None:
        return None  # 有控制台（源码运行或命令行启动），保持原样
    log = app_dir() / LOG_NAME
    try:
        stream = open(log, "w", encoding="utf-8", buffering=1)
    except OSError:
        return None
    sys.stdout = stream
    sys.stderr = stream
    return log


def show_error_box(title: str, message: str) -> None:
    """弹一个系统对话框。没有控制台时，这是唯一能让用户看到问题的方式。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)  # MB_ICONERROR
    except Exception:  # noqa: BLE001 - 弹不出来也不能再抛异常
        pass


def run() -> int:
    log = redirect_output_to_log()
    try:
        print(f"启动中…… 可执行文件目录：{app_dir()}")
        return main(["--open"])
    except Exception:  # noqa: BLE001 - 到这一层说明启动失败，必须让用户知道
        detail = traceback.format_exc()
        print(detail, flush=True)
        hint = f"\n\n详细日志：{log}" if log else ""
        show_error_box(
            "靶点尽调 · 启动失败",
            f"程序没能正常启动。\n\n{detail.strip().splitlines()[-1]}{hint}",
        )
        return 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(run())
