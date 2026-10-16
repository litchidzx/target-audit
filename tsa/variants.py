"""亚型分析层。

把两件事拼起来：
  * 变异谱（cBioPortal）—— 这个癌种里实际发生的是哪些变异，各占多少
  * 亚型成药（openFDA 说明书）—— 针对某个变异，现在有没有获批药物

输出一个对临床决策有直接意义的数字：**这个队列里有多大比例的改变，
是现有获批药物覆盖得到的。**

以 KRAS 为例，实测结果：

    胰腺癌   G12D(49) G12V(33) G12R(25) … G12C(1)
             获批药物只覆盖 G12C，覆盖率约 1%
    肺腺癌   G12C(70) G12V(40) G12D(20) …
             覆盖率约 40%

同一个基因，"有没有药"的答案在不同癌种里差了四十倍。**这就是不看亚型
会漏掉的东西**：只说"KRAS 有获批药物"，对胰腺癌患者毫无意义。

一条必须写明的局限：说明书只收录已获批药物，因此这里回答的是
"现在能不能用上药"，不是"有没有人在研发"。临床阶段的候选药不会体现。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .connectors.base import ConnectorError, HttpClient
from .connectors.cbioportal import CBioPortalConnector, VariantProfile
from .connectors.variant_drugs import fetch_variant_drugs

log = logging.getLogger("tsa.variants")

# 逐个变异查说明书是有成本的（每个变异一次请求），所以只查最高频的几个。
# 剩下的归入"其他"，不影响覆盖率的量级判断。
MAX_VARIANTS_QUERIED = 6

# 结构性变异（缺失、插入、移码）在说明书里通常按外显子或区域描述，
# 例如奥希替尼写的是"EGFR exon 19 deletions"，而不是 E746_A750del。
# 因此蛋白坐标的关键词匹配对这类变异不适用 —— 查不到不等于没有药。
# 必须把它与"查过、确实没有"区分开，否则会给出错误结论。
STRUCTURAL_MARKERS = ("DEL", "INS", "FS", "TER", "*", "SPLICE", ">")


def variant_kind(name: str) -> str:
    upper = name.upper()
    if any(m in upper for m in STRUCTURAL_MARKERS):
        return "structural"
    return "point"
@dataclass
class VariantBrief:
    name: str
    cases: int
    fraction: float
    drugs: list[str] = field(default_factory=list)
    kind: str = "point"

    @property
    def covered(self) -> bool:
        return bool(self.drugs)

    @property
    def determinate(self) -> bool:
        """关键词匹配对这条变异是否适用。

        点突变适用；结构性变异不适用，说明书写的是外显子而不是坐标。
        """
        return self.kind == "point"


@dataclass
class VariantAnalysis:
    gene: str
    cancer: str
    study_id: str
    altered_cases: int
    briefs: list[VariantBrief] = field(default_factory=list)
    other_variant_count: int = 0
    selected: VariantBrief | None = None
    headline: str = ""
    note: str = ""

    @property
    def covered_cases(self) -> int:
        return sum(b.cases for b in self.briefs if b.covered)

    @property
    def undetermined_cases(self) -> int:
        return sum(b.cases for b in self.briefs if not b.determinate)

    @property
    def determinate_cases(self) -> int:
        """可判定的病例数（分母）。结构性变异被排除，避免低估覆盖率。"""
        return max(0, self.altered_cases - self.undetermined_cases)

    @property
    def coverage(self) -> float:
        """在可判定的变异里，有获批药物覆盖的比例。"""
        base = self.determinate_cases
        return self.covered_cases / base if base else 0.0

    @property
    def dominant(self) -> VariantBrief | None:
        return self.briefs[0] if self.briefs else None

    def brief(self, name: str) -> VariantBrief | None:
        for b in self.briefs:
            if b.name.upper() == name.upper():
                return b
        return None


def _headline(analysis: VariantAnalysis) -> str:
    if not analysis.briefs:
        return "该癌种队列中没有取到变异谱数据。"
    cov = analysis.coverage
    # 结构性变异被排除在分母外，主标题必须说明，否则读者会以为这就是真实覆盖率。
    caveat = (
        f"另有 {analysis.undetermined_cases} 例结构性变异需人工核对。"
        if analysis.undetermined_cases
        else ""
    )

    if analysis.selected is not None:
        sel = analysis.selected
        if not sel.determinate:
            return (
                f"所选亚型 {sel.name} 是结构性变异，说明书通常按外显子描述，"
                f"无法用蛋白坐标自动匹配药物，需要人工核对。"
                f"该变异占本队列改变的 {sel.fraction:.0%}。"
            )
        if sel.covered:
            return (
                f"所选亚型 {sel.name} 有获批药物（{'、'.join(sel.drugs[:3])}），"
                f"该亚型占本队列改变的 {sel.fraction:.0%}。"
            )
        return (
            f"所选亚型 {sel.name} 目前没有获批药物，"
            f"占本队列改变的 {sel.fraction:.0%}。"
        )

    if cov >= 0.6:
        return f"可判定变异中 {cov:.0%} 有获批药物覆盖，亚型覆盖较好。{caveat}"
    if cov >= 0.25:
        return f"可判定变异中 {cov:.0%} 有获批药物覆盖，其余存在缺口。{caveat}"
    dominant = analysis.dominant
    if dominant is not None:
        return (
            f"可判定变异中 {cov:.0%} 有获批药物覆盖。主要改变 {dominant.name}"
            f"（占 {dominant.fraction:.0%}）目前没有获批药物 —— "
            f"「这个靶点有药」在本癌种里基本用不上。{caveat}"
        )
    return f"可判定变异中 {cov:.0%} 有获批药物覆盖。{caveat}"


def analyze(
    gene: str,
    cancer: str,
    *,
    selected_variant: str | None = None,
    client: HttpClient | None = None,
) -> VariantAnalysis | None:
    """生成亚型分析。数据取不到时返回 None，而不是抛异常。

    亚型分析是叠加在基因级审计之上的附加信息，它失败不应该让整个审计失败。
    """
    client = client or HttpClient()
    try:
        profile: VariantProfile = CBioPortalConnector(client).fetch_profile(gene, cancer)
    except ConnectorError as exc:
        log.warning("取不到 %s/%s 的变异谱：%s", gene, cancer, exc)
        return None

    if not profile.variants:
        return None

    total = profile.altered_samples
    briefs: list[VariantBrief] = []
    for name, cases in profile.variants[:MAX_VARIANTS_QUERIED]:
        drugs: list[str] = []
        try:
            drugs = fetch_variant_drugs(gene, name, client).drugs
        except ConnectorError as exc:
            log.warning("查 %s %s 的药物失败：%s", gene, name, exc)
        briefs.append(
            VariantBrief(
                name=name,
                cases=cases,
                fraction=cases / total if total else 0.0,
                drugs=drugs,
                kind=variant_kind(name),
            )
        )

    analysis = VariantAnalysis(
        gene=gene.upper(),
        cancer=cancer.upper(),
        study_id=profile.study_id,
        altered_cases=total,
        briefs=briefs,
        other_variant_count=max(0, len(profile.variants) - len(briefs)),
    )

    if selected_variant:
        chosen = analysis.brief(selected_variant)
        if chosen is None:
            # 用户输入的亚型不在高频列表里，单独查一次
            cases = profile.count_of(selected_variant)
            drugs: list[str] = []
            try:
                drugs = fetch_variant_drugs(gene, selected_variant, client).drugs
            except ConnectorError:
                pass
            chosen = VariantBrief(
                name=selected_variant.upper(),
                cases=cases,
                fraction=cases / total if total else 0.0,
                drugs=drugs,
                kind=variant_kind(selected_variant),
            )
        analysis.selected = chosen

    analysis.headline = _headline(analysis)
    analysis.note = (
        f"变异谱来自 cBioPortal（{profile.study_id}），"
        f"药物信息来自 openFDA 说明书的适应症文本。"
        f"覆盖率只统计可判定的变异：点突变可以用蛋白坐标检索说明书，"
        f"而缺失、插入等结构性变异在说明书里按外显子描述，关键词匹配不适用，"
        f"因此不计入分母。"
        f"说明书只收录已获批药物，回答的是「现在能不能用上药」，"
        f"不包含临床阶段候选药。"
    )
    return analysis
