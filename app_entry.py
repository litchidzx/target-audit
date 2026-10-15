"""打包入口，供 PyInstaller 使用。

用户不会直接运行这个文件 —— 它只是把命令行入口包装成可执行程序。
双击生成的 exe 后会自动打开浏览器，无需终端操作。
"""

from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

# 源码方式运行时需要把仓库根加入搜索路径；打包后模块已在包内，不需要
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_web import main  # noqa: E402

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main(["--open"]))
