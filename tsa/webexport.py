"""把审计结果导出成一个可以离线打开的工作台。

关键取舍：**不做前后端分离**。

审计结果在构建时全部算好、内嵌进一个 JS 数据文件，界面用原生 JS 渲染。
这样带来三个确定的收益：

1. 打开即用。托管到 GitHub Pages 是一个链接；下载仓库后双击 docs/index.html
   也能直接用，不需要装 Python、不需要联网、不需要 400MB 参考数据。
2. 演示不会失败。现场不依赖网络，也不会因为某个外部接口抽风而打不开。
3. 结果可复核。页面上每个数字都能点回原始数据源，且与命令行跑出来的完全一致
   —— 因为两者用的是同一个引擎，只是数据在构建时被固化了。

代价是它不能审计任意基因（那需要实时跑六个数据源）。所以定位是
"可浏览的审计结果集 + 交互式阅读器"，而不是在线计算服务。这一点在页面上
有明确说明，不假装自己能做做不到的事。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .dimensions import DIMENSION_META, label, short_label
from .paths import asset_path
from .schema import CAUSE_LABEL_ZH, AuditCard, Dimension, EvidenceRecord, to_plain

ASSETS = asset_path("tsa", "webassets")

CANCER_ZH = {
    "PAAD": "胰腺癌", "LUAD": "肺腺癌", "LUSC": "肺鳞癌", "COAD": "结肠癌",
    "READ": "直肠癌", "STAD": "胃癌", "LIHC": "肝癌", "KIRC": "肾癌",
    "BRCA": "乳腺癌", "OV": "卵巢癌", "UCEC": "子宫内膜癌", "HNSC": "头颈癌",
    "BLCA": "膀胱癌", "SKCM": "黑色素瘤", "GBM": "胶质母细胞瘤", "PRAD": "前列腺癌",
    "THCA": "甲状腺癌", "CESC": "宫颈癌", "CHOL": "胆管癌",
}

GROUP_TITLE = {
    "tumor": "肿瘤侧 · 有多重要、有多独特",
    "clinical": "临床侧 · 真实患者中是什么样",
    "normal": "正常侧 · 抑制它会不会伤到正常组织",
    "feasibility": "可做性 · 做不做得出来",
}

# 规则编号到中文名的映射。放在这里是因为报告和图表的命名要一致。
RULE_DESC = {
    "R1": "治疗窗狭窄",
    "R2": "缺乏独特性",
    "R3": "表达与依赖脱节",
    "R4": "重要但难成药",
    "R5": "可成药但不安全",
    "R6": "遗传学脆弱",
    "R7": "缺失主导型改变",
}


def _record_dict(record: EvidenceRecord) -> dict[str, Any]:
    return {
        "dimension": record.dimension.value,
        "label": label(record.dimension),
        "group": DIMENSION_META[record.dimension].group.value,
        "polarity": record.polarity.value,
        "strength": round(record.strength, 4),
        "confidence": round(record.confidence, 4),
        "percentile": record.percentile,
        "value": record.value,
        "unit": record.unit,
        "cohort": record.cohort,
        "source": record.source,
        "statement": record.statement,
        "url": record.url,
    }


def _variant_dict(card: AuditCard) -> dict[str, Any] | None:
    analysis = getattr(card, "variant_analysis", None)
    if analysis is None or not analysis.briefs:
        return None
    return {
        "study_id": analysis.study_id,
        "altered_cases": analysis.altered_cases,
        "determinate_cases": analysis.determinate_cases,
        "covered_cases": analysis.covered_cases,
        "coverage": round(analysis.coverage, 4),
        "headline": analysis.headline,
        "note": analysis.note,
        "other_variant_count": analysis.other_variant_count,
        "variants": [
            {
                "name": b.name,
                "cases": b.cases,
                "fraction": round(b.fraction, 4),
                "drugs": b.drugs,
                "kind": b.kind,
            }
            for b in analysis.briefs
        ],
    }


def card_to_dict(card: AuditCard) -> dict[str, Any]:
    total_dims = len(list(Dimension))
    covered = total_dims - len(card.missing_dimensions)
    return {
        "gene": card.gene,
        "cancer": card.cancer,
        "cancer_zh": CANCER_ZH.get(card.cancer, card.cancer),
        "verdict": card.verdict,
        "headline": card.headline,
        "therapeutic_window": card.therapeutic_window,
        "coverage": {"covered": covered, "total": total_dims},
        "generated_at": card.generated_at,
        "conflicts": [
            {
                "rule_id": c.rule_id,
                "type": c.type.value,
                "type_zh": RULE_DESC.get(c.rule_id, c.type.value),
                "headline": c.headline,
                "severity": round(c.severity, 4),
                "dimensions": [d.value for d in c.dimensions],
                "dimension_labels": [label(d) for d in c.dimensions],
                "causes": [CAUSE_LABEL_ZH[cause] for cause in c.cause_candidates],
                "experiment": c.suggested_experiment,
                "explanation": c.explanation,
                "explanation_meta": c.metadata or {},
            }
            for c in card.conflicts
        ],
        "evidence": [_record_dict(r) for r in card.records],
        "variant_analysis": _variant_dict(card),
        "missing": [
            {"dimension": d.value, "label": label(d), "hint": DIMENSION_META[d].source_hint}
            for d in card.missing_dimensions
        ],
    }


def build_dataset(cards: list[AuditCard]) -> dict[str, Any]:
    items = [card_to_dict(c) for c in cards]
    rules = sorted({c["rule_id"] for item in items for c in item["conflicts"]})
    cancers = sorted({item["cancer_zh"] for item in items})
    return {
        "generated_at": items[0]["generated_at"] if items else "",
        "counts": {
            "targets": len(items),
            "cancers": len(cancers),
            "dimensions": len(list(Dimension)),
            "rules_fired": len(rules),
            "rules_total": len(RULE_DESC),
        },
        "cancers": cancers,
        "rules": [{"id": r, "label": RULE_DESC.get(r, r)} for r in rules],
        "rule_catalog": [{"id": k, "label": v} for k, v in RULE_DESC.items()],
        # 维度目录：前端渲染总览条与分组时都靠它，避免在 JS 里硬编码中文
        "dimensions": [
            {
                "value": d.value,
                "label": label(d),
                "short": short_label(d),
                "group": DIMENSION_META[d].group.value,
                "hint": DIMENSION_META[d].source_hint,
            }
            for d in Dimension
        ],
        "groups": GROUP_TITLE,
        "targets": items,
    }


def write_site(cards: Iterable[AuditCard], out_dir) -> list[str]:
    """把审计结果写成可离线打开的工作台，返回生成的文件列表。

    刻意输出**扁平结构**（所有文件在同一层），而不是 index.html + assets/。
    原因是托管平台的目录处理规则不一致：GitHub Pages 保留层级，但魔搭创空间的
    网页上传会把拖进去的文件夹拍平。扁平结构两边都能直接用，省掉一类难查的故障。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    dataset = build_dataset(list(cards))

    (out / "style.css").write_text(
        (ASSETS / "style.css").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (out / "app.js").write_text(
        (ASSETS / "app.js").read_text(encoding="utf-8"), encoding="utf-8"
    )
    # 数据以 JS 变量内嵌，而不是 fetch 一个 json —— 这样用 file:// 直接打开也能用，
    # 不需要起本地服务器。这是"下载后双击就能用"的前提。
    (out / "data.js").write_text(
        "window.AUDIT_DATA = " + json.dumps(to_plain(dataset), ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    (out / "index.html").write_text(
        (ASSETS / "index.html").read_text(encoding="utf-8"), encoding="utf-8"
    )
    # favicon 也要一起放到站点根目录，否则浏览器标签页没有图标
    favicon = ASSETS / "favicon.png"
    if favicon.exists():
        (out / "favicon.png").write_bytes(favicon.read_bytes())

    return ["style.css", "app.js", "data.js", "favicon.png", "index.html"]
