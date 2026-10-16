"""统一证据模型 —— 整个审计引擎的公共语言。

这是这个项目最重要的一个工程决定：所有数据源的输出，先被规约成同一种
EvidenceRecord，之后的冲突检测、解释、报告渲染都只跟这个结构打交道。
新增一个数据源只需要写一个 connector，不需要动下游任何代码。

三条设计原则：

1. 归一化必须先在维度内部完成，再映射成"极性 + 强度"。绝不跨维度比较原始值
   —— 表达 TPM、IC50、生存 HR 之间没有可比性。
2. 每条证据都保留 raw 和 url，任何结论都能追溯回原始来源。
3. 极性只回答一个问题：这条证据对"这个靶点值得继续推进"是有利还是不利。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Dimension(str, Enum):
    DEPENDENCY = "dependency"
    SELECTIVITY = "selectivity"
    CLINICAL = "clinical"
    NORMAL_EXPRESSION = "normal_expression"
    GENETIC_CONSTRAINT = "genetic_constraint"
    DRUGGABILITY = "druggability"
    ON_TARGET_TOXICITY = "on_target_toxicity"


class Group(str, Enum):
    TUMOR = "tumor"
    CLINICAL = "clinical"
    NORMAL = "normal"
    FEASIBILITY = "feasibility"


class Polarity(str, Enum):
    FAVORABLE = "favorable"
    UNFAVORABLE = "unfavorable"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class ConflictType(str, Enum):
    NARROW_THERAPEUTIC_WINDOW = "narrow_therapeutic_window"
    LACKS_UNIQUENESS = "lacks_uniqueness"
    EXPRESSION_DEPENDENCY_DECOUPLING = "expression_dependency_decoupling"
    IMPORTANT_BUT_UNDRUGGABLE = "important_but_undruggable"
    FEASIBLE_BUT_UNSAFE = "feasible_but_unsafe"
    GENETIC_FRAGILITY = "genetic_fragility"
    LOSS_DOMINANT_ALTERATION = "loss_dominant_alteration"


class ConflictCause(str, Enum):
    """受控词表。

    LLM 解释冲突时只能从这个清单里选，不允许自由发挥 —— 这是防止幻觉的
    关键设计，也是"解释可复现"的前提。
    """

    MODEL_SYSTEM_DIFFERENCE = "model_system_difference"
    EXPRESSION_DEPENDENCY_DECOUPLING = "expression_dependency_decoupling"
    SAMPLE_SIZE_OR_BIAS = "sample_size_or_bias"
    TRANSCRIPT_OR_LOCALIZATION = "transcript_or_localization"
    PATHWAY_REDUNDANCY = "pathway_redundancy"
    TEMPORAL_DYNAMICS = "temporal_dynamics"
    TUMOR_SUPPRESSOR_MECHANISM = "tumor_suppressor_mechanism"


CAUSE_LABEL_ZH: dict[ConflictCause, str] = {
    ConflictCause.MODEL_SYSTEM_DIFFERENCE: "模型体系差异（细胞系 / 类器官 / PDX 结论不一致）",
    ConflictCause.EXPRESSION_DEPENDENCY_DECOUPLING: "表达与依赖脱节（表达高不等于敲掉会死）",
    ConflictCause.SAMPLE_SIZE_OR_BIAS: "样本量不足或选择偏倚",
    ConflictCause.TRANSCRIPT_OR_LOCALIZATION: "转录本或亚细胞定位差异",
    ConflictCause.PATHWAY_REDUNDANCY: "通路冗余（有代偿通路）",
    ConflictCause.TEMPORAL_DYNAMICS: "时间动态（不同疾病阶段结论不同）",
    ConflictCause.TUMOR_SUPPRESSOR_MECHANISM: "抑癌基因失活机制（以缺失/功能丧失为主，不可直接抑制）",
}


@dataclass
class EvidenceRecord:
    """一条证据。

    polarity / strength 由 normalize 层从百分位映射而来；value / raw 保留原始值，
    保证任何结论都能回退到源头复核。
    """

    dimension: Dimension
    source: str
    metric: str
    statement: str
    polarity: Polarity = Polarity.UNKNOWN
    strength: float = 0.0
    confidence: float = 0.0
    value: Any = None
    unit: str | None = None
    percentile: float | None = None
    cohort: str | None = None
    url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    retrieved_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @property
    def is_actionable(self) -> bool:
        """是否构成一个可用于冲突检测的信号。"""
        return self.polarity in (Polarity.FAVORABLE, Polarity.UNFAVORABLE) and self.strength > 0


@dataclass
class Conflict:
    """一条被规则层定位出来的矛盾。

    explanation 与 suggested_experiment 由 LLM 层填写；在那之前它是一个完整的、
    可独立呈现的对象 —— 这正是"规则先定位、模型后解释"的落地方式。
    """

    type: ConflictType
    dimensions: list[Dimension]
    evidence: list[EvidenceRecord]
    severity: float
    headline: str
    cause_candidates: list[ConflictCause] = field(default_factory=list)
    explanation: str | None = None
    suggested_experiment: str | None = None
    rule_id: str = ""
    # 解释层的来源信息：用了哪个模型、提示词指纹、尝试次数、是否降级。
    # 放在这里是为了让报告能说明"这句话是谁说的"。
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditCard:
    """一个靶点的完整审计结果，也是导出报告与前端渲染的唯一数据源。"""

    gene: str
    cancer: str
    records: list[EvidenceRecord]
    conflicts: list[Conflict]
    missing_dimensions: list[Dimension]
    verdict: str = ""
    headline: str = ""
    therapeutic_window: str = "unknown"
    # 亚型分析（tsa.variants.VariantAnalysis）。放在这里是 Any 以避免
    # schema 反过来依赖 connectors —— 这一层要保持零依赖。
    variant_analysis: Any = None
    # 生效的治疗模态评价标准（tsa.mapping.EffectiveMapping），同样用 Any 保持本层零依赖。
    # 未指定模态时为 None，报告里也就不会出现这一节。
    mapping: Any = None
    # 按模态设定不参与判断的维度。覆盖率的分母要据此收窄，
    # 否则对 CAR-T 这类靶点，"没有小分子成药性"会被误算成证据缺口。
    not_applicable_dimensions: list[Dimension] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def by_dimension(self) -> dict[Dimension, list[EvidenceRecord]]:
        out: dict[Dimension, list[EvidenceRecord]] = {d: [] for d in Dimension}
        for r in self.records:
            out[r.dimension].append(r)
        return out


def to_plain(obj: Any) -> Any:
    """把 dataclass / Enum 递归转成可 json 序列化的普通对象。"""
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, list):
        return [to_plain(x) for x in obj]
    if isinstance(obj, tuple):
        return [to_plain(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): to_plain(v) for k, v in obj.items()}
    if hasattr(obj, "__dataclass_fields__"):
        return {k: to_plain(getattr(obj, k)) for k in obj.__dataclass_fields__}
    return obj
