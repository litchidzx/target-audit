"""Connector 接口与 HTTP 客户端。

新增一个数据源只需要：继承 Connector、实现 fetch()、把结果按 make_record()
规约成 EvidenceRecord。下游的冲突检测和报告渲染完全不需要改。
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..schema import Dimension, EvidenceRecord

DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "cache"
USER_AGENT = "target-selectivity-audit/0.1 (research prototype)"


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

    def __init__(self, cache_dir: Path | None = None, ttl_seconds: int = 7 * 24 * 3600):
        self.cache_dir = Path(cache_dir or DEFAULT_CACHE)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds

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
                return payload
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
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
