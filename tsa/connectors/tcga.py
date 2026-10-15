"""TCGA / GDC：临床相关性。

贡献 CLINICAL 维度。这个维度要回答的不是"突变频率高不高"，而是
"这个靶点在真实病人里的改变图谱是什么样"。

端点已于 2026-10-09 对着 GDC Data Release 46.0 实测通过：
  - GET /projects/{project}?expand=summary      -> data.summary.case_count
  - GET /ssm_occurrences                        -> 小突变（SNV/indel）
  - GET /cnv_occurrences                        -> 拷贝数改变（GISTIC 5 分类）
  - GET /analysis/top_mutated_genes_by_project  -> 只有基因注释、没有计数，不能当参考分布用

实测过程中最重要的一个发现（这个发现本身就是工具价值的最好说明）：

    只用 ssm_occurrences 算"突变频率"，会把肿瘤抑制基因严重低估。
    在 TCGA-PAAD 里 CDKN2A 的突变频率只有 16%，但同队列中它的纯合缺失
    还有相当比例 —— 因为肿瘤抑制基因主要靠缺失失活，不靠点突变。
    如果工具只报一个"突变频率"，使用者会得出"CDKN2A 在胰腺癌里不怎么重要"
    这个完全错误的结论。

所以这里把三种改变分开统计再取并集，并且单独判断"改变方式是不是以缺失为主"。
后者的输出不是分数，而是一条独立的证据 —— 它会触发 R7 冲突规则。

关于分位参考：变异频率的分位必须在同一队列内部比较才有意义，所以这里用一个
癌基因面板实时算出该队列的参考分布，而不是写死一组数字。面板结果走 HTTP 缓存，
只在第一次运行时真正发起请求。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..normalize import make_record, percentile_rank
from ..schema import ConflictCause, Dimension, EvidenceRecord, Polarity
from .base import Connector, ConnectorError, HttpClient

GDC_API = "https://api.gdc.cancer.gov"

# 常用癌种的快捷映射。系统本身不限于这些 —— resolve_project() 接受任何
# TCGA 项目编号，所以新增一个癌种不需要改代码。
PROJECT_FOR_CANCER = {
    "PAAD": "TCGA-PAAD",   # 胰腺癌
    "LUAD": "TCGA-LUAD",   # 肺腺癌
    "LUSC": "TCGA-LUSC",   # 肺鳞癌
    "BRCA": "TCGA-BRCA",   # 乳腺癌
    "COAD": "TCGA-COAD",   # 结肠癌
    "READ": "TCGA-READ",   # 直肠癌
    "STAD": "TCGA-STAD",   # 胃癌
    "LIHC": "TCGA-LIHC",   # 肝癌
    "KIRC": "TCGA-KIRC",   # 肾透明细胞癌
    "OV": "TCGA-OV",       # 卵巢癌
    "UCEC": "TCGA-UCEC",   # 子宫内膜癌
    "HNSC": "TCGA-HNSC",   # 头颈鳞癌
    "BLCA": "TCGA-BLCA",   # 膀胱癌
    "SKCM": "TCGA-SKCM",   # 黑色素瘤
    "GBM": "TCGA-GBM",     # 胶质母细胞瘤
    "PRAD": "TCGA-PRAD",   # 前列腺癌
}


def resolve_project(cancer: str) -> str:
    """把癌种标识解析为 GDC 项目编号。

    接受两种写法：常用的癌种缩写（PAAD），或直接给 TCGA 项目编号（TCGA-PAAD）。
    这样扩展到任何 TCGA 收录的癌种都不需要改代码。
    """
    key = (cancer or "").strip().upper()
    if key.startswith("TCGA-"):
        return key
    project = PROJECT_FOR_CANCER.get(key)
    if project is None:
        raise ConnectorError(
            f"未知癌种 {cancer}。可传入 TCGA 项目编号（如 TCGA-LUAD），"
            f"或使用已收录缩写：{', '.join(sorted(PROJECT_FOR_CANCER))}"
        )
    return project

# 字段名需要完整前缀。实测确认：写 consequence.transcript.gene.symbol 会返回 0 条，
# 必须写 ssm.consequence.transcript.gene.symbol。
FIELD_PROJECT = "case.project.project_id"
FIELD_SSM_GENE = "ssm.consequence.transcript.gene.symbol"
FIELD_CNV_GENE = "cnv.consequence.gene.symbol"
FIELD_CNV_CATEGORY = "cnv.cnv_change_5_category"

# GISTIC 五分类里的高置信度改变
DEEP_DELETION = "Homozygous Deletion"
AMPLIFICATION = "Amplification"

# 用于计算队列内分位参考的癌基因面板。只是"背景分布"，不是候选靶点列表。
REFERENCE_GENE_PANEL: tuple[str, ...] = (
    "TP53", "KRAS", "SMAD4", "CDKN2A", "ARID1A", "KDM6A", "RNF43", "TGFBR2",
    "KMT2C", "GNAS", "BRAF", "PIK3CA", "PTEN", "RB1", "APC", "ATM",
    "BRCA1", "BRCA2", "PALB2", "EGFR", "ERBB2", "MET", "MYC", "CCND1",
)

MAX_PAGE = 2000


@dataclass
class ClinicalSummary:
    """一个基因在一个队列里的改变图谱。case 集合保留下来是为了取并集。"""

    gene: str
    project: str
    n_cases: int
    mutated_cases: frozenset[str]
    deep_deleted_cases: frozenset[str]
    amplified_cases: frozenset[str]

    @property
    def mutated_fraction(self) -> float:
        return len(self.mutated_cases) / self.n_cases if self.n_cases else 0.0

    @property
    def deep_deleted_fraction(self) -> float:
        return len(self.deep_deleted_cases) / self.n_cases if self.n_cases else 0.0

    @property
    def amplified_fraction(self) -> float:
        return len(self.amplified_cases) / self.n_cases if self.n_cases else 0.0

    @property
    def altered_cases(self) -> frozenset[str]:
        """任一改变类型的并集。这才是"改变频率"，不等于"突变频率"。"""
        return self.mutated_cases | self.deep_deleted_cases | self.amplified_cases

    @property
    def altered_fraction(self) -> float:
        return len(self.altered_cases) / self.n_cases if self.n_cases else 0.0

    @property
    def deletion_only_cases(self) -> frozenset[str]:
        """只靠缺失改变、没有点突变的病例 —— 纯缺失贡献。"""
        return self.deep_deleted_cases - self.mutated_cases

    @property
    def amplification_only_cases(self) -> frozenset[str]:
        return self.amplified_cases - self.mutated_cases

    @property
    def exclusive_counts(self) -> dict[str, int]:
        """三种机制各自的排他贡献病例数。

        必须用排他集合来比较，否则会出现一个错误结论：只要一个队列里没有缺失，
        任何一例扩增都会被判成"扩增主导"，即使突变病例远多于扩增。
        """
        return {
            "mutation_dominant": len(
                self.mutated_cases - self.deep_deleted_cases - self.amplified_cases
            ),
            "loss_dominant": len(
                self.deep_deleted_cases - self.mutated_cases - self.amplified_cases
            ),
            "amplification_dominant": len(
                self.amplified_cases - self.mutated_cases - self.deep_deleted_cases
            ),
        }

    @property
    def is_loss_dominant(self) -> bool:
        """改变方式是不是以缺失为主。

        判据：排他贡献以缺失最多，且纯合缺失占比不低于 5%。
        5% 这个下限是为了避免在小队列上把噪声判成机制。
        """
        return (
            self.alteration_mode == "loss_dominant"
            and self.deep_deleted_fraction >= 0.05
        )

    @property
    def alteration_mode(self) -> str:
        """改变方式的归类。

        同一个基因在不同癌种里可能走完全不同的机制。例如 KRAS 在胰腺癌里
        以点突变为主，在乳腺癌里则以扩增为主 —— 这两种机制对"能不能成药"
        的含义不一样：热点突变对应明确的活化机制，而野生型基因的扩增
        只是剂量效应，作为靶点的理由要弱得多。
        """
        counts = self.exclusive_counts
        # 并列时优先判为突变主导，这是三者中信号最明确的一类。
        order = ("mutation_dominant", "loss_dominant", "amplification_dominant")
        return max(order, key=lambda k: (counts[k], -order.index(k)))

    def loss_dominance_strength(self) -> float:
        """缺失主导的程度，0-1。纯缺失占比达到 15% 即算满强度。"""
        return min(
            1.0, self.exclusive_counts["loss_dominant"] / max(1, self.n_cases) / 0.15
        )


def _query_cases(
    endpoint: str,
    filters: dict[str, Any],
    fields: str,
    client: HttpClient,
) -> list[dict[str, Any]]:
    """分页取全所有命中，返回原始 hit 列表。"""
    hits: list[dict[str, Any]] = []
    offset = 0
    while True:
        payload = client.get_json(
            f"{GDC_API}/{endpoint}",
            {
                "filters": json.dumps(filters),
                "fields": fields,
                "size": MAX_PAGE,
                "from": offset,
                "format": "JSON",
            },
        )
        page = ((payload.get("data") or {}).get("hits")) or []
        hits.extend(page)
        total = ((payload.get("data") or {}).get("pagination") or {}).get("total", 0)
        offset += len(page)
        if not page or offset >= total:
            break
    return hits


def _project_filter(project: str) -> dict[str, Any]:
    return {"op": "in", "content": {"field": FIELD_PROJECT, "value": [project]}}


def fetch_project_case_count(project: str, client: HttpClient | None = None) -> int:
    """取队列病例数。

    必须带 expand=summary —— 不带的话 data.case_count 是 None（实测确认）。
    """
    client = client or HttpClient()
    payload = client.get_json(f"{GDC_API}/projects/{project}", {"expand": "summary", "format": "JSON"})
    count = ((payload.get("data") or {}).get("summary") or {}).get("case_count")
    if count is None:
        raise ConnectorError(f"GDC 未返回 {project} 的 case_count")
    return int(count)


def fetch_mutation_cases(gene: str, project: str, client: HttpClient) -> frozenset[str]:
    filters = {
        "op": "and",
        "content": [
            _project_filter(project),
            {"op": "in", "content": {"field": FIELD_SSM_GENE, "value": [gene.upper()]}},
        ],
    }
    hits = _query_cases("ssm_occurrences", filters, "case.case_id", client)
    return frozenset(
        h["case"]["case_id"] for h in hits if isinstance(h.get("case"), dict) and h["case"].get("case_id")
    )


def fetch_cnv_cases(
    gene: str, project: str, category: str, client: HttpClient
) -> frozenset[str]:
    filters = {
        "op": "and",
        "content": [
            _project_filter(project),
            {"op": "in", "content": {"field": FIELD_CNV_GENE, "value": [gene.upper()]}},
            {"op": "in", "content": {"field": FIELD_CNV_CATEGORY, "value": [category]}},
        ],
    }
    hits = _query_cases("cnv_occurrences", filters, "case.case_id", client)
    return frozenset(
        h["case"]["case_id"] for h in hits if isinstance(h.get("case"), dict) and h["case"].get("case_id")
    )


def fetch_clinical_summary(
    gene: str, project: str, client: HttpClient | None = None
) -> ClinicalSummary:
    client = client or HttpClient()
    gene = gene.upper()
    n_cases = fetch_project_case_count(project, client)
    return ClinicalSummary(
        gene=gene,
        project=project,
        n_cases=n_cases,
        mutated_cases=fetch_mutation_cases(gene, project, client),
        deep_deleted_cases=fetch_cnv_cases(gene, project, DEEP_DELETION, client),
        amplified_cases=fetch_cnv_cases(gene, project, AMPLIFICATION, client),
    )


def build_reference_distribution(
    project: str,
    client: HttpClient | None = None,
    panel: tuple[str, ...] = REFERENCE_GENE_PANEL,
    exclude: str | None = None,
) -> list[float]:
    """用癌基因面板实时算出该队列的"改变频率"参考分布。

    结果走 HTTP 缓存，所以只有第一次运行会真的发几十个请求。
    """
    client = client or HttpClient()
    values: list[float] = []
    for g in panel:
        if exclude and g == exclude.upper():
            continue
        try:
            summary = fetch_clinical_summary(g, project, client)
        except ConnectorError:
            continue
        values.append(summary.altered_fraction)
    if len(values) < 5:
        raise ConnectorError(f"{project} 的参考面板只取到 {len(values)} 个基因，样本太少，分位不可信")
    return values


def summarize(
    summary: ClinicalSummary, reference: list[float] | None = None
) -> list[EvidenceRecord]:
    """把改变图谱转成两条证据：改变频率，以及改变方式。"""
    reference = reference or []
    frac = summary.altered_fraction
    pct = percentile_rank(frac, reference) if len(reference) >= 5 else None

    url = f"https://portal.gdc.cancer.gov/projects/{summary.project}"
    records = [
        make_record(
            dimension=Dimension.CLINICAL,
            source="GDC / TCGA",
            metric="altered_case_fraction",
            statement=(
                f"{summary.project} 的 {summary.n_cases} 个病例中，"
                f"{len(summary.altered_cases)} 例存在改变（{frac:.1%}）；"
                f"其中点突变 {len(summary.mutated_cases)} 例（{summary.mutated_fraction:.1%}）、"
                f"纯合缺失 {len(summary.deep_deleted_cases)} 例（{summary.deep_deleted_fraction:.1%}）、"
                f"扩增 {len(summary.amplified_cases)} 例（{summary.amplified_fraction:.1%}）。"
                f"改变频率是三类取并集后的结果，不等于突变频率。"
                f"注意这里只统计结构性改变，未包含启动子甲基化沉默和杂合性缺失，"
                f"所以对于以表观沉默为主失活的基因，真实失活率会高于此值。"
            ),
            percentile=pct,
            value=round(frac, 4),
            unit="fraction",
            cohort=summary.project,
            url=url,
            confidence=0.75,
            raw={
                "n_cases": summary.n_cases,
                "n_mutated": len(summary.mutated_cases),
                "n_deep_deleted": len(summary.deep_deleted_cases),
                "n_amplified": len(summary.amplified_cases),
                "n_altered": len(summary.altered_cases),
                "reference_panel_size": len(reference),
            },
        )
    ]

    # 第二条证据的方向不经过百分位映射，直接由机制分析给出 ——
    # "缺失主导"和"数值高低"不是同一个概念，硬塞进百分位会让语义失真。
    if summary.is_loss_dominant:
        records.append(
            EvidenceRecord(
                dimension=Dimension.CLINICAL,
                source="GDC / TCGA",
                metric="alteration_mechanism",
                statement=(
                    f"{summary.gene} 的改变以缺失失活为主："
                    f"{summary.exclusive_counts['loss_dominant']} 例仅由纯合缺失驱动，"
                    f"而仅由扩增驱动的为 {summary.exclusive_counts['amplification_dominant']} 例，"
                    f"仅由点突变驱动的为 {summary.exclusive_counts['mutation_dominant']} 例。"
                    f"这是抑癌基因的典型模式，没有可直接抑制的靶点产物。"
                ),
                polarity=Polarity.UNFAVORABLE,
                strength=round(summary.loss_dominance_strength(), 4),
                confidence=0.7,
                value=len(summary.deletion_only_cases),
                unit="cases",
                cohort=summary.project,
                url=url,
                raw={
                    "exclusive_counts": summary.exclusive_counts,
                    "deep_deleted_fraction": round(summary.deep_deleted_fraction, 4),
                },
            )
        )
    return records


class TCGAConnector(Connector):
    name = "tcga"
    dimensions = (Dimension.CLINICAL,)

    def __init__(self, client: HttpClient | None = None):
        self.client = client or HttpClient()

    def fetch(self, req: Any) -> list[EvidenceRecord]:
        gene = (getattr(req, "gene", None) or str(req)).upper()
        cancer = getattr(req, "cancer", None)
        if not cancer:
            raise ConnectorError("请求未指定癌种；癌种是必填参数，系统不设默认值")
        project = resolve_project(cancer)
        summary = fetch_clinical_summary(gene, project, self.client)
        reference = build_reference_distribution(project, self.client, exclude=gene)
        return summarize(summary, reference)
