"""把本地界面打包成可执行程序，让用户下载后双击就能用。

    python scripts/build_exe.py              # 目录模式，产出压缩包（默认，推荐）
    python scripts/build_exe.py --onefile    # 单文件 exe

为什么要打包：Python 是解释型语言，`.py` 只是文本，必须有解释器才能跑。
把解释器和依赖一起打进去之后，用户就不需要装 Python —— 这正是
"别的软件下载双击就能用"的原因。

两种模式的取舍：
  * **目录模式**（默认）：产出一个文件夹，内含 exe 与依赖库。运行时不需要解压，
    启动快，杀毒误报概率低，也不依赖临时目录的写入权限。
  * **单文件模式**：只有一个 exe，分发最省事；但每次启动都要先解压到临时目录，
    对临时目录权限敏感，也更容易被杀毒软件误判。

Windows 单文件版在受限环境下可能因解压被拦而启动失败，所以默认用目录模式。

产物是平台相关的：在 Windows 上构建只能得到 Windows 用的程序。

参考数据（DepMap 约 400MB）不打进包里，运行时自动获取或由用户放到程序旁的 data/。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEP = ";" if sys.platform.startswith("win") else ":"

# 需要引用 tsa 包来定位参考数据，因此把仓库根加入搜索路径
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打包本地界面为可执行程序")
    parser.add_argument("--onefile", action="store_true", help="产出单个 exe")
    parser.add_argument("--out", help="压缩包输出目录（默认放在仓库上一级）")
    opts = parser.parse_args(argv)

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("需要 PyInstaller：pip install pyinstaller", file=sys.stderr)
        return 1

    assets = ROOT / "tsa" / "webassets"
    if not assets.exists():
        print(f"找不到资源目录 {assets}", file=sys.stderr)
        return 1

    for stale in ("build", "dist"):
        path = ROOT / stale
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile" if opts.onefile else "--onedir",
        # 不留控制台窗口。双击后只出现应用窗口，观感与普通桌面软件一致。
        # 代价是启动输出与报错不再可见，因此 app_entry 会把它们写进日志文件，
        # 并在启动失败时弹出系统对话框。
        "--windowed",
        "--name",
        "target-audit",
        # 应用图标。不指定的话 PyInstaller 会用自己的默认图标，
        # 快捷方式和 exe 都会显示成那个东西。
        "--icon",
        str(ROOT / "assets" / "app.ico"),
        # 界面资源必须打进去，否则打包后读不到 CSS 与 JS。
        # 目标路径保持 tsa/webassets，与源码目录结构一致，
        # 这样 paths.asset_path("tsa", "webassets") 在两种运行方式下都成立。
        "--add-data",
        f"{assets}{SEP}tsa/webassets",
        # 单元测试也打进去，这样打包版自己就能跑 `target-audit.exe self-test`
        # 自证可用（评审拿到的是 exe，不是源码，得让它能自己验自己）。
        "--add-data",
        f"{ROOT / 'tests'}{SEP}tests",
        # 这几个模块只有开发脚本用得到，排除后体积明显下降
        "--exclude-module",
        "matplotlib",
        "--exclude-module",
        "numpy",
        "--exclude-module",
        "docx",
        "--exclude-module",
        "markdown_it",
        str(ROOT / "app_entry.py"),
    ]

    print("开始构建……")
    if subprocess.run(cmd, cwd=str(ROOT)).returncode != 0:
        print("构建失败。", file=sys.stderr)
        return 1

    exe_name = "target-audit.exe" if sys.platform.startswith("win") else "target-audit"

    if opts.onefile:
        exe = ROOT / "dist" / exe_name
        if not exe.exists():
            print("构建结束但没找到产物。", file=sys.stderr)
            return 1
        print(f"\n已生成 {exe}\n  大小 {exe.stat().st_size / 1e6:.1f} MB")
        print("  双击运行即可，会自动打开浏览器")
        return 0

    bundle = ROOT / "dist" / "target-audit"
    if not (bundle / exe_name).exists():
        print("构建结束但没找到产物。", file=sys.stderr)
        return 1

    # 附一个启动脚本。可执行文件自身会尝试打开浏览器，但打包环境下
    # webbrowser 有可能找不到可用浏览器。这个脚本用 cmd 的 start 打开地址，
    # 是 Windows 上最可靠的方式，作为兜底路径。
    if sys.platform.startswith("win"):
        (bundle / "启动.cmd").write_text(
            "@echo off\r\n"
            "cd /d \"%~dp0\"\r\n"
            "start \"\" \"target-audit.exe\"\r\n"
            "timeout /t 3 /nobreak >nul\r\n"
            "start \"\" \"http://127.0.0.1:8000\"\r\n",
            encoding="utf-8",
        )

    # 把参考数据一并放进包里，让用户开箱就是七个维度。
    # 紧凑矩阵 13.8MB 相对于 497MB 的官方 CSV 几乎不构成负担；
    # gnomAD 那张表只有 4.6MB，放进去可以省掉一次首次下载。
    from tsa.connectors.depmap import MODEL_PATTERNS, PACKED_FILE, resolve_data_file
    from tsa.connectors.gnomad import CONSTRAINT_FILE

    bundled: list[tuple[str, int]] = []
    targets: list[tuple[Path, Path]] = []
    if PACKED_FILE.exists():
        targets.append((PACKED_FILE, bundle / "data" / "depmap" / PACKED_FILE.name))
    model_file = resolve_data_file(MODEL_PATTERNS)
    if model_file is not None:
        targets.append((model_file, bundle / "data" / "depmap" / model_file.name))
    if CONSTRAINT_FILE.exists():
        targets.append((CONSTRAINT_FILE, bundle / "data" / "gnomad" / CONSTRAINT_FILE.name))

    for src, dest in targets:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        bundled.append((str(dest.relative_to(bundle)), dest.stat().st_size))

    if bundled:
        print("\n已随包附带参考数据：")
        for name, size in bundled:
            print(f"  {name}  {size / 1e6:.1f} MB")
    else:
        print("\n注意：没有找到参考数据，包内不含 DepMap 矩阵，")
        print("      用户将只能看到五个维度。先运行 python scripts/pack_depmap.py。")

    out_dir = Path(opts.out).resolve() if opts.out else ROOT.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = Path(
        shutil.make_archive(
            str(out_dir / "target-audit-windows-x64"), "zip", str(bundle)
        )
    )
    print(f"\n已生成压缩包 {archive}\n  大小 {archive.stat().st_size / 1e6:.1f} MB")
    print("  用户解压后双击 target-audit.exe 即可，不需要安装 Python")
    print("\n把这个压缩包作为附件上传到 GitHub Releases，不要提交进仓库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
