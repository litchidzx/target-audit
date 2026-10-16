"""打包入口，供 PyInstaller 使用。

用户不会直接运行这个文件 —— 它把命令行入口包装成可执行程序。

窗口模式下（打包时加了 --windowed）没有控制台，于是有两个必须处理的坑：

  1. **stdout / stderr 变成 None**，任何 print() 都会抛 AttributeError，
     程序在启动阶段就崩，而用户看不到任何信息。所以第一步先把输出重定向到日志文件。
  2. **出错时无处可见**，用户只看到"双击没反应"。所以致命错误要弹一个系统对话框，
     同时把完整堆栈写进日志，两者都指向同一个文件。

同一个 exe 兼顾两种用法：

  * 不带参数双击 —— 启动图形界面；
  * 带子命令（audit / self-test / doctor / demo）—— 复用命令行入口。
    图形子系统进程不会自动继承终端，所以要显式 AttachConsole 到父进程的控制台，
    否则输出会落进日志文件，在命令行里看起来像"什么都没发生"。
"""

from __future__ import annotations

import multiprocessing
import os
import sys
import traceback
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_web import main  # noqa: E402

LOG_NAME = "target-audit.log"

# 认得出的子命令就转交命令行入口，其余参数（包括无参数）走图形界面。
CLI_COMMANDS = ("audit", "self-test", "doctor", "demo")


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


def attach_parent_console() -> bool:
    """把输出接回调用方的终端。

    打包成图形子系统程序后，进程自身不带控制台，`print()` 会无处可去。
    从命令行启动时，父进程的终端仍然存在，AttachConsole 可以接上去，
    这样 `target-audit.exe audit ...` 的输出就和普通命令行程序一样。
    双击启动时没有父控制台，这里返回 False，改由日志文件兜底。

    设 `TSA_CLI_LOG=1` 可以强制不接终端，输出全部写进日志文件；
    在终端行为异常时这是个可用的退路。
    """
    if os.environ.get("TSA_CLI_LOG"):
        return False
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes

        ATTACH_PARENT_PROCESS = -1
        if not ctypes.windll.kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
            return False
        stream = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stdout = stream
        sys.stderr = stream
        return True
    except Exception:  # noqa: BLE001 - 接不上终端不影响命令本身执行
        return False


def show_error_box(title: str, message: str) -> None:
    """弹一个系统对话框。没有控制台时，这是唯一能让用户看到问题的方式。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)  # MB_ICONERROR
    except Exception:  # noqa: BLE001 - 弹不出来也不能再抛异常
        pass


def run() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] in CLI_COMMANDS:
        attach_parent_console()
        log = redirect_output_to_log()
        if sys.stdout is None and log is not None:
            # 既没有终端也写不了日志，至少别让程序崩在 print 上
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
            sys.stderr = sys.stdout
        try:
            from run import main as cli_main

            return cli_main(argv)
        except SystemExit:
            raise  # --help、参数填错等情形，argparse 已经给出提示和退出码
        except Exception:
            # 异常绝不能外抛：图形界面程序里没人接管它，PyInstaller 会弹一个
            # "Failed to execute script" 的对话框等用户点确定，命令行里看起来就是卡死。
            detail = traceback.format_exc()
            print(detail, flush=True)
            if log is not None:
                print(f"详细日志：{log}", flush=True)
            return 1

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
