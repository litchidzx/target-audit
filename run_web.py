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
import re
import sys
import threading
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="靶点尽调本地网页界面")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--mock", action="store_true", help="离线模式，用假数据")
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    cancers = cancer_choices(args.mock)
    handler = make_handler(args.mock, cancers)

    try:
        server = _Server((args.host, args.port), handler)
    except OSError as exc:
        print(
            f"无法绑定 {args.host}:{args.port} —— {exc}\n"
            f"  端口可能已被占用（例如先前启动的实例还没退出）。\n"
            f"  换一个端口：python run_web.py --port {args.port + 1}",
            file=sys.stderr,
            flush=True,
        )
        return 1
    url = f"http://{args.host}:{args.port}/"

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
    print("   关闭这个窗口即停止程序")
    print("  " + "─" * 54)
    print()
    sys.stdout.flush()

    if args.open:
        def _open_browser() -> None:
            try:
                if not webbrowser.open(url):
                    print("（浏览器未能自动打开，请手动复制上面的地址）", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"（浏览器未能自动打开：{exc}，请手动复制上面的地址）", flush=True
                )

        threading.Timer(0.8, _open_browser).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
