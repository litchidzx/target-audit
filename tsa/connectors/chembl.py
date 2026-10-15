"""ChEMBL：成药性。

贡献 DRUGGABILITY 维度，并为 ON_TARGET_TOXICITY 提供"同靶点已上市药物"的入口。

端点（2026-10-15 实测通过）：
    /chembl/api/data/target/search?q={gene}          找到靶点条目
    /chembl/api/data/mechanism?target_chembl_id=...  该靶点的作用机制与相关药物

关于取值的一个诚实说明：
  "有没有化学物质"本身不是连续量。这里按药物的最高研发阶段定义一个有序等级
  （已上市 > 临床期 > 仅临床前 > 有靶点无化学物质 > 无靶点记录），
  再把这个等级当作分位使用。这是人为定义的序，报告里必须标明。

方向：越有成药路径对靶点越有利，所以 high_is_favorable=True。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..normalize import make_record
from ..schema import Dimension, EvidenceRecord
from .base import Connector, ConnectorError, HttpClient

CHEMBL_API = "https://www.ebi.ac.uk/chembl/api/data"
UNIPROT_API = "https://rest.uniprot.org/uniprotkb/search"

# 有序等级：数字越大表示成药路径越明确
STAGE_RANK = {
    "no_target_record": 0.05,
    "target_only": 0.30,
    "preclinical": 0.55,
    "clinical": 0.78,
    "approved": 0.95,
}

STAGE_LABEL = {
    "no_target_record": "ChEMBL 中没有该基因的靶点记录",
    "target_only": "有靶点记录，但没有已知活性化学物质",
    "preclinical": "只有临床前阶段的活性物质",
    "clinical": "有进入临床阶段的药物",
    "approved": "已有获批上市的药物",
}


@dataclass
class DruggabilityProfile:
    gene: str
    stage: str
    target_chembl_id: str | None = None
    approved_drugs: list[str] = field(default_factory=list)
    clinical_drugs: list[str] = field(default_factory=list)
    approved_drug_ids: list[str] = field(default_factory=list)
    clinical_drug_ids: list[str] = field(default_factory=list)
    action_types: list[str] = field(default_factory=list)
    mechanism_count: int = 0
    url: str = ""

    @property
    def known_drugs(self) -> list[str]:
        """可用于查不良反应的**药物名称**（不是 ChEMBL 编号），已上市的优先。"""
        return list(dict.fromkeys(self.approved_drugs + self.clinical_drugs))


def _drug_names(ids: list[str], client: HttpClient) -> list[str]:
    """把 ChEMBL 分子编号解析成药物名称。

    mechanism 接口只给编号（形如 CHEMBL4535757），而 FAERS 查询需要的是
    药名（SOTORASIB）或商品名，所以必须再查一次分子接口。
    同时取同义词：FAERS 的上报里商品名和通用名混用，多带一个能提高命中率。
    """
    names: list[str] = []
    for chembl_id in ids:
        try:
            payload = client.get_json(f"{CHEMBL_API}/molecule/{chembl_id}", {"format": "json"})
        except ConnectorError:
            continue
        pref = str(payload.get("pref_name") or "").strip().upper()
        if not pref:
            continue
        names.append(pref)
        for syn in (payload.get("molecule_synonyms") or [])[:1]:
            synonym = str(syn.get("molecule_synonym") or "").strip().upper()
            if synonym and synonym != pref:
                names.append(synonym)
    return list(dict.fromkeys(names))


def resolve_uniprot(gene: str, client: HttpClient) -> str | None:
    """基因符号 -> 人源 UniProt 主登录号。"""
    payload = client.get_json(
        UNIPROT_API,
        {
            "query": f"gene_exact:{gene} AND organism_id:9606 AND reviewed:true",
            "fields": "accession",
            "format": "json",
            "size": 1,
        },
    )
    results = payload.get("results") or []
    return results[0].get("primaryAccession") if results else None


def _mechanism_count(target_id: str, client: HttpClient) -> int:
    try:
        payload = client.get_json(
            f"{CHEMBL_API}/mechanism",
            {"target_chembl_id": target_id, "format": "json", "limit": 1},
        )
    except ConnectorError:
        return 0
    return int((payload.get("page_meta") or {}).get("total_count", 0) or 0)


def find_target(gene: str, client: HttpClient) -> str | None:
    """找到该基因对应的 ChEMBL 靶点条目。

    不能只用名称搜索。实测中 `q=EGFR` 返回的前几条是蛋白互作靶点和**小鼠**
    EGFR（UniProt Q01279），真正的人源单蛋白条目 CHEMBL203 根本不在前面 ——
    只按名称匹配会把有上市药物的靶点判成"无成药路径"。

    正确做法是先解析人源 UniProt 登录号，再按 accession 精确查靶点；
    若有多个条目，取机制记录最多的那个。
    """
    accession = resolve_uniprot(gene, client)
    if accession:
        payload = client.get_json(
            f"{CHEMBL_API}/target",
            {"target_components__accession": accession, "format": "json", "limit": 20},
        )
        targets = [
            t
            for t in (payload.get("targets") or [])
            if t.get("organism") == "Homo sapiens"
        ]
        singles = [t for t in targets if t.get("target_type") == "SINGLE PROTEIN"]
        candidates = singles or targets
        if candidates:
            if len(candidates) == 1:
                return candidates[0].get("target_chembl_id")
            # 多个候选时取机制记录最多的，它才是治疗上真正被使用的条目
            scored = [
                (_mechanism_count(t["target_chembl_id"], client), t["target_chembl_id"])
                for t in candidates[:3]
            ]
            scored.sort(reverse=True)
            return scored[0][1]

    # 回退：按名称搜索，仍要求人源
    payload = client.get_json(
        f"{CHEMBL_API}/target/search", {"q": gene, "format": "json", "limit": 10}
    )
    for item in payload.get("targets") or []:
        if item.get("organism") == "Homo sapiens" and item.get("target_type") == "SINGLE PROTEIN":
            return item.get("target_chembl_id")
    return None


def fetch_profile(gene: str, client: HttpClient) -> DruggabilityProfile:
    target_id = find_target(gene, client)
    if not target_id:
        return DruggabilityProfile(
            gene=gene,
            stage="no_target_record",
            url=f"https://www.ebi.ac.uk/chembl/g/#search_results/all/query={gene}",
        )

    payload = client.get_json(
        f"{CHEMBL_API}/mechanism",
        {"target_chembl_id": target_id, "format": "json", "limit": 100},
    )
    mechanisms = payload.get("mechanisms") or []

    approved: list[str] = []
    clinical: list[str] = []
    actions: set[str] = set()

    for mech in mechanisms:
        drug_id = str(mech.get("molecule_chembl_id") or "").strip()
        if not drug_id:
            continue
        try:
            phase = int(mech.get("max_phase") or 0)
        except (TypeError, ValueError):
            phase = 0
        if mech.get("action_type"):
            actions.add(str(mech["action_type"]))
        if phase >= 4:
            approved.append(drug_id)
        elif phase >= 1:
            clinical.append(drug_id)

    approved_names = _drug_names(sorted(set(approved)), client)
    clinical_names = _drug_names(sorted(set(clinical)), client)

    if approved:
        stage = "approved"
    elif clinical:
        stage = "clinical"
    elif mechanisms:
        stage = "preclinical"
    else:
        stage = "target_only"

    return DruggabilityProfile(
        gene=gene,
        stage=stage,
        target_chembl_id=target_id,
        approved_drugs=approved_names,
        clinical_drugs=clinical_names,
        approved_drug_ids=sorted(set(approved)),
        clinical_drug_ids=sorted(set(clinical)),
        action_types=sorted(actions),
        mechanism_count=len(mechanisms),
        url=f"https://www.ebi.ac.uk/chembl/target_report_card/{target_id}/",
    )


def summarize(profile: DruggabilityProfile) -> list[EvidenceRecord]:
    percentile = STAGE_RANK[profile.stage]
    counts = []
    if profile.approved_drugs:
        counts.append(f"已上市药物 {len(profile.approved_drug_ids)} 个")
    if profile.clinical_drugs:
        counts.append(f"临床阶段药物 {len(profile.clinical_drug_ids)} 个")
    if profile.mechanism_count:
        counts.append(f"机制记录 {profile.mechanism_count} 条")
    detail = "，".join(counts) if counts else "无"
    named = "、".join(
        profile.approved_drugs or profile.clinical_drugs or []
    )[:80]

    return [
        make_record(
            dimension=Dimension.DRUGGABILITY,
            source="ChEMBL",
            metric="development_stage",
            statement=(
                f"成药路径：{STAGE_LABEL[profile.stage]}（{detail}）。"
                f"作用类型：{'、'.join(profile.action_types) or '未知'}。"
                + (f"相关药物：{named}。" if named else "")
                +
                f"该判定由研发阶段映射到有序等级得到，不是连续测量值。"
            ),
            percentile=percentile,
            value=percentile,
            unit="ordinal_score",
            cohort="ChEMBL",
            url=profile.url,
            confidence=0.7,
            raw={
                "stage": profile.stage,
                "target_chembl_id": profile.target_chembl_id,
                "approved_drugs": profile.approved_drugs,
                "clinical_drugs": profile.clinical_drugs,
                "approved_drug_ids": profile.approved_drug_ids,
                "clinical_drug_ids": profile.clinical_drug_ids,
                "action_types": profile.action_types,
                "mechanism_count": profile.mechanism_count,
            },
        )
    ]


class ChEMBLConnector(Connector):
    name = "chembl"
    dimensions = (Dimension.DRUGGABILITY,)

    def __init__(self, client: HttpClient | None = None):
        self.client = client or HttpClient()

    def fetch(self, req: Any) -> list[EvidenceRecord]:
        gene = (getattr(req, "gene", None) or str(req)).upper()
        return summarize(fetch_profile(gene, self.client))
