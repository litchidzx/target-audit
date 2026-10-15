"""gnomAD：人类遗传学耐受性。

贡献 GENETIC_CONSTRAINT 维度。用人类的天然实验预判毒性 —— 人类群体中
携带功能缺失变异的人如果活得很好，说明抑制这个靶点大概率可以耐受；
反过来，如果这个基因对功能缺失高度不耐受，抑制它就要格外小心。

实现方式刻意不用在线 API。gnomAD 的 GraphQL 接口在本环境持续返回 429，
而且在线查询不利于复现。改用官方发布的基因级约束表：

    https://storage.googleapis.com/gcp-public-data--gnomad/release/2.1.1/
        constraint/gnomad.v2.1.1.lof_metrics.by_gene.txt.bgz

这个文件 4.6MB，含全部基因的 pLI、LOEUF（oe_lof_upper）等指标。
下载一次缓存到本地，之后所有查询都在本地完成，结果完全可复现。

用 scripts/fetch_reference_data.py 下载。

方向：功能缺失越不耐受，对靶点越不利，所以 high_is_favorable=False。
分位在全部基因内部计算 —— 这是真正的维度内分位，不是人为设定的等级。
"""

from __future__ import annotations

import csv
import gzip
import io
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import urllib.request

from ..normalize import make_record
from ..paths import data_root
from ..schema import Dimension, EvidenceRecord
from .base import Connector, ConnectorNotConfigured, HttpClient

log = logging.getLogger("tsa.connectors.gnomad")

DATA_DIR = data_root() / "gnomad"
CONSTRAINT_FILE = DATA_DIR / "gnomad.v2.1.1.lof_metrics.by_gene.txt.bgz"
CONSTRAINT_URL = (
    "https://storage.googleapis.com/gcp-public-data--gnomad/release/2.1.1/"
    "constraint/gnomad.v2.1.1.lof_metrics.by_gene.txt.bgz"
)

# 首次使用时自动下载约束表。这个文件只有 4.6MB，比很多网页的素材还小，
# 让使用者为此专门跑一条命令是不必要的门槛。DepMap 那个 400MB 不做自动下载，
# 体积太大，而且跑不到也不影响系统降级运行。
AUTODOWNLOAD_ENV = "TSA_NO_AUTODOWNLOAD"

# pLI 高于此值视为对功能缺失不耐受
PLI_INTOLERANT = 0.9
# LOEUF 低于此值视为对功能缺失不耐受
LOEUF_INTOLERANT = 0.35


@dataclass
class Constraint:
    gene: str
    pLI: float | None
    loeuf: float | None
    oe_lof: float | None
    obs_lof: int | None
    exp_lof: float | None


_CACHE: dict[str, Constraint] = {}
_LOEUF_DISTRIBUTION: list[float] = []


def _to_float(value: str | None) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def ensure_constraint_file(path: Path = CONSTRAINT_FILE, *, allow_download: bool = True) -> Path:
    """确保本地有约束表；缺了就自动下载一次。

    这是本项目唯一会在运行时写磁盘的地方，只为了替使用者省掉一条手工命令。
    设置 TSA_NO_AUTODOWNLOAD=1 可以关掉自动下载，改由
    scripts/fetch_reference_data.py 显式获取。
    """
    if path.exists():
        return path
    if not allow_download or os.environ.get(AUTODOWNLOAD_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise ConnectorNotConfigured(
            f"缺少 {path}。运行 python scripts/fetch_reference_data.py 下载。"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    log.warning("首次使用 gnomAD，正在下载约束表（约 4.6MB）……")
    request = urllib.request.Request(
        CONSTRAINT_URL, headers={"User-Agent": "target-selectivity-audit/0.1"}
    )
    tmp = path.with_suffix(path.suffix + ".part")
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            tmp.write_bytes(response.read())
        tmp.replace(path)
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise ConnectorNotConfigured(
            f"gnomAD 约束表下载失败：{exc}\n"
            f"可以稍后重试，或手动下载后放到 {path}：\n  {CONSTRAINT_URL}"
        ) from exc
    log.warning("gnomAD 约束表已就绪（%.1f MB）", path.stat().st_size / 1e6)
    return path


def _load(path: Path = CONSTRAINT_FILE) -> None:
    """把约束表读进内存缓存。只做一次。"""
    global _CACHE, _LOEUF_DISTRIBUTION
    if _CACHE:
        return
    path = ensure_constraint_file(path)

    raw = path.read_bytes()
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as fh:
        text = fh.read().decode("utf-8", "replace")

    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    distribution: list[float] = []
    cache: dict[str, Constraint] = {}
    for row in reader:
        gene = (row.get("gene") or "").strip().upper()
        if not gene:
            continue
        constraint = Constraint(
            gene=gene,
            pLI=_to_float(row.get("pLI")),
            loeuf=_to_float(row.get("oe_lof_upper")),
            oe_lof=_to_float(row.get("oe_lof")),
            obs_lof=int(_to_float(row.get("obs_lof")) or 0) or None,
            exp_lof=_to_float(row.get("exp_lof")),
        )
        cache[gene] = constraint
        if constraint.loeuf is not None:
            distribution.append(constraint.loeuf)

    distribution.sort()
    _CACHE = cache
    _LOEUF_DISTRIBUTION = distribution


def _percentile_rank(value: float, sorted_values: list[float]) -> float:
    """在已排序的列表里算分位，用二分查找。"""
    import bisect

    n = len(sorted_values)
    if n == 0:
        raise ValueError("参考分布为空")
    lo = bisect.bisect_left(sorted_values, value)
    hi = bisect.bisect_right(sorted_values, value)
    return (lo + 0.5 * (hi - lo)) / n


def lookup(gene: str, path: Path = CONSTRAINT_FILE) -> Constraint:
    _load(path)
    constraint = _CACHE.get(gene.upper())
    if constraint is None:
        raise ConnectorNotConfigured(f"gnomAD 约束表中找不到 {gene}")
    return constraint


def summarize(gene: str, constraint: Constraint) -> list[EvidenceRecord]:
    """纯函数：约束指标 -> 一条 GENETIC_CONSTRAINT 证据。

    LOEUF 越低越不耐受。为了让"高 = 更不耐受"（配合 high_is_favorable=False），
    这里用 1 减去 LOEUF 在全部基因中的分位作为不耐受程度。
    """
    if constraint.loeuf is None:
        raise ConnectorNotConfigured(f"{gene} 在 gnomAD 约束表中没有 LOEUF 值")

    loeuf_pct = _percentile_rank(constraint.loeuf, _LOEUF_DISTRIBUTION)
    intolerance = 1.0 - loeuf_pct

    flags = []
    if constraint.pLI is not None and constraint.pLI >= PLI_INTOLERANT:
        flags.append(f"pLI {constraint.pLI:.3f} 达到不耐受阈值")
    if constraint.loeuf <= LOEUF_INTOLERANT:
        flags.append(f"LOEUF {constraint.loeuf:.3f} 低于不耐受阈值")

    statement = (
        f"{gene} 的功能缺失约束：pLI {constraint.pLI if constraint.pLI is None else round(constraint.pLI, 3)}、"
        f"LOEUF {round(constraint.loeuf, 3)}。"
        f"以 gnomAD 全部 {len(_LOEUF_DISTRIBUTION)} 个基因为参照，"
        f"该基因对功能缺失的不耐受程度位于第 {intolerance:.0%} 分位"
        f"（分位越高越不耐受，抑制它的风险越大）。"
    )
    if flags:
        statement += "判定为对功能缺失不耐受（" + "；".join(flags) + "）。"
    else:
        statement += "未达到不耐受阈值。"

    return [
        make_record(
            dimension=Dimension.GENETIC_CONSTRAINT,
            source="gnomAD v2.1.1 constraint",
            metric="lof_intolerance_percentile",
            statement=statement,
            percentile=intolerance,
            value=round(constraint.loeuf, 4),
            unit="LOEUF",
            cohort=f"gnomAD {len(_LOEUF_DISTRIBUTION)} genes",
            url=f"https://gnomad.broadinstitute.org/gene/{gene}",
            confidence=0.75 if constraint.obs_lof else 0.4,
            raw={
                "pLI": constraint.pLI,
                "LOEUF": constraint.loeuf,
                "oe_lof": constraint.oe_lof,
                "obs_lof": constraint.obs_lof,
                "exp_lof": constraint.exp_lof,
                "intolerance_percentile": round(intolerance, 4),
            },
        )
    ]


class GnomadConnector(Connector):
    name = "gnomad"
    dimensions = (Dimension.GENETIC_CONSTRAINT,)

    def fetch(self, req: Any) -> list[EvidenceRecord]:
        gene = (getattr(req, "gene", None) or str(req)).upper()
        constraint = lookup(gene)
        return summarize(gene, constraint)
