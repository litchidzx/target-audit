"""Connector 接口与 HTTP 客户端。

新增一个数据源只需要：继承 Connector、实现 fetch()、把结果按 make_record()
规约成 EvidenceRecord。下游的冲突检测和报告渲染完全不需要改。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..paths import data_root
from ..schema import Dimension, EvidenceRecord

DEFAULT_CACHE = data_root() / "cache"
USER_AGENT = "target-selectivity-audit/0.1 (research prototype)"

# 设为 1 时只读缓存、完全不发网络请求。
# 用途有二：一是无网环境下重放已有结果，二是让构建结果可复现
# （避免同一次构建里混进不同时间抓到的数据）。
OFFLINE_ENV = "TSA_OFFLINE"

# 连续多少次连接失败就判定为整体不可达。
# 断网时每个连接器都重试三次纯属空转：一轮 35 个靶点 × 6 个连接器
# 会产生上千次注定失败的请求，把构建拖到几十分钟。
CONNECTION_FAILURE_LIMIT = 3


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


class ConnectorError(RuntimeError):
    """数据源不可用。引擎会把它降级成"该维度缺失"，而不是整体失败。"""


class ConnectorNotConfigured(ConnectorError):
    """缺少本地数据文件或凭据。"""


@dataclass(frozen=True)
class GeneRef:
    symbol: str
    hgnc_id: str | None = None
    ensembl_id: str | None = None

    @classmethod
    def of(cls, symbol: str, **kw) -> "GeneRef":
        return cls(symbol=symbol.strip().upper(), **kw)


class Connector(ABC):
    name: str = "unnamed"
    dimensions: tuple[Dimension, ...] = ()

    @abstractmethod
    def fetch(self, req: Any) -> list[EvidenceRecord]:
        """返回该数据源贡献的全部证据。失败请抛 ConnectorError。"""


class HttpClient:
    """带磁盘缓存的极简 HTTP 客户端。

    对外部 API 做开发时缓存是必需的：既避免反复打对方接口，也让离线调试成为可能。
    """

    # 进程级状态：连续失败计数与熔断开关，所有实例共享。
    _consecutive_failures = 0
    _circuit_open = False

    def __init__(
        self,
        cache_dir: Path | None = None,
        ttl_seconds: int = 7 * 24 * 3600,
        offline: bool | None = None,
    ):
        self.cache_dir = Path(cache_dir or DEFAULT_CACHE)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self.offline = _env_flag(OFFLINE_ENV) if offline is None else offline

    def _cache_path(self, url: str) -> Path:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        return self.cache_dir / f"{key}.json"

    def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        use_cache: bool = True,
        retries: int = 3,
        timeout: int = 30,
    ) -> Any:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"

        path = self._cache_path(url)
        if use_cache and path.exists():
            age = time.time() - path.stat().st_mtime
            if age < self.ttl_seconds:
                return json.loads(path.read_text(encoding="utf-8"))

        if self.offline:
            raise ConnectorError(f"离线模式且缓存未命中：{url}")
        if HttpClient._circuit_open:
            raise ConnectorError(
                "网络已判定为不可达，本次构建不再尝试请求。"
                f"如需仅用缓存重放，设置 {OFFLINE_ENV}=1。"
            )

        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                if use_cache:
                    path.write_text(
                        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                    )
                HttpClient._consecutive_failures = 0
                return payload
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                # 连接被拒说明目标整体不可达，不是偶发抖动，重试没有意义。
                refused = isinstance(exc, urllib.error.URLError) and (
                    "refused" in str(exc).lower() or "10061" in str(exc)
                )
                if refused:
                    HttpClient._consecutive_failures += 1
                    if HttpClient._consecutive_failures >= CONNECTION_FAILURE_LIMIT:
                        HttpClient._circuit_open = True
                    break
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
        raise ConnectorError(f"请求失败 {url}: {last_error}")

    def post_json(self, url: str, body: dict[str, Any], *, timeout: int = 60) -> Any:
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ConnectorError(f"请求失败 {url}: {exc}") from exc
