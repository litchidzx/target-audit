"""下载需要本地缓存的参考数据。

目前只有一个：gnomAD 的基因级约束表（4.6MB）。
它会缓存到 data/gnomad/，之后所有查询都在本地完成，不依赖在线接口。

用法：
    python scripts/fetch_reference_data.py
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tsa.connectors.gnomad import CONSTRAINT_FILE, CONSTRAINT_URL  # noqa: E402

USER_AGENT = "target-selectivity-audit/0.1 (research)"


def download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response:
        total = int(response.headers.get("Content-Length") or 0)
        target.write_bytes(response.read())
    size = target.stat().st_size
    print(f"  {target.name}  {size / 1e6:.1f} MB" + (f"（声明 {total / 1e6:.1f} MB）" if total else ""))


def main() -> int:
    if CONSTRAINT_FILE.exists():
        print(f"已存在，跳过：{CONSTRAINT_FILE}")
        return 0
    print("下载参考数据：")
    try:
        download(CONSTRAINT_URL, CONSTRAINT_FILE)
    except Exception as exc:  # noqa: BLE001
        print(f"  失败：{exc}")
        return 1
    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
