"""百分位 -> 极性 + 强度。

这是"不跨维度比较原始值"这条约束的落地位置。所有 connector 都必须先在本维度
内部算出百分位，再由这里统一转成极性，方向定义只在 dimensions.py 里出现一次。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .dimensions import DIMENSION_META
from .schema import Dimension, EvidenceRecord, Polarity

LOW_CUT = 0.30
HIGH_CUT = 0.70


def percentile_rank(value: float, population: Sequence[float]) -> float:
    """value 在 population 中的百分位（0-1）。用中点法处理并列。"""
    n = len(population)
    if n == 0:
        raise ValueError("population 为空，无法计算百分位")
    below = sum(1 for v in population if v < value)
    equal = sum(1 for v in population if v == value)
    return (below + 0.5 * equal) / n


def to_signal(
    percentile: float,
    high_is_favorable: bool,
    low_cut: float = LOW_CUT,
    high_cut: float = HIGH_CUT,
) -> tuple[Polarity, float]:
    """把百分位映射成 (极性, 强度)。

    强度用偏离中位的程度衡量，所以"极高"和"极低"都会给出强信号，
    只是方向由 high_is_favorable 决定。
    """
    p = min(max(percentile, 0.0), 1.0)
    strength = abs(p - 0.5) * 2.0

    if p >= high_cut:
        polarity = Polarity.FAVORABLE if high_is_favorable else Polarity.UNFAVORABLE
    elif p <= low_cut:
        polarity = Polarity.UNFAVORABLE if high_is_favorable else Polarity.FAVORABLE
    else:
        polarity = Polarity.NEUTRAL
    return polarity, strength


def make_record(
    *,
    dimension: Dimension,
    source: str,
    metric: str,
    statement: str,
    percentile: float | None,
    value: object = None,
    unit: str | None = None,
    cohort: str | None = None,
    url: str | None = None,
    confidence: float = 0.6,
    raw: dict | None = None,
) -> EvidenceRecord:
    """按统一规则构造一条证据。percentile 缺失即标记为 UNKNOWN。"""
    meta = DIMENSION_META[dimension]
    if percentile is None:
        return EvidenceRecord(
            dimension=dimension,
            source=source,
            metric=metric,
            statement=statement,
            polarity=Polarity.UNKNOWN,
            strength=0.0,
            confidence=confidence,
            value=value,
            unit=unit,
            percentile=None,
            cohort=cohort,
            url=url,
            raw=raw or {},
        )
    polarity, strength = to_signal(percentile, meta.high_is_favorable)
    return EvidenceRecord(
        dimension=dimension,
        source=source,
        metric=metric,
        statement=statement,
        polarity=polarity,
        strength=round(strength, 4),
        confidence=confidence,
        value=value,
        unit=unit,
        percentile=round(percentile, 4),
        cohort=cohort,
        url=url,
        raw=raw or {},
    )


def summarize_group(records: Iterable[EvidenceRecord], dimension: Dimension) -> float | None:
    """某个维度的平均强度，用于快速汇总展示。"""
    vals = [r.strength for r in records if r.dimension is dimension and r.is_actionable]
    return sum(vals) / len(vals) if vals else None
