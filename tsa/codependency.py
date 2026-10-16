"""依赖共变表：哪些基因与查询基因同起同落，哪些与它互斥替补。

这是"这个靶点能不能被替代"所需要的证据。现有的跨癌种选择性只看一个方向的均值，
看不出结构：一个基因可能在本癌种里高度依赖，同时另有一个基因与它严格互斥 ——
那正是旁路或平行同源补偿在数据里的样子，也是抑制剂失效的常见原因。

**为什么不是嵌入向量**：先试过把每个基因的依赖曲线做 z-score 后截断 SVD，
取向量余弦找近邻。验证没通过 —— KRAS 的近邻是 RAB10、CTNNB1、HNF1A 这类
随谱系成分走的基因，CDKN2A 的近邻全是噪声（它本身没有依赖信号）。
直接检验已知关系才发现，真信号在原始相关系数里：SMARCA4 与 SMARCA2 是 −0.19
（已知的旁系同源互斥），CDK4 与 CCND1 是 +0.42（同一复合物），
而随机配对的相关中位数是 −0.007、90 分位只有 0.063。
降维把它冲淡了，所以这里直接存每个基因的强正相关与强负相关伙伴。

**为什么运行时不自己算**：18434 × 1361 的相关系数矩阵在纯 Python 里算不动，
而"别人电脑上不加依赖就能跑"是底线。相关性在开发端算一次，
运行时只是查表，代价是常数级的。
"""

from __future__ import annotations

import json
import lzma
import struct
from dataclasses import dataclass
from pathlib import Path

from .paths import asset_path, data_root

MAGIC = b"DMCD"  # DepMap Co-Dependency
HEADER_STRUCT = struct.Struct("<4sI")

# 相关强度量化：int8 存 r×100，覆盖 ±1.27，精度 0.01，足够区分强弱。
CORR_QUANT = 100.0
# 依赖曲线动态范围低于这个值的基因，其相关性由噪声主导，不给出伙伴列表。
# 取 0.25：CDKN2A 是 0.19（确实平坦），KRAS 是 0.62。
FLAT_THRESHOLD = 0.25


def default_path() -> Path:
    return asset_path("data", "depmap", "codependency.bin.lzma")


def user_path() -> Path:
    """使用者自己训练的那份优先于内置版本。"""

    return data_root() / "depmap" / "codependency.bin.lzma"


def resolve_path() -> Path:
    custom = user_path()
    return custom if custom.exists() else default_path()


@dataclass
class CoDependencyTable:
    genes: list[str]
    k: int
    payload: bytes
    meta: dict

    def _index(self) -> dict[str, int]:
        cached = self.meta.get("_index")
        if cached is None:
            cached = {g: i for i, g in enumerate(self.genes)}
            self.meta["_index"] = cached
        return cached

    def has(self, gene: str) -> bool:
        return gene.upper() in self._index()

    def dynamic_range(self, gene: str) -> float | None:
        """该基因依赖曲线的动态范围。接近 0 表示它到处都一样，没有可用结构。"""

        name = gene.upper()
        index = self._index().get(name)
        if index is not None and self.meta.get("range"):
            return self.meta["range"][index] / 100.0
        # 被平坦度筛掉的基因：名字在 flat 表里，动态范围也记着
        flat = self.meta.get("flat") or {}
        if name in flat:
            return flat[name] / 100.0
        return None

    def known_but_flat(self, gene: str) -> bool:
        """在 DepMap 矩阵里、但依赖曲线没有结构。"""

        return gene.upper() in (self.meta.get("flat") or {})

    def is_flat(self, gene: str) -> bool:
        if self.known_but_flat(gene):
            return True
        value = self.dynamic_range(gene)
        return value is not None and value < FLAT_THRESHOLD

    def partners(self, gene: str) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
        """返回（同向伙伴, 互补伙伴）。基因不在表里或曲线平坦时返回空列表。"""

        index = self._index().get(gene.upper())
        if index is None or self.is_flat(gene):
            return [], []

        k = self.k
        gene_count = len(self.genes)
        # 每条记录：k 个正相关索引(int32) + k 个正相关系数(int8)
        #           + k 个负相关索引(int32) + k 个负相关系数(int8)
        pos_idx_bytes = 4 * k
        pos_corr_bytes = k
        neg_idx_bytes = 4 * k
        record = pos_idx_bytes + pos_corr_bytes + neg_idx_bytes + k
        base = index * record

        def read(idx_offset: int, corr_offset: int, size: int) -> list[tuple[str, float]]:
            indices = struct.unpack_from(f"<{k}i", self.payload, base + idx_offset)
            corrs = struct.unpack_from(f"<{k}b", self.payload, base + corr_offset)
            out: list[tuple[str, float]] = []
            for i, c in zip(indices, corrs):
                if i < 0 or i >= gene_count:
                    continue
                out.append((self.genes[i], c / CORR_QUANT))
            return out

        positive = read(0, pos_idx_bytes, k)
        negative = read(pos_idx_bytes + pos_corr_bytes, pos_idx_bytes + pos_corr_bytes + neg_idx_bytes, k)
        return positive, negative


def save(
    path: Path,
    *,
    genes: list[str],
    k: int,
    ranges: list[float],
    payload: bytes,
    meta: dict,
) -> None:
    header = dict(meta)
    header.update(
        {
            "format": "depmap-codependency-int8",
            "genes": genes,
            "k": k,
            "range": [int(round(r * 100)) for r in ranges],
            "flat_threshold": FLAT_THRESHOLD,
        }
    )
    header_bytes = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    blob = HEADER_STRUCT.pack(MAGIC, len(header_bytes)) + header_bytes + payload
    path.parent.mkdir(parents=True, exist_ok=True)
    with lzma.open(path, "wb", preset=6) as fh:
        fh.write(blob)


def load(path: Path | None = None) -> CoDependencyTable | None:
    """读不到返回 None —— 这是可选能力，不该让审计失败。"""

    target = path or resolve_path()
    if not target.exists():
        return None
    with lzma.open(target, "rb") as fh:
        magic, header_len = HEADER_STRUCT.unpack(fh.read(HEADER_STRUCT.size))
        if magic != MAGIC:
            raise ValueError(f"{target} 不是依赖共变表")
        header = json.loads(fh.read(header_len).decode("utf-8"))
        payload = fh.read()
    return CoDependencyTable(
        genes=list(header["genes"]),
        k=int(header["k"]),
        payload=payload,
        meta=header,
    )


_CACHE: dict[str, CoDependencyTable | None] = {}


def table(path: Path | None = None) -> CoDependencyTable | None:
    """按路径缓存，避免同一次审计里反复解压。"""

    key = str(path or resolve_path())
    if key not in _CACHE:
        try:
            _CACHE[key] = load(path)
        except (OSError, ValueError, KeyError):
            _CACHE[key] = None
    return _CACHE[key]


@dataclass
class CoDependencyBrief:
    """审计报告里要用的一小段结论，避免报告层再依赖表格结构。"""

    gene: str
    positive: list[tuple[str, float]]
    negative: list[tuple[str, float]]
    dynamic_range: float | None
    is_flat: bool
    available: bool
    note: str = ""


def summarize(gene: str, *, top: int = 12, path: Path | None = None) -> CoDependencyBrief:
    """给一个基因的共变概览。表不存在或基因不在表里都返回 available=False。"""

    loaded = table(path)
    if loaded is None:
        return CoDependencyBrief(gene, [], [], None, False, False,
                                 note="依赖共变表未就绪（可用 scripts/train_codependency.py 生成）")
    if not loaded.has(gene) and not loaded.known_but_flat(gene):
        return CoDependencyBrief(gene, [], [], None, False, False,
                                 note="该基因不在 DepMap 依赖矩阵内（可能不是蛋白编码基因，或名称不同）")
    dynamic = loaded.dynamic_range(gene)
    if loaded.is_flat(gene):
        return CoDependencyBrief(
            gene, [], [], dynamic, True, True,
            note=("该基因在 DepMap 里，但它的依赖性在各细胞系间几乎没有差异"
                  + (f"（动态范围 {dynamic:.2f}）" if dynamic is not None else "")
                  + "，相关伙伴会由噪声主导，因此不给出列表"),
        )
    positive, negative = loaded.partners(gene)
    return CoDependencyBrief(
        gene,
        positive[:top],
        negative[:top],
        dynamic,
        False,
        True,
        note=(
            "相关系数只表示两条依赖曲线同起同落，不代表通路归属或因果。"
            "作为参照：随机基因对的相关中位数是 −0.007、90 分位 0.063；"
            "我们检验过的已知关系分别落在 SMARCA4/SMARCA2 互补 −0.19、"
            "CDK4/CCND1 同向 +0.42、KRAS/RAF1 同向 +0.34。"
            "因此 r 低于约 0.2 的条目多半反映谱系成分，值得细看的是一致且强度较高的那几个。"
        ),
    )
