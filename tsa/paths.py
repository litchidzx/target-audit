"""路径解析。

打包成单文件可执行程序后，`__file__` 指向的是解压出来的临时目录，
而参考数据应该放在用户看得见、可写的位置。两者必须分开处理，
否则会出现"源码能跑、exe 找不到文件"这种情况。

两个概念：
  * 资源根（只读）—— 打包进程序里的东西，例如 webassets 下的 CSS 与 JS
  * 数据根（可写）—— 参考数据与缓存。冻结时放在可执行文件旁边，
    用户可以直接往那个目录里丢 DepMap 文件。
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包出来的可执行文件里。"""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """打包进程序的只读资源所在目录。

    源码运行时返回仓库根，冻结时返回 PyInstaller 的解压目录。
    两者内部的相对布局保持一致（都以 tsa/ 开头），所以 asset_path()
    在两处都指向同一个相对位置，不需要分支判断。
    """
    if is_frozen():
        # PyInstaller 单文件模式会把资源解压到 _MEIPASS
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


def data_root() -> Path:
    """参考数据与缓存的根目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent / "data"
    return Path(__file__).resolve().parents[1] / "data"


def asset_path(*parts: str) -> Path:
    return resource_root().joinpath(*parts)
