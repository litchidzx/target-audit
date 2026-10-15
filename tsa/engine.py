"""编排层：拉取 -> 检测 -> 汇总。

connector 是唯一的 I/O 边界。任何 connector 抛错都只降级为"该维度缺失"，
不能让整个审计失败 —— 七个数据源里挂掉两三个是常态。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .conflicts import detect_conflicts
from .connectors.base import Connector, ConnectorError
from .explain import explain_conflicts
from .llm.base import LLMProvider
from .schema import AuditCard, Dimension, EvidenceRecord, Polarity

log = logging.getLogger("tsa.engine")


@dataclass
class AuditRequest:
    gene: str
    cancer: str          # 癌种为必填参数，系统不设默认癌种
    ensembl_id: str | None = None


def _collect(
    req: AuditRequest, connectors: list[Connector]
) -> tuple[list[EvidenceRecord], dict[str, str]]:
    records: list[EvidenceRecord] = []
    errors: dict[str, str] = {}
    for conn in connectors:
        try:
            got = conn.fetch(req)
        except ConnectorError as exc:
            errors[conn.name] = str(exc)
            log.warning("connector %s 失败: %s", conn.name, exc)
            continue
        except Exception as exc:  # noqa: BLE001 - 单个数据源不许拖垮整体
            errors[conn.name] = f"未预期错误: {exc}"
            log.exception("connector %s 未预期错误", conn.name)
            continue
        records.extend(got)
    return records, errors


def _missing_dimensions(records: list[EvidenceRecord]) -> list[Dimension]:
    have = {r.dimension for r in records if r.is_actionable}
    return [d for d in Dimension if d not in have]


def _therapeutic_window(records: list[EvidenceRecord]) -> str:
    """治疗窗判断。

    注意统计口径：这里要把正常侧**全部**证据纳入分母，而不是只数"有方向"的那些。
    只数有方向的记录会让单条不利证据直接变成 100%，把治疗窗误判成"全线告警"。

    未知与中性证据既不贡献风险也不贡献保护，但它们的存在说明这一侧证据不足，
    所以单独统计有方向证据的数量来决定结论是否可信。
    """
    normal_side = [
        r
        for r in records
        if r.dimension
        in (
            Dimension.NORMAL_EXPRESSION,
            Dimension.GENETIC_CONSTRAINT,
            Dimension.ON_TARGET_TOXICITY,
        )
    ]
    risky = [r for r in normal_side if r.polarity is Polarity.UNFAVORABLE and r.strength > 0]
    protective = [r for r in normal_side if r.polarity is Polarity.FAVORABLE and r.strength > 0]

    decisive = len(risky) + len(protective)
    # 只有一两条有向证据时，比例很容易顶到 1.0，判成"全线告警"是不稳的。
    # 宁可说证据不足，也不要给一个经不起追问的结论。
    if decisive < 2:
        return "unknown（正常侧有效证据不足）"
    ratio = len(risky) / decisive
    if ratio == 1.0 and decisive >= 3:
        return "narrow（正常侧全线告警）"
    if ratio >= 0.6:
        return "moderate（部分正常侧风险）"
    return "wide（正常侧证据支持）"


def _verdict(card: AuditCard) -> tuple[str, str]:
    """给一句判断和一句主标题。

    判断按冲突的**类型**给，而不是按严重度阈值。早期版本用"严重度 ≥ 0.7 就判
    高风险"，结果是每个基因都是"高风险"，这个结论不携带任何信息。不同类型的
    冲突对应的是完全不同的问题，判断必须把这个区别保留下来。
    """
    if not card.conflicts:
        return "值得推进", f"{card.gene} 各维度证据方向一致，未见明显矛盾。"

    verdict_by_type = {
        "loss_dominant_alteration": "需要换策略",
        "important_but_undruggable": "成药路径不明",
        "feasible_but_unsafe": "安全边界待确认",
        "narrow_therapeutic_window": "治疗窗待确认",
        "lacks_uniqueness": "选择性不足",
        "expression_dependency_decoupling": "模型体系存疑",
        "genetic_fragility": "抑制深度需严格控制",
    }
    headline_by_type = {
        "loss_dominant_alteration": (
            f"{card.gene} 在病人中改变频率高，但以缺失失活为主 —— "
            f"没有可直接抑制的靶点产物，需要换策略而不是换分子。"
        ),
        "expression_dependency_decoupling": (
            f"{card.gene} 的前临床与临床证据存在系统性脱节，"
            f"需先确认哪个模型体系可信。"
        ),
        "important_but_undruggable": f"{card.gene} 重要，但目前看不到成药路径。",
        "feasible_but_unsafe": f"{card.gene} 做得出来，但安全边界可能很窄。",
        "narrow_therapeutic_window": f"{card.gene} 治疗窗可能很窄。",
        "lacks_uniqueness": f"{card.gene} 重要性充分，独特性不足。",
        "genetic_fragility": f"{card.gene} 的功能缺失不耐受，抑制深度需要严格控制。",
    }

    top = card.conflicts[0]
    verdict = verdict_by_type.get(top.type.value, "存在需要解释的矛盾")
    headline = headline_by_type.get(
        top.type.value, f"{card.gene} 存在需要解释的证据矛盾。"
    )
    if len(card.conflicts) > 1:
        others = "、".join(
            verdict_by_type.get(c.type.value, c.type.value) for c in card.conflicts[1:]
        )
        verdict = f"{verdict}（另有：{others}）"
    return verdict, headline


def run_audit(
    req: AuditRequest,
    connectors: list[Connector],
    llm: LLMProvider | None = None,
) -> AuditCard:
    """执行一次审计。

    llm 为空时只跑规则层，冲突的候选原因与建议实验照常输出，只是没有模型解释。
    传入 provider 后，解释层会填充每条冲突的 explanation 与 metadata。
    """
    records, errors = _collect(req, connectors)
    conflicts = detect_conflicts(records, gene=req.gene)
    missing = _missing_dimensions(records)

    if llm is not None and conflicts:
        explain_conflicts(conflicts, llm, req.gene, req.cancer)

    card = AuditCard(
        gene=req.gene,
        cancer=req.cancer,
        records=records,
        conflicts=conflicts,
        missing_dimensions=missing,
        therapeutic_window=_therapeutic_window(records),
    )
    card.verdict, card.headline = _verdict(card)
    if errors:
        card.headline += f"（{len(errors)} 个数据源未返回结果）"
    return card
