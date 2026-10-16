"""Markdown 报告渲染。

输出的形态就是产品定位本身：默认展示冲突，而不是分数。
"""

from __future__ import annotations

from .dimensions import DIMENSION_META, label
from .schema import AuditCard, CAUSE_LABEL_ZH, EvidenceRecord, Group, Polarity

POLARITY_MARK = {
    Polarity.FAVORABLE: "＋",
    Polarity.UNFAVORABLE: "－",
    Polarity.NEUTRAL: "·",
    Polarity.UNKNOWN: "?",
}

GROUP_TITLE = {
    Group.TUMOR: "肿瘤侧：有多重要、有多独特",
    Group.CLINICAL: "临床侧：真实病人里是什么样",
    Group.NORMAL: "正常侧：打它会不会伤到好人",
    Group.FEASIBILITY: "可做性",
}


def _conflict_dims(c) -> str:
    """冲突涉及哪些维度。

    R7 这类"同一维度内部方向矛盾"的冲突，两个方向都落在临床相关性上，
    直接拼会显示成"临床相关性 × 临床相关性"，所以要改列具体的证据指标。
    """
    labels = list(dict.fromkeys(label(d) for d in c.dimensions))
    if len(labels) == 1:
        metrics = " / ".join(dict.fromkeys(r.metric for r in c.evidence))
        return f"{labels[0]}（{metrics}）"
    return " × ".join(labels)


def _evidence_line(r: EvidenceRecord) -> str:
    pct = f"分位 {r.percentile:.2f}" if r.percentile is not None else "分位 —"
    src = f"[{r.source}]({r.url})" if r.url else r.source
    return (
        f"  {POLARITY_MARK[r.polarity]} **{label(r.dimension)}** · {pct} · "
        f"强度 {r.strength:.2f} · {src}\n"
        f"    {r.statement}"
    )


def _variant_section(card: AuditCard) -> list[str]:
    """亚型分析。

    这是"有没有药"这句话的适用边界 —— 同一个基因在不同癌种里答案可能差几十倍，
    不拆开亚型就看不出这一点。
    """
    analysis = getattr(card, "variant_analysis", None)
    if analysis is None or not analysis.briefs:
        return []

    out: list[str] = ["## 亚型分析", ""]
    out.append(f"**{analysis.headline}**")
    out.append("")
    out.append(f"*样本范围：{analysis.study_id}，共 {analysis.altered_cases} 例存在改变*")
    out.append("")
    out.append("| 变异 | 病例 | 占改变 | 获批药物 |")
    out.append("|---|---|---|---|")
    for b in analysis.briefs:
        if b.drugs:
            drugs = "、".join(b.drugs[:3])
            if len(b.drugs) > 3:
                drugs += f" 等 {len(b.drugs)} 个"
        elif not b.determinate:
            drugs = "不适用（说明书按外显子描述）"
        else:
            drugs = "—"
        out.append(f"| {b.name} | {b.cases} | {b.fraction:.0%} | {drugs} |")
    if analysis.other_variant_count:
        out.append(
            f"| 其他 {analysis.other_variant_count} 种低频变异 | — | — | 未逐一查询 |"
        )
    out.append("")
    if analysis.selected is not None:
        sel = analysis.selected
        out.append(
            f"**所选亚型 {sel.name}**：占本队列改变的 {sel.fraction:.0%}"
            f"（{sel.cases} 例）。"
        )
        out.append("")
    out.append(f"*{analysis.note}*")
    out.append("")
    return out


def render_markdown(card: AuditCard) -> str:
    out: list[str] = []
    out.append(f"# 靶点审计 · {card.gene} / {card.cancer}")
    out.append("")
    out.append(f"**判断：** {card.verdict}")
    out.append("")
    out.append(f"**主标题：** {card.headline}")
    out.append("")
    out.append(f"**治疗窗：** {card.therapeutic_window}")
    out.append("")
    out.append(f"*生成时间 {card.generated_at}*")
    out.append("")

    out.append("## 冲突")
    out.append("")
    if not card.conflicts:
        out.append("未检出明显矛盾。")
        out.append("")
    for c in card.conflicts:
        out.append(f"### {c.rule_id} · {c.type.value}")
        out.append("")
        out.append(f"**{c.headline}**")
        out.append("")
        out.append(f"- 涉及维度：{_conflict_dims(c)}")
        out.append(f"- 严重度：{c.severity:.2f}")
        out.append("- 候选原因（受控词表）：" if c.explanation else "- 候选原因（受控词表，待解释层确认）：")
        for cause in c.cause_candidates:
            out.append(f"  - {CAUSE_LABEL_ZH[cause]}")
        if c.suggested_experiment:
            out.append(f"- 建议的区分实验：{c.suggested_experiment}")
        out.append("")
        if c.explanation:
            out.append(f"> {c.explanation}")
            out.append("")
            meta = c.metadata or {}
            if meta:
                source = meta.get("source", "unknown")
                provider = meta.get("provider", "")
                if source != "llm":
                    origin = "规则层候选原因（模型解释不可用或未通过校验）"
                elif provider == "stub":
                    origin = "确定性兜底（未调用模型）"
                else:
                    origin = f"模型 {meta.get('model') or provider or '未知'}"
                out.append(f"  *解释来源：{origin}；提示词指纹 {meta.get('prompt_hash', '—')}*")
                out.append("")

    out.extend(_variant_section(card))

    out.append("## 证据矩阵")
    out.append("")
    by_dim = card.by_dimension()
    for group in (Group.TUMOR, Group.CLINICAL, Group.NORMAL, Group.FEASIBILITY):
        dims = [d for d, m in DIMENSION_META.items() if m.group is group]
        out.append(f"**{GROUP_TITLE[group]}**")
        out.append("")
        for d in dims:
            items = by_dim[d]
            if not items:
                out.append(f"  ? **{label(d)}** · 证据不足")
            for r in items:
                out.append(_evidence_line(r))
        out.append("")

    if card.missing_dimensions:
        out.append("## 缺失证据")
        out.append("")
        for d in card.missing_dimensions:
            out.append(f"- {label(d)}（{DIMENSION_META[d].source_hint}）")
        out.append("")

    out.append("---")
    out.append("")
    out.append(
        "本报告为研究用途的靶点证据整理，不构成任何医疗或投资建议。"
        "所有结论均可通过上方链接回溯至原始数据源。"
    )
    return "\n".join(out)
