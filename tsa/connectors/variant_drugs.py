"""针对具体亚型的已获批药物。

回答的问题：某个变异（例如 KRAS G12D）现在有没有获批的药。

数据来源是**药品说明书的适应症文本**，用精确检索而不是文本摘要：

    GET /drug/label.json?search=indications_and_usage:"KRAS G12C"

这比"拿到药物清单再逐个读说明书找亚型"可靠得多 —— 那才叫大海捞针。
实测结果与已知事实一致：

    G12C  -> ADAGRASIB、SOTORASIB、PANITUMUMAB
    V600E -> 曲美替尼、恩考芬尼、维莫非尼等 9 个
    G12D  -> 无命中

**"无命中"是有意义的答案**，表示该亚型目前没有获批药物。openFDA 用 404 表达
这个情况，不是查询失败，必须区分开。

必须写在报告里的局限：说明书只收录**已获批**药物。临床阶段或临床前的候选药
不会出现。所以这里回答的是"现在能不能用上药"，不是"有没有人在做"。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import ConnectorError, HttpClient

OPENFDA_LABEL = "https://api.fda.gov/drug/label.json"


@dataclass
class VariantDrugs:
    gene: str
    variant: str
    drugs: list[str] = field(default_factory=list)
    matched_query: str = ""

    @property
    def has_drugs(self) -> bool:
        return bool(self.drugs)


def _drugs_from(payload: dict | None) -> list[str]:
    if not payload:
        return []
    names: list[str] = []
    for result in payload.get("results", []):
        for name in (result.get("openfda", {}) or {}).get("generic_name", []) or []:
            upper = str(name).strip().upper()
            if upper and upper not in names:
                names.append(upper)
    return names


def fetch_variant_drugs(
    gene: str, variant: str, client: HttpClient | None = None
) -> VariantDrugs:
    """查这个亚型有没有已获批药物。"""
    client = client or HttpClient()
    gene, variant = gene.upper(), variant.upper()

    # 先试"基因 + 亚型"的短语，最精确；命中不了再单独用亚型。
    # 两步都做是因为说明书写法不统一：有的写 "KRAS G12C"，有的只写 "G12C"。
    attempts = (f"{gene} {variant}", variant)
    for query in attempts:
        try:
            payload = client.get_json(
                OPENFDA_LABEL,
                {"search": f'indications_and_usage:"{query}"', "limit": 50},
                allow_404=True,
            )
        except ConnectorError:
            # 网络层面的问题往上抛；查询没命中已经由 allow_404 转成 None 了
            raise
        drugs = _drugs_from(payload)
        if drugs:
            return VariantDrugs(gene=gene, variant=variant, drugs=drugs, matched_query=query)

    return VariantDrugs(gene=gene, variant=variant)
