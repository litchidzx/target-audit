"""HPA（Human Protein Atlas）：正常组织暴露。

贡献 NORMAL_EXPRESSION 维度。这是 R1（治疗窗狭窄）的关键一侧。

端点为单基因 JSON（2026-10-15 实测通过）：
    https://www.proteinatlas.org/{ENSG}.json

不需要 Ensembl ID 也能查，用基因符号走 /api/search_download.php 亦可，
但实测该接口参数较严，单基因 JSON 更省事。这里优先用基因符号到
搜索结果页的重定向，失败时回退到直接用符号拼 URL。

关于取值的一个诚实说明：
  HPA 的"组织特异性"和"组织分布"是**分类变量**（Tissue enriched /
  Group enriched / Low tissue specificity；Detected in single / some /
  many / all），不是连续测量值。这里把它们映射到有序等级再合成一个
  "广泛表达程度"。这个映射是人为定义的序，必须在报告里标明，
  不能让读者以为它是测出来的分数。

方向：广泛表达对靶点不利（正常组织也会被影响），所以 high_is_favorable=False。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..normalize import make_record
from ..schema import Dimension, EvidenceRecord
from .base import Connector, ConnectorError, HttpClient

HPA_API = "https://www.proteinatlas.org"

# 有序等级：数字越大表示越广泛 / 越不特异
TISSUE_DISTRIBUTION_RANK = {
    "detected in single": 1,
    "detected in some": 2,
    "detected in many": 3,
    "detected in all": 4,
}
TISSUE_SPECIFICITY_RANK = {
    "tissue enriched": 1,
    "group enriched": 2,
    "tissue enhanced": 3,
    "low tissue specificity": 4,
}
CELL_TYPE_DISTRIBUTION_RANK = TISSUE_DISTRIBUTION_RANK
CELL_TYPE_SPECIFICITY_RANK = {
    "cell type enriched": 1,
    "group enriched": 2,
    "cell type enhanced": 3,
    "low cell type specificity": 4,
}


@dataclass
class NormalExpression:
    gene: str
    ensembl: str | None
    tissue_distribution: str
    tissue_specificity: str
    cell_type_distribution: str
    cell_type_specificity: str
    druggable_flag: bool
    url: str

    def breadth_score(self) -> float:
        """广泛表达程度，0-1。1 表示全身广泛表达、毫无选择性。"""
        parts = [
            TISSUE_DISTRIBUTION_RANK.get(self.tissue_distribution.strip().lower()),
            TISSUE_SPECIFICITY_RANK.get(self.tissue_specificity.strip().lower()),
            CELL_TYPE_DISTRIBUTION_RANK.get(self.cell_type_distribution.strip().lower()),
            CELL_TYPE_SPECIFICITY_RANK.get(self.cell_type_specificity.strip().lower()),
        ]
        known = [p for p in parts if p is not None]
        if not known:
            return 0.5
        # 等级 1-4 映射到 0-1
        return sum((p - 1) / 3 for p in known) / len(known)


def _fetch_gene_json(gene: str, client: HttpClient) -> dict[str, Any]:
    candidates = [f"{HPA_API}/api/search_download.php"]
    # 先用搜索接口拿 Ensembl ID，再取单基因 JSON
    payload = client.get_json(
        f"{HPA_API}/api/search_download.php",
        {"search": gene, "format": "json", "columns": "g,gs,eg", "compress": "no"},
    )
    if isinstance(payload, list) and payload:
        ensembl = str(payload[0].get("Ensembl") or "").strip()
        if ensembl:
            data = client.get_json(f"{HPA_API}/{ensembl}.json")
            if isinstance(data, dict) and data.get("Gene"):
                return data
    raise ConnectorError(f"HPA 未返回 {gene} 的记录")


def summarize(gene: str, data: dict[str, Any]) -> list[EvidenceRecord]:
    """纯函数：HPA JSON -> 一条 NORMAL_EXPRESSION 证据。"""
    ensembl = (data.get("Ensembl") or "").strip() or None
    url = f"{HPA_API}/{ensembl}.json" if ensembl else f"{HPA_API}/search/{gene}"

    protein_class = data.get("Protein class") or []
    if isinstance(protein_class, str):
        protein_class = [protein_class]
    druggable = any("approved drug" in str(c).lower() for c in protein_class)

    record = NormalExpression(
        gene=data.get("Gene") or gene,
        ensembl=ensembl,
        tissue_distribution=str(data.get("RNA tissue distribution") or "").strip(),
        tissue_specificity=str(data.get("RNA tissue specificity") or "").strip(),
        cell_type_distribution=str(data.get("RNA single cell type distribution") or "").strip(),
        cell_type_specificity=str(data.get("RNA single cell type specificity") or "").strip(),
        druggable_flag=druggable,
        url=url,
    )

    breadth = record.breadth_score()
    return [
        make_record(
            dimension=Dimension.NORMAL_EXPRESSION,
            source="HPA",
            metric="normal_tissue_breadth",
            statement=(
                f"正常组织分布：{record.tissue_distribution or '未知'}；"
                f"组织特异性：{record.tissue_specificity or '未知'}；"
                f"单细胞层面分布：{record.cell_type_distribution or '未知'}；"
                f"单细胞特异性：{record.cell_type_specificity or '未知'}。"
                f"广泛表达程度为 {breadth:.2f}（由分类变量映射到有序等级后合成，"
                f"不是连续测量值）。"
            ),
            percentile=breadth,
            value=round(breadth, 3),
            unit="ordinal_score",
            cohort="HPA normal tissue",
            url=url,
            confidence=0.6,
            raw={
                "tissue_distribution": record.tissue_distribution,
                "tissue_specificity": record.tissue_specificity,
                "cell_type_distribution": record.cell_type_distribution,
                "cell_type_specificity": record.cell_type_specificity,
                "druggable_flag": record.druggable_flag,
            },
        )
    ]


class HPAConnector(Connector):
    name = "hpa"
    dimensions = (Dimension.NORMAL_EXPRESSION,)

    def __init__(self, client: HttpClient | None = None):
        self.client = client or HttpClient()

    def fetch(self, req: Any) -> list[EvidenceRecord]:
        gene = (getattr(req, "gene", None) or str(req)).upper()
        data = _fetch_gene_json(gene, self.client)
        return summarize(gene, data)
