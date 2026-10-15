"""冲突检测。

核心设计：规则层只负责"定位"矛盾，不负责解释。定位必须是纯函数、可复现、
可单测的；解释才交给 LLM。这样即使模型换了，矛盾还是能被稳定找出来。

每条规则就是一对互相拉扯的信号。这不是随便定的，它对应靶点决策里最常见的
几种失败模式 —— 也正因为失败模式就那么几种，"认出冲突"才构成专业判断。
"""

from __future__ import annotations

from dataclasses import dataclass

from .dimensions import label
from .schema import (
    Conflict,
    ConflictCause,
    ConflictType,
    Dimension,
    EvidenceRecord,
    Polarity,
)

# 触发冲突所需的最低强度。低于这个值认为是噪声，不构成矛盾。
MIN_STRENGTH = 0.25


@dataclass(frozen=True)
class ConflictRule:
    rule_id: str
    type: ConflictType
    requires: tuple[tuple[Dimension, Polarity], ...]
    headline: str
    causes: tuple[ConflictCause, ...]
    experiment: str
    base_severity: float = 0.5


RULES: tuple[ConflictRule, ...] = (
    ConflictRule(
        rule_id="R1",
        type=ConflictType.NARROW_THERAPEUTIC_WINDOW,
        requires=(
            (Dimension.DEPENDENCY, Polarity.FAVORABLE),
            (Dimension.NORMAL_EXPRESSION, Polarity.UNFAVORABLE),
        ),
        headline="{gene} 高度重要，但正常组织广泛暴露 —— 治疗窗可能很窄。",
        causes=(
            ConflictCause.PATHWAY_REDUNDANCY,
            ConflictCause.MODEL_SYSTEM_DIFFERENCE,
        ),
        experiment="在正常原代细胞或类器官上做同一靶点的抑制实验，直接测杀伤选择性。",
        base_severity=0.6,
    ),
    ConflictRule(
        rule_id="R2",
        type=ConflictType.LACKS_UNIQUENESS,
        requires=(
            (Dimension.DEPENDENCY, Polarity.FAVORABLE),
            (Dimension.SELECTIVITY, Polarity.UNFAVORABLE),
        ),
        headline="{gene} 确实是依赖基因，但这份依赖不是本癌种独有 —— 缺的是独特性，不是重要性。",
        causes=(
            ConflictCause.PATHWAY_REDUNDANCY,
            ConflictCause.SAMPLE_SIZE_OR_BIAS,
        ),
        experiment="换用本癌种特异的模型体系（类器官或 PDX）复测依赖性，确认差异是否稳定。",
        base_severity=0.55,
    ),
    ConflictRule(
        rule_id="R3",
        type=ConflictType.EXPRESSION_DEPENDENCY_DECOUPLING,
        requires=(
            (Dimension.CLINICAL, Polarity.FAVORABLE),
            (Dimension.DEPENDENCY, Polarity.UNFAVORABLE),
        ),
        headline="{gene} 在病人数据里很突出，但敲掉它细胞并不死 —— 经典的表达与依赖脱节。",
        causes=(
            ConflictCause.EXPRESSION_DEPENDENCY_DECOUPLING,
            ConflictCause.MODEL_SYSTEM_DIFFERENCE,
            ConflictCause.PATHWAY_REDUNDANCY,
        ),
        experiment="在类器官或体内模型中做敲降，而不是在二维细胞系里判断依赖性。",
        base_severity=0.65,
    ),
    ConflictRule(
        rule_id="R4",
        type=ConflictType.IMPORTANT_BUT_UNDRUGGABLE,
        requires=(
            (Dimension.CLINICAL, Polarity.FAVORABLE),
            (Dimension.DRUGGABILITY, Polarity.UNFAVORABLE),
        ),
        headline="{gene} 在疾病中重要，但目前看不到成药路径。",
        causes=(
            ConflictCause.TRANSCRIPT_OR_LOCALIZATION,
            ConflictCause.MODEL_SYSTEM_DIFFERENCE,
        ),
        experiment="评估非小分子模态：降解剂、抗体、核酸药物，或换个口袋重做结构分析。",
        base_severity=0.5,
    ),
    ConflictRule(
        rule_id="R5",
        type=ConflictType.FEASIBLE_BUT_UNSAFE,
        requires=(
            (Dimension.DRUGGABILITY, Polarity.FAVORABLE),
            (Dimension.ON_TARGET_TOXICITY, Polarity.UNFAVORABLE),
        ),
        headline="{gene} 有现成的成药路径，但同靶点药物已知的不良反应提示在靶毒性风险高。",
        causes=(
            ConflictCause.MODEL_SYSTEM_DIFFERENCE,
            ConflictCause.SAMPLE_SIZE_OR_BIAS,
        ),
        experiment="按不良反应涉及的器官系统做靶点表达和功能验证，估算可耐受的抑制深度。",
        base_severity=0.6,
    ),
    ConflictRule(
        rule_id="R6",
        type=ConflictType.GENETIC_FRAGILITY,
        requires=(
            (Dimension.GENETIC_CONSTRAINT, Polarity.UNFAVORABLE),
            (Dimension.DRUGGABILITY, Polarity.FAVORABLE),
        ),
        headline="{gene} 具备成药条件，但人类遗传学证据提示功能缺失不耐受 —— 抑制深度需要严格控制。",
        causes=(
            ConflictCause.PATHWAY_REDUNDANCY,
            ConflictCause.TEMPORAL_DYNAMICS,
        ),
        experiment="查清 LoF 携带者的表型谱，确认毒性与抑制深度之间的剂量关系。",
        base_severity=0.55,
    ),
    # R7 是唯一一条"同一维度内部方向矛盾"的规则：一件事是基因改变得有多频繁，
    # 另一件事是它靠什么方式改变。肿瘤抑制基因改变频率很高，但主要是缺失失活，
    # 没有可直接抑制的产物 —— 这个区分是真实存在的，也是纯频率数字会掩盖的。
    ConflictRule(
        rule_id="R7",
        type=ConflictType.LOSS_DOMINANT_ALTERATION,
        requires=(
            (Dimension.CLINICAL, Polarity.FAVORABLE),
            (Dimension.CLINICAL, Polarity.UNFAVORABLE),
        ),
        headline=(
            "{gene} 在病人中改变频率高，但改变方式以缺失为主 —— 这是抑癌基因的典型模式，"
            "没有可直接抑制的靶点产物。"
        ),
        causes=(
            ConflictCause.TUMOR_SUPPRESSOR_MECHANISM,
            ConflictCause.PATHWAY_REDUNDANCY,
        ),
        experiment=(
            "评估合成致死策略（例如 PARP 之于 BRCA1/2）或下游通路，而不是直接抑制该基因产物。"
        ),
        base_severity=0.6,
    ),
)


def _pick(
    records: list[EvidenceRecord], dimension: Dimension, polarity: Polarity
) -> list[EvidenceRecord]:
    return [
        r
        for r in records
        if r.dimension is dimension and r.polarity is polarity and r.strength >= MIN_STRENGTH
    ]


def detect_conflicts(records: list[EvidenceRecord], gene: str = "") -> list[Conflict]:
    """按规则定位所有矛盾。纯函数，不依赖网络也不依赖模型。"""
    found: list[Conflict] = []
    for rule in RULES:
        matched: list[EvidenceRecord] = []
        ok = True
        for dimension, polarity in rule.requires:
            hit = _pick(records, dimension, polarity)
            if not hit:
                ok = False
                break
            matched.extend(hit)
        if not ok:
            continue

        mean_strength = sum(r.strength for r in matched) / len(matched)
        severity = min(1.0, rule.base_severity + 0.4 * mean_strength)
        found.append(
            Conflict(
                type=rule.type,
                dimensions=[d for d, _ in rule.requires],
                evidence=matched,
                severity=round(severity, 4),
                headline=rule.headline.format(gene=gene or "该靶点"),
                cause_candidates=list(rule.causes),
                suggested_experiment=rule.experiment,
                rule_id=rule.rule_id,
            )
        )

    found.sort(key=lambda c: c.severity, reverse=True)
    return found


def describe(conflict: Conflict) -> str:
    """给报告用的紧凑描述。"""
    dims = " × ".join(label(d) for d in conflict.dimensions)
    return f"[{conflict.rule_id}] {dims}（严重度 {conflict.severity:.2f}）"
