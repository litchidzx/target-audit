"""cBioPortal：变异谱。

回答的问题：这个基因在这个癌种里，实际发生的是哪些变异，各占多少。

为什么用它而不是 GDC：**蛋白编号的基准不同。**

GDC 的变异注释按转录本给出，而同一个变异会命中十几个转录本，每个编号都不一样
（实测 BRAF 的一个变异被标成 A444Cfs*9 / A404Cfs*9 / A85Cfs*9 …）。
它标记的"规范转录本"是 ENSEMBL 的，与社区通用写法不一致 ——
BRAF 最著名的 V600E 在 GDC 规范转录本下会写成 V640E。

编号一旦不一致，变异名就对不上药物说明书和文献，整条链断掉。
cBioPortal 自己做了标准化注释，实测输出的是 V600E、G12D 这类通用写法。

接口（2026-10-16 实测）：
    GET  /api/studies                                       研究列表
    GET  /api/studies/{sid}/molecular-profiles              找突变谱
    GET  /api/studies/{sid}/sample-lists                    找样本列表
    GET  /api/genes/{symbol}                                取 entrez id
    POST /api/molecular-profiles/{pid}/mutations/fetch      取突变
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .base import Connector, ConnectorError, HttpClient

CBIO_API = "https://www.cbioportal.org/api"

# 癌种代码 -> 首选研究。PanCancer Atlas 版本注释统一，优先用它。
# 找不到时会在同名研究里回退，所以这张表不必求全。
PREFERRED_STUDY = {
    "PAAD": "paad_tcga_pan_can_atlas_2018",
    "LUAD": "luad_tcga_pan_can_atlas_2018",
    "LUSC": "lusc_tcga_pan_can_atlas_2018",
    "COAD": "coadread_tcga_pan_can_atlas_2018",
    "READ": "coadread_tcga_pan_can_atlas_2018",
    "STAD": "stad_tcga_pan_can_atlas_2018",
    "BRCA": "brca_tcga_pan_can_atlas_2018",
    "LIHC": "lihc_tcga_pan_can_atlas_2018",
    "OV": "ov_tcga_pan_can_atlas_2018",
    "KIRC": "kirc_tcga_pan_can_atlas_2018",
    "HNSC": "hnsc_tcga_pan_can_atlas_2018",
    "BLCA": "blca_tcga_pan_can_atlas_2018",
    "SKCM": "skcm_tcga_pan_can_atlas_2018",
    "UCEC": "ucec_tcga_pan_can_atlas_2018",
    "GBM": "gbm_tcga_pan_can_atlas_2018",
    "PRAD": "prad_tcga_pan_can_atlas_2018",
}


@dataclass
class VariantProfile:
    """一个基因在一个队列里的变异构成。"""

    gene: str
    cancer: str
    study_id: str
    total_samples: int
    variants: list[tuple[str, int]] = field(default_factory=list)

    @property
    def altered_samples(self) -> int:
        return sum(n for _, n in self.variants)

    @property
    def dominant(self) -> tuple[str, int] | None:
        return self.variants[0] if self.variants else None

    def fraction(self, variant: str) -> float:
        """某个变异占该基因全部改变的比例。"""
        for name, count in self.variants:
            if name.upper() == variant.upper():
                total = self.altered_samples
                return count / total if total else 0.0
        return 0.0

    def count_of(self, variant: str) -> int:
        for name, count in self.variants:
            if name.upper() == variant.upper():
                return count
        return 0


class CBioPortalConnector(Connector):
    """变异谱不是"维度"证据，而是亚型分析的输入，所以不产出 EvidenceRecord。

    这里保留 Connector 的接口形状只是为了复用 HttpClient 与缓存，
    fetch() 返回空列表，实际入口是 fetch_profile()。
    """

    name = "cbioportal"
    dimensions: tuple = ()

    def __init__(self, client: HttpClient | None = None):
        self.client = client or HttpClient()

    def fetch(self, req: Any) -> list:
        return []

    # ---- 内部：找出研究所需的几个标识 ----

    def resolve_study(self, cancer: str) -> str:
        """癌种代码 -> cBioPortal 研究 ID。"""
        cancer = (cancer or "").upper()
        preferred = PREFERRED_STUDY.get(cancer)
        studies = self.client.get_json(f"{CBIO_API}/studies")
        ids = [s["studyId"] for s in studies]
        if preferred and preferred in ids:
            return preferred

        # 回退：找该癌种下任何 TCGA 研究
        prefix = cancer.lower()
        candidates = [i for i in ids if i.startswith(f"{prefix}_tcga")]
        if not candidates:
            # 直肠与结肠共用研究
            if prefix in ("read", "coad"):
                candidates = [i for i in ids if i.startswith("coadread_tcga")]
        if not candidates:
            raise ConnectorError(f"cBioPortal 里找不到癌种 {cancer} 对应的研究")
        # 优先带 pan_can_atlas 的版本
        atlas = [i for i in candidates if "pan_can_atlas" in i]
        return (atlas or candidates)[0]

    def mutation_profile_id(self, study_id: str) -> str:
        profiles = self.client.get_json(f"{CBIO_API}/studies/{study_id}/molecular-profiles")
        for p in profiles:
            if p.get("molecularAlterationType") == "MUTATION_EXTENDED":
                return p["molecularProfileId"]
        raise ConnectorError(f"研究 {study_id} 没有突变谱")

    def sample_list_id(self, study_id: str) -> str:
        lists = self.client.get_json(f"{CBIO_API}/studies/{study_id}/sample-lists")
        all_lists = [s["sampleListId"] for s in lists if s["sampleListId"].endswith("_all")]
        if all_lists:
            return all_lists[0]
        if lists:
            return lists[0]["sampleListId"]
        raise ConnectorError(f"研究 {study_id} 没有样本列表")

    def entrez_id(self, gene: str) -> int:
        payload = self.client.get_json(f"{CBIO_API}/genes/{gene.upper()}")
        entrez = payload.get("entrezGeneId")
        if entrez is None:
            raise ConnectorError(f"cBioPortal 里找不到基因 {gene}")
        return int(entrez)

    # ---- 对外入口 ----

    def fetch_profile(self, gene: str, cancer: str) -> VariantProfile:
        gene = gene.upper()
        study_id = self.resolve_study(cancer)
        profile_id = self.mutation_profile_id(study_id)
        samples = self.sample_list_id(study_id)
        entrez = self.entrez_id(gene)

        mutations = self.client.post_json(
            f"{CBIO_API}/molecular-profiles/{profile_id}/mutations/fetch",
            {"entrezGeneIds": [entrez], "sampleListId": samples},
        )

        # 按病例去重：同一个病例可能有多个突变记录（不同转录本或多次采样）
        per_variant: dict[str, set[str]] = {}
        for m in mutations:
            change = (m.get("proteinChange") or "").strip()
            sample = m.get("sampleId") or m.get("patientId")
            if not change or not sample:
                continue
            # 跳过同义突变等不改变蛋白的记录
            if str(m.get("mutationType") or "").lower().startswith("silent"):
                continue
            per_variant.setdefault(change.upper(), set()).add(sample)

        variants = sorted(
            ((name, len(cases)) for name, cases in per_variant.items()),
            key=lambda x: (-x[1], x[0]),
        )
        return VariantProfile(
            gene=gene,
            cancer=cancer.upper(),
            study_id=study_id,
            total_samples=0,  # cBioPortal 未在本次请求中返回总数，占比按改变病例算
            variants=variants,
        )
