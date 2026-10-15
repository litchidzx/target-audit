"""从真实审计结果生成图表。

图表直接由引擎的输出渲染，不手工捏数据 —— 所以图和报告永远是一致的，
改了引擎重新跑一遍就同步了。

用法：
    python scripts/make_figures.py                       # 真实数据
    python scripts/make_figures.py --mock                # 离线跑通
    python scripts/make_figures.py --out docs/assets/figures
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tsa.dimensions import DIMENSION_META, label  # noqa: E402
from tsa.engine import AuditRequest, run_audit  # noqa: E402
from tsa.schema import AuditCard, Dimension, Group, Polarity  # noqa: E402

# 与站点配色保持一致
INK = "#12161c"
MUTED = "#5b6675"
LINE = "#e4e9f0"
FAV = "#177a4a"
UNFAV = "#bb3a2c"
NEUTRAL = "#b9c1cd"
UNKNOWN = "#eef0f4"
ACCENT = "#1f4fd1"
ACCENT_SOFT = "#eef2fe"

TARGETS = (
    ("KRAS", "PAAD"),
    ("KRAS", "LUAD"),
    ("KRAS", "COAD"),
    ("CDKN2A", "PAAD"),
    ("CDKN2A", "LUAD"),
    ("SMAD4", "PAAD"),
    ("TP53", "PAAD"),
    ("EGFR", "LUAD"),
    ("BRCA2", "PAAD"),
)

CANCER_ZH = {
    "PAAD": "胰腺癌",
    "LUAD": "肺腺癌",
    "COAD": "结肠癌",
    "STAD": "胃癌",
    "BRCA": "乳腺癌",
}


def setup_fonts() -> None:
    for path in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"):
        try:
            font_manager.fontManager.addfont(path)
        except Exception:  # noqa: BLE001
            pass
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "sans-serif"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.facecolor"] = "white"
    plt.rcParams["axes.edgecolor"] = LINE


def build_connectors(mock: bool):
    if mock:
        from tsa.mock import mock_connector

        return [mock_connector()]
    from tsa.connectors.chembl import ChEMBLConnector
    from tsa.connectors.depmap import DepMapConnector
    from tsa.connectors.faers import FaersConnector
    from tsa.connectors.gnomad import GnomadConnector
    from tsa.connectors.hpa import HPAConnector
    from tsa.connectors.tcga import TCGAConnector

    return [
        DepMapConnector(),
        TCGAConnector(),
        HPAConnector(),
        GnomadConnector(),
        ChEMBLConnector(),
        FaersConnector(),
    ]


def _polarity_color(polarity: Polarity) -> str:
    return {
        Polarity.FAVORABLE: FAV,
        Polarity.UNFAVORABLE: UNFAV,
        Polarity.NEUTRAL: NEUTRAL,
        Polarity.UNKNOWN: UNKNOWN,
    }[polarity]


def figure_evidence_matrix(cards: list[AuditCard], out: Path) -> Path:
    """七维证据矩阵。行是靶点，列是维度，颜色表示方向。"""
    dims = list(Dimension)
    rows = [f"{c.gene} / {CANCER_ZH.get(c.cancer, c.cancer)}" for c in cards]

    fig, ax = plt.subplots(figsize=(9.2, 5.0), dpi=150)
    for i, card in enumerate(cards):
        by_dim = card.by_dimension()
        for j, dim in enumerate(dims):
            records = by_dim[dim]
            if not records:
                color, mark = UNKNOWN, "—"
            else:
                # 同一维度可能有多条证据，取方向最明确的一条代表
                ranked = sorted(
                    records,
                    key=lambda r: (r.polarity is not Polarity.UNKNOWN, r.strength),
                    reverse=True,
                )
                top = ranked[0]
                color = _polarity_color(top.polarity)
                mark = {
                    Polarity.FAVORABLE: "＋",
                    Polarity.UNFAVORABLE: "－",
                    Polarity.NEUTRAL: "·",
                    Polarity.UNKNOWN: "—",
                }[top.polarity]
            ax.add_patch(
                plt.Rectangle((j, len(cards) - 1 - i), 0.94, 0.9, color=color, alpha=0.9)
            )
            ax.text(
                j + 0.47,
                len(cards) - 1 - i + 0.45,
                mark,
                ha="center",
                va="center",
                fontsize=12,
                color="white" if color in (FAV, UNFAV) else MUTED,
                fontweight="bold",
            )

    ax.set_xlim(-0.06, len(dims))
    ax.set_ylim(-0.06, len(cards))
    ax.set_xticks([j + 0.47 for j in range(len(dims))])
    ax.set_xticklabels([label(d) for d in dims], fontsize=10)
    ax.set_yticks([len(cards) - 1 - i + 0.45 for i in range(len(cards))])
    ax.set_yticklabels(rows, fontsize=10)
    ax.xaxis.set_ticks_position("top")
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title("七维证据矩阵：方向由系统在本维度内归一化后判定", fontsize=12, pad=34)

    handles = [
        plt.Rectangle((0, 0), 1, 1, color=FAV),
        plt.Rectangle((0, 0), 1, 1, color=UNFAV),
        plt.Rectangle((0, 0), 1, 1, color=NEUTRAL),
        plt.Rectangle((0, 0), 1, 1, color=UNKNOWN),
    ]
    ax.legend(
        handles,
        ["支持推进", "提示风险", "中性", "证据不足"],
        loc="upper center",
        bbox_to_anchor=(0.5, -0.04),
        ncol=4,
        frameon=False,
        fontsize=10,
    )
    fig.tight_layout()
    path = out / "evidence-matrix.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def figure_rule_hits(cards: list[AuditCard], out: Path) -> Path:
    """冲突规则命中矩阵。"""
    rules = [f"R{i}" for i in range(1, 8)]
    rows = [f"{c.gene} / {CANCER_ZH.get(c.cancer, c.cancer)}" for c in cards]

    fig, ax = plt.subplots(figsize=(6.6, 5.0), dpi=150)
    for i, card in enumerate(cards):
        severity = {c.rule_id: c.severity for c in card.conflicts}
        for j, rid in enumerate(rules):
            y = len(cards) - 1 - i
            if rid in severity:
                sev = severity[rid]
                ax.add_patch(
                    plt.Rectangle((j, y), 0.94, 0.9, color=UNFAV, alpha=0.25 + 0.65 * sev)
                )
                ax.text(
                    j + 0.47, y + 0.45, f"{sev:.2f}",
                    ha="center", va="center", fontsize=8.5, color="white", fontweight="bold",
                )
            else:
                ax.add_patch(plt.Rectangle((j, y), 0.94, 0.9, color=UNKNOWN))

    ax.set_xlim(-0.06, len(rules))
    ax.set_ylim(-0.06, len(cards))
    ax.set_xticks([j + 0.47 for j in range(len(rules))])
    ax.set_xticklabels(rules, fontsize=11, fontweight="bold")
    ax.set_yticks([len(cards) - 1 - i + 0.45 for i in range(len(cards))])
    ax.set_yticklabels(rows, fontsize=10)
    ax.xaxis.set_ticks_position("top")
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title("冲突规则命中情况（格内为严重度）", fontsize=12, pad=34)
    fig.tight_layout()
    path = out / "rule-hits.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def figure_cross_cancer(out: Path) -> Path | None:
    """KRAS 在不同癌种中的改变频率与机制构成。"""
    from tsa.connectors.base import ConnectorError, HttpClient
    from tsa.connectors.tcga import fetch_clinical_summary

    syndromes = ("PAAD", "COAD", "LUAD", "STAD", "BRCA")
    project = {
        "PAAD": "TCGA-PAAD", "COAD": "TCGA-COAD", "LUAD": "TCGA-LUAD",
        "STAD": "TCGA-STAD", "BRCA": "TCGA-BRCA",
    }
    client = HttpClient()
    data = []
    for code in syndromes:
        try:
            s = fetch_clinical_summary("KRAS", project[code], client)
        except ConnectorError as exc:
            print(f"  {code} 跳过：{exc}")
            continue
        n = s.n_cases or 1
        # 必须用排他贡献来堆叠。三类改变是**重叠**的集合，直接把各自占比相加
        # 会超过真实改变率（胰腺癌会算出 62.2%，而并集只有 59.5%）。
        # 排他部分之和不足并集，差额就是同时存在多种机制的病例，单独作为一段。
        counts = s.exclusive_counts
        only_mut = counts["mutation_dominant"] / n * 100
        only_del = counts["loss_dominant"] / n * 100
        only_amp = counts["amplification_dominant"] / n * 100
        multi = s.altered_fraction * 100 - (only_mut + only_del + only_amp)
        data.append(
            (
                CANCER_ZH[code],
                only_mut,
                only_del,
                only_amp,
                max(0.0, multi),
                s.altered_fraction * 100,
                n,
            )
        )
    if not data:
        print("  跨癌种图跳过：没有取到数据")
        return None

    names = [d[0] for d in data]
    mut = np.array([d[1] for d in data])
    dele = np.array([d[2] for d in data])
    amp = np.array([d[3] for d in data])
    multi = np.array([d[4] for d in data])
    totals = np.array([d[5] for d in data])
    sizes = [d[6] for d in data]

    fig, ax = plt.subplots(figsize=(7.6, 3.6), dpi=150)
    x = np.arange(len(names))
    ax.bar(x, mut, 0.55, label="仅点突变", color=ACCENT)
    ax.bar(x, dele, 0.55, bottom=mut, label="仅纯合缺失", color=UNFAV)
    ax.bar(x, amp, 0.55, bottom=mut + dele, label="仅基因扩增", color=FAV)
    ax.bar(x, multi, 0.55, bottom=mut + dele + amp, label="多种机制并存", color=NEUTRAL)
    for i, total in enumerate(totals):
        ax.text(i, total + 1.6, f"{total:.1f}%", ha="center", fontsize=9.5, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{n}\n(队列 {s} 例)" for n, s in zip(names, sizes)], fontsize=10)
    ax.set_ylabel("KRAS 改变病例占比 (%)")
    ax.set_title("同一基因在不同癌种中的改变频率与机制构成", fontsize=12)
    ax.legend(frameon=False, fontsize=9, ncol=4, loc="upper right")
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_ylim(0, totals.max() * 1.34)
    fig.tight_layout()
    path = out / "cross-cancer.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def figure_alteration_method(out: Path) -> Path:
    """只用点突变会低估多少：以 CDKN2A 为例。"""
    from tsa.connectors.base import ConnectorError, HttpClient
    from tsa.connectors.tcga import fetch_clinical_summary

    client = HttpClient()
    try:
        s = fetch_clinical_summary("CDKN2A", "TCGA-PAAD", client)
    except ConnectorError as exc:
        print(f"  CDKN2A 图跳过：{exc}")
        s = None

    if s is None:
        return None
    n = s.n_cases
    only = len(s.mutated_cases) / n * 100
    union = s.altered_fraction * 100

    fig, ax = plt.subplots(figsize=(5.4, 3.4), dpi=150)
    bars = ax.bar(
        ["仅统计点突变", "点突变 ∪ 纯合缺失 ∪ 扩增"],
        [only, union],
        color=[NEUTRAL, ACCENT],
        width=0.5,
    )
    for b, v in zip(bars, (only, union)):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.2, f"{v:.1f}%",
                ha="center", fontsize=11, fontweight="bold")
    ax.annotate(
        "",
        xy=(1, union), xytext=(0, only),
        arrowprops=dict(arrowstyle="->", color=UNFAV, lw=1.6),
    )
    ax.text(0.5, (only + union) / 2 + 3, f"低估 {(union - only):.1f} 个百分点",
            ha="center", color=UNFAV, fontsize=10, fontweight="bold")
    ax.set_ylabel(f"CDKN2A 改变病例占比（TCGA-PAAD，{n} 例）")
    ax.set_title("只看点突变会得出完全相反的结论", fontsize=12)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_ylim(0, union * 1.35)
    fig.tight_layout()
    path = out / "alteration-method.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def figure_architecture(out: Path) -> Path:
    """系统架构：六层流水线。"""
    layers = [
        ("输入", "候选靶点 + 目标癌种", ACCENT_SOFT),
        ("采集", "七个公开数据源，统一接口", "#f2f5fb"),
        ("标准化", "维度内归一化 → 方向 + 强度", "#f2f5fb"),
        ("冲突检测", "七条规则，纯函数，可单测", "#fdeceb"),
        ("解释", "受限词表内解释矛盾成因", "#fdf6ec"),
        ("汇总", "审计报告 / 静态站点 / 接口", "#e8f6ee"),
    ]
    fig, ax = plt.subplots(figsize=(6.4, 5.4), dpi=150)
    h = 0.72
    gap = 0.22
    for i, (name, desc, color) in enumerate(layers):
        y = (len(layers) - 1 - i) * (h + gap)
        ax.add_patch(
            FancyBboxPatch(
                (0.06, y), 0.88, h,
                boxstyle="round,pad=0.012,rounding_size=0.03",
                facecolor=color, edgecolor=LINE, linewidth=1,
            )
        )
        ax.text(0.12, y + h / 2 + 0.10, name, fontsize=11, fontweight="bold", color=INK, va="center")
        ax.text(0.12, y + h / 2 - 0.14, desc, fontsize=9, color=MUTED, va="center")
        if i < len(layers) - 1:
            ax.add_patch(
                FancyArrowPatch(
                    (0.5, y), (0.5, y - gap),
                    arrowstyle="-|>", mutation_scale=11, color=NEUTRAL, lw=1.2,
                )
            )
    ax.set_xlim(0, 1)
    ax.set_ylim(-0.05, len(layers) * (h + gap) + 0.15)
    ax.axis("off")
    ax.set_title("系统架构", fontsize=12, pad=6)
    fig.tight_layout()
    path = out / "architecture.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成图表")
    parser.add_argument("--out", default="docs/assets/figures")
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.ERROR)
    setup_fonts()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    connectors = build_connectors(args.mock)
    cards = []
    for gene, cancer in TARGETS:
        card = run_audit(AuditRequest(gene=gene, cancer=cancer), connectors)
        cards.append(card)
        print(f"  审计 {gene}/{cancer}：{card.verdict}")

    print()
    made = [figure_evidence_matrix(cards, out), figure_rule_hits(cards, out)]
    for optional in (figure_cross_cancer(out), figure_alteration_method(out)):
        if optional:
            made.append(optional)
    made.append(figure_architecture(out))

    print("已生成图表：")
    for p in made:
        print(f"  {p}  ({p.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
