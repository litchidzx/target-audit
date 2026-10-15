"""openFDA FAERS：经验性在靶毒性。

贡献 ON_TARGET_TOXICITY 维度。逻辑是：先找到同靶点的已上市或临床阶段药物，
再拉这些药物在真实世界自发上报中的不良反应构成。这是"这个靶点抑制起来
会不会有代价"最直接的经验性证据。

端点（2026-10-15 实测通过）：
    https://api.fda.gov/drug/event.json?search=patient.drug.medicinalproduct:"X"
        &count=patient.reaction.reactionmeddrapt.exact

必须写在报告里的局限（这一条不能省）：
  FAERS 是自发上报数据，**没有分母**，所以算不出发生率，只能看构成。
  上报还存在严重偏倚（Weber 效应、诉讼驱动的上报、媒体报道后的短期激增），
  并且无法区分"药物引起"和"疾病本身导致的症状"。
  因此这里只把不良反应谱作为需要权衡的信号，不做因果推断，
  置信度也相应压低。

方向：存在明确的不良反应谱对靶点不利，所以 high_is_favorable=False。

注意：本类依赖 ChEMBL 提供的药物清单。没有同靶点药物时返回
UNKNOWN 而不是编造信号。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..normalize import make_record
from ..schema import Dimension, EvidenceRecord
from .base import Connector, ConnectorError, HttpClient
from .chembl import DruggabilityProfile, fetch_profile

OPENFDA_API = "https://api.fda.gov/drug/event.json"

# 上报量分档。FAERS 没有分母，只能用总量级作为"证据充分程度"的代理。
REPORT_BANDS = ((500, 0.90), (100, 0.75), (20, 0.60), (1, 0.40))


@dataclass
class ToxicitySignal:
    gene: str
    drug: str
    total_reports: int
    top_reactions: list[tuple[str, int]]
    url: str

    @property
    def strength_from_volume(self) -> float:
        for threshold, score in REPORT_BANDS:
            if self.total_reports >= threshold:
                return score
        return 0.0


def fetch_drug_reactions(
    drug: str, client: HttpClient, top_n: int = 8
) -> ToxicitySignal | None:
    search = f'patient.drug.medicinalproduct:"{drug}"'
    try:
        payload = client.get_json(
            OPENFDA_API,
            {
                "search": search,
                "count": "patient.reaction.reactionmeddrapt.exact",
                "limit": top_n,
            },
        )
    except ConnectorError:
        return None

    results = payload.get("results") or []
    if not results:
        return None

    # 注意：count 查询里的 meta.results.total 不是上报总数，而是聚合结果的条数。
    # 取上报总数必须另发一次普通查询。实测确认过这一点：用 count 的 total
    # 会得到 0，而 0 会被下游误读成"没有不良反应"，那是完全相反的信号。
    total = 0
    try:
        probe = client.get_json(OPENFDA_API, {"search": search, "limit": 1})
        total = int((probe.get("meta") or {}).get("results", {}).get("total", 0) or 0)
    except ConnectorError:
        total = 0

    top = [(str(r.get("term") or ""), int(r.get("count") or 0)) for r in results[:top_n]]
    return ToxicitySignal(
        gene="",
        drug=drug,
        total_reports=total,
        top_reactions=top,
        url=f"https://open.fda.gov/apis/drug/event/",
    )


def summarize(
    gene: str, profile: DruggabilityProfile, signals: list[ToxicitySignal]
) -> list[EvidenceRecord]:
    url = "https://open.fda.gov/apis/drug/event/"
    # 只保留真的有上报记录的药物。total 为 0 表示查不到，不是"安全"。
    usable = [s for s in signals if s.total_reports > 0]
    if not usable:
        queried = "、".join(s.drug for s in signals)
        reason = (
            f"已查询同靶点药物 {queried}，但未取到上报记录"
            if signals
            else f"{gene} 目前没有可用于查询不良反应的同靶点药物"
        )
        return [
            make_record(
                dimension=Dimension.ON_TARGET_TOXICITY,
                source="openFDA FAERS",
                metric="known_drug_ae_profile",
                statement=f"{reason}，该维度暂无经验性证据。",
                percentile=None,
                value=None,
                url=url,
                confidence=0.2,
            )
        ]

    strongest = max(usable, key=lambda s: s.total_reports)
    percentile = strongest.strength_from_volume
    reaction_text = "、".join(f"{term}（{count} 例）" for term, count in strongest.top_reactions[:5])

    return [
        make_record(
            dimension=Dimension.ON_TARGET_TOXICITY,
            source="openFDA FAERS",
            metric="known_drug_ae_profile",
            statement=(
                f"同靶点药物 {strongest.drug} 的自发上报中记录到 "
                f"{strongest.total_reports} 例不良反应，主要构成为：{reaction_text}。"
                f"共查询了 {len(signals)} 个同靶点药物，其中 {len(usable)} 个取到记录。"
                f"需要注意，上报记录里的反应字段可能混入适应证本身（例如把肺癌当作反应上报），"
                f"不能直接读作药物引起的事件。"
                f"FAERS 无分母，无法计算发生率，且存在上报偏倚，"
                f"本条仅作为需要权衡的安全信号，不构成因果关系判断。"
            ),
            percentile=percentile,
            value=strongest.total_reports,
            unit="reports",
            cohort="FAERS (spontaneous reports)",
            url=url,
            confidence=0.35,
            raw={
                "drugs_queried": [s.drug for s in signals],
                "drugs_with_reports": [s.drug for s in usable],
                "drug": strongest.drug,
                "total_reports": strongest.total_reports,
                "top_reactions": strongest.top_reactions,
            },
        )
    ]


class FaersConnector(Connector):
    name = "faers"
    dimensions = (Dimension.ON_TARGET_TOXICITY,)

    def __init__(
        self,
        client: HttpClient | None = None,
        max_drugs: int = 5,
        profile: DruggabilityProfile | None = None,
    ):
        self.client = client or HttpClient()
        self.max_drugs = max_drugs
        # 允许外部复用已经拉过的 ChEMBL 结果，避免重复请求
        self.profile = profile

    def fetch(self, req: Any) -> list[EvidenceRecord]:
        gene = (getattr(req, "gene", None) or str(req)).upper()
        profile = self.profile or fetch_profile(gene, self.client)
        drugs = profile.known_drugs[: self.max_drugs]
        signals = []
        for drug in drugs:
            signal = fetch_drug_reactions(drug, self.client)
            if signal:
                signal.gene = gene
                signals.append(signal)
        return summarize(gene, profile, signals)
