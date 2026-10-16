"""本地网页界面：输入任意基因，当场跑审计。

    python run_web.py                  # 打开 http://127.0.0.1:8000
    python run_web.py --mock           # 离线模式，用假数据验证界面
    python run_web.py --port 8080      # 换端口
    python run_web.py --open           # 启动后自动打开浏览器

只有标准库，没有第三方依赖。服务端在请求时实时调用引擎，
所以这里可以审计任意基因 —— 这一点与 docs/ 里那份预计算结果集不同。

为什么需要它：docs/ 里的工作台是"结果集"，只能看已经算好的靶点。
而研究者真正要问的是"我这个靶点怎么样"，所以需要一个能即时计算的入口。
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).parent))

from tsa.connectors.base import ConnectorError  # noqa: E402
from tsa.engine import AuditRequest, run_audit  # noqa: E402
from tsa.liveui import full_css, render_error, render_form, render_result  # noqa: E402
from tsa.webexport import CANCER_ZH  # noqa: E402

GENE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9\-]{0,14}$")

# 默认癌种列表：以 TCGA 收录且我们已验证过的为主
DEFAULT_CANCERS = ("PAAD", "LUAD", "COAD", "STAD", "BRCA", "LIHC", "OV", "KIRC", "HNSC", "SKCM")

SAMPLES = (("KRAS", "PAAD", "胰腺癌"), ("EGFR", "LUAD", "肺腺癌"), ("BRCA2", "BRCA", "乳腺癌"))


def cancer_choices(mock: bool) -> list[tuple[str, str]]:
    if mock:
        return [("PAAD", "胰腺癌"), ("LUAD", "肺腺癌")]
    return [(code, CANCER_ZH.get(code, code)) for code in DEFAULT_CANCERS]


def build_connectors(mock: bool):
    if mock:
        from tsa.mock import mock_connector

        return [mock_connector()]
    from tsa.connectors.chembl import ChEMBLConnector
    from tsa.connectors.depmap import DepMapConnector
    from tsa.connectors.faers import FaersConnector
    from tsa.connectors.gnomad import GnomadConnector
    from tsa.connectors.hpa import HPAConnector
    from tsa.connectors.tcga import TCGAConnector

    return [
        DepMapConnector(),
        TCGAConnector(),
        HPAConnector(),
        GnomadConnector(),
        ChEMBLConnector(),
        FaersConnector(),
    ]


def make_handler(mock: bool, cancers: list[tuple[str, str]]):
    # 连接器实例在进程内复用，HTTP 缓存因此是共享的 —— 同一个基因第二次查询会很快。
    connectors = build_connectors(mock)
    valid = {code for code, _ in cancers}

    class Handler(BaseHTTPRequestHandler):
        server_version = "target-audit-web/0.1"

        def log_message(self, fmt, *args):  # 默认的日志太吵，只留错误
            if not str(args[0]).startswith(("2", "3")):
                sys.stderr.write("  %s\n" % (fmt % args))

        def _send(self, body: str, status: int = 200, ctype: str = "text/html; charset=utf-8"):
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            try:
                self.server.last_request = time.time()
            except Exception:  # noqa: BLE001
                pass

            if parsed.path == "/style.css":
                try:
                    self._send(full_css(), ctype="text/css; charset=utf-8")
                except Exception as exc:  # noqa: BLE001
                    # 样式读不到时不能直接断连 —— 浏览器会显示成"服务器无响应"，
                    # 而真正的原因（例如打包后资源路径不对）就完全看不见了。
                    sys.stderr.write(f"样式读取失败：{exc!r}\n")
                    self._send(
                        f"/* 样式读取失败：{exc} */", ctype="text/css; charset=utf-8"
                    )
                return
            if parsed.path == "/health":
                self._send("ok", ctype="text/plain; charset=utf-8")
                return
            if parsed.path == "/shutdown":
                self._send(
                    "<!doctype html><meta charset='utf-8'>"
                    "<body style='font-family:sans-serif;padding:48px;color:#111726'>"
                    "<h2>已停止</h2><p>后台服务已经关闭，端口也已释放，"
                    "现在可以安全地关闭这个页面。</p></body>"
                )
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if parsed.path == "/audit":
                self._handle_audit(params)
                return
            if parsed.path in ("/", "/index.html"):
                self._send(
                    render_form(
                        cancers,
                        gene=(params.get("gene", [""])[0] or "").upper(),
                        cancer=(params.get("cancer", [""])[0] or ""),
                        samples=SAMPLES if not mock else None,
                    )
                )
                return
            self._send(render_error("找不到这个地址。", hint="回首页重新输入即可。"), status=404)

        def _handle_audit(self, params: dict[str, list[str]]):
            gene = (params.get("gene", [""])[0] or "").strip().upper()
            cancer = (params.get("cancer", [""])[0] or "").strip().upper()

            if not GENE_RE.match(gene):
                self._send(
                    render_error(
                        "基因符号看起来不对。",
                        hint="请输入 HGNC 基因符号，例如 KRAS、TP53、CDKN2A。不要填蛋白名或药物名。",
                    ),
                    status=400,
                )
                return
            if cancer not in valid:
                self._send(
                    render_error(
                        f"暂不支持癌种 {cancer or '（空）'}。",
                        hint="可用癌种：" + "、".join(f"{z}（{c}）" for c, z in cancers),
                    ),
                    status=400,
                )
                return

            try:
                card = run_audit(AuditRequest(gene=gene, cancer=cancer), connectors)
            except ConnectorError as exc:
                self._send(
                    render_error(
                        "数据源暂时不可用。",
                        hint=f"{exc}\n如果刚断过网，稍后重试；也可以先看看已算好的结果集。",
                    ),
                    status=503,
                )
                return
            except Exception as exc:  # noqa: BLE001
                self._send(
                    render_error("审计过程中出错了。", hint=str(exc)[:300]), status=500
                )
                return
            self._send(render_result(card))

    return Handler


class _Server(ThreadingHTTPServer):
    """在 Windows 上必须关掉 SO_REUSEADDR。

    默认开启时，Windows 允许两个进程同时绑定同一个端口 —— 表面上第二次启动
    也"成功"了，实际请求会被两个版本的服务随机分流，排查起来极其困难
    （同一个地址一会儿正常一会儿报错）。关掉之后端口被占用会直接报错。
    """

    allow_reuse_address = not sys.platform.startswith("win")

    # 最近一次收到请求的时间。空闲看门狗据此判断是否该自动退出。
    last_request: float = 0.0


def find_browser_exe() -> Path | None:
    """找一个基于 Chromium 的浏览器，用于以「应用模式」打开界面。"""
    if not sys.platform.startswith("win"):
        return None
    candidates = []
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if not base:
            continue
        candidates += [
            Path(base) / "Microsoft/Edge/Application/msedge.exe",
            Path(base) / "Google/Chrome/Application/chrome.exe",
        ]
    for path in candidates:
        if path.exists():
            return path
    return None


def open_app_window(url: str) -> bool:
    """以应用模式打开界面：没有地址栏、没有标签页，看起来就是独立程序。

    这是 VS Code、Slack 这类软件的通用做法 —— 网页技术画界面，外面套一层窗口。
    区别只是它们自带浏览器内核，我们借用系统已装的 Edge，因此不增加任何依赖。

    为什么不用 pywebview 之类的专用壳：它需要额外安装依赖，而目标环境
    （评委、同学的电脑）未必能联网装包。用系统自带的浏览器最稳妥。
    """
    exe = find_browser_exe()
    if exe is None:
        return False
    try:
        subprocess.Popen(
            [str(exe), f"--app={url}", "--window-size=1280,900"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"已以应用窗口方式打开（{exe.name}）。", flush=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def open_browser(url: str) -> bool:
    """尽最大努力用系统默认浏览器打开地址，并如实报告结果。

    单一方式不够可靠：打包后 webbrowser 有时找不到可用浏览器而静默返回 False，
    使用者看到的就是"双击了没反应"。所以这里依次尝试几种方法，
    任何一种成功就返回；全部失败则明确提示手动打开。
    """
    # 首选应用模式：用户体验上就是一个独立程序窗口，而不是浏览器标签页
    if open_app_window(url):
        return True

    attempts: list[tuple[str, object]] = []
    if sys.platform.startswith("win"):
        # Windows 上最直接的方式：交给系统外壳处理默认浏览器
        attempts.append(("os.startfile", lambda: os.startfile(url)))  # type: ignore[attr-defined]
    attempts.append(("webbrowser", lambda: webbrowser.open(url)))
    if sys.platform.startswith("win"):
        attempts.append(
            (
                "cmd start",
                lambda: subprocess.Popen(
                    ["cmd", "/c", "start", "", url],
                    shell=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ),
            )
        )

    for name, action in attempts:
        try:
            result = action()  # type: ignore[operator]
            if result is False:  # webbrowser 用 False 表示没找到浏览器
                continue
            print(f"已用 {name} 打开浏览器。", flush=True)
            return True
        except Exception:  # noqa: BLE001 - 换下一种方式
            continue

    print()
    print("  浏览器未能自动打开。请手动复制下面这行到浏览器地址栏：")
    print(f"      {url}")
    print()
    sys.stdout.flush()
    return False


def is_our_instance(host: str, port: int, timeout: float = 2.0) -> bool:
    """端口被占用时，判断占用者是不是本程序的另一个实例。

    双击第二次是很常见的操作（用户忘了第一次还开着）。此时若直接报"端口被占用"
    会让人困惑，正确做法是直接打开已经在运行的那个实例。
    """
    import urllib.request

    try:
        url = f"http://{host}:{port}/health"
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200 and response.read().decode().strip() == "ok"
    except Exception:  # noqa: BLE001
        return False


def watch_idle(server: "_Server", timeout_seconds: int) -> None:
    """空闲太久就自动退出。

    窗口模式没有控制台，用户关掉应用窗口后进程会留在后台占着端口。
    看门狗保证它最终会自己消失，而不是变成僵尸进程。
    """
    while True:
        time.sleep(30)
        idle = time.time() - getattr(server, "last_request", 0.0)
        if idle >= timeout_seconds:
            print(f"已空闲 {idle / 60:.0f} 分钟，自动退出。", flush=True)
            server.shutdown()
            return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="靶点尽调本地网页界面")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--mock", action="store_true", help="离线模式，用假数据")
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    parser.add_argument(
        "--idle-minutes",
        type=int,
        default=30,
        help="空闲多少分钟后自动退出（0 表示不自动退出）",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    cancers = cancer_choices(args.mock)
    handler = make_handler(args.mock, cancers)

    try:
        server = _Server((args.host, args.port), handler)
    except OSError as exc:
        # 端口被占用：如果占用者就是本程序，直接打开它，不要把用户挡在门外。
        if is_our_instance(args.host, args.port):
            print("检测到程序已经在运行，直接打开现有实例。", flush=True)
            open_browser(f"http://{args.host}:{args.port}/")
            return 0
        print(
            f"无法绑定 {args.host}:{args.port} —— {exc}\n"
            f"  端口被其他程序占用了。换一个端口重试：\n"
            f"     python run_web.py --port {args.port + 1}\n"
            f"  可执行文件版请改启动脚本里的端口号。",
            file=sys.stderr,
            flush=True,
        )
        return 1
    url = f"http://{args.host}:{args.port}/"

    # 有没有真正的控制台。窗口模式（--windowed 打包）下 stdout 被接到日志文件，
    # 此时"关闭这个窗口即停止程序"这句话就不成立了，必须换成别的指引。
    has_console = sys.stdout is not None and sys.stdout.isatty()

    # 启动信息必须立刻可见：
    #   * flush=True —— 不加的话输出会被缓冲，双击运行时窗口长时间一片空白，
    #     使用者会以为程序没启动。这个坑真实发生过。
    #   * 单独成框 —— 万一浏览器没自动弹出，这几行是唯一的线索。
    print()
    print("  " + "─" * 54)
    print("   靶点尽调 · 实时审计")
    print("  " + "─" * 54)
    print(f"   请在浏览器中打开：  {url}")
    print()
    if args.mock:
        print("   当前为离线模式，结果来自内置假数据")
    else:
        print("   首次查询一个基因约 20-30 秒，之后走缓存")
    if has_console:
        print("   关闭这个窗口即停止程序")
    else:
        print("   使用完毕后，点界面右上角的「退出程序」即可关闭（也会空闲自动退出）")
    print("  " + "─" * 54)
    print()
    sys.stdout.flush()

    if args.idle_minutes > 0:
        threading.Thread(
            target=watch_idle,
            args=(server, args.idle_minutes * 60),
            daemon=True,
        ).start()

    if args.open:
        threading.Timer(0.8, lambda: open_browser(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
