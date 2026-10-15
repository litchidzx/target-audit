"""跑一批审计，把结果渲染成静态站点。

用法：
    # 用真实数据（需要网络，首次较慢，之后走缓存）
    python scripts/build_site.py --out docs

    # 离线跑通（不需要任何凭据）
    python scripts/build_site.py --out docs --mock

产出是一组纯静态文件，直接托管即可。审计结果在构建时算好并固化，
所以演示现场不依赖网络，也不会因为外部接口抽风而失败。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tsa.engine import AuditRequest, run_audit  # noqa: E402
from tsa.mock import mock_connector  # noqa: E402
from tsa.webexport import write_site  # noqa: E402

# 演示靶点。刻意覆盖不同癌种和不同结论类型，让首页能体现系统的区分度：
# 有治疗窗告警的、有缺失主导的、有成药路径明确的、有表达与依赖脱节的。
DEFAULT_TARGETS: tuple[tuple[str, str], ...] = (
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


def build_connectors(mock: bool):
    if mock:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成靶点尽调静态站点")
    parser.add_argument("--out", default="docs", help="输出目录")
    parser.add_argument("--mock", action="store_true", help="用离线假数据")
    parser.add_argument(
        "--targets",
        help="靶点列表，形如 KRAS:PAAD,EGFR:LUAD；默认使用内置演示集合",
    )
    parser.add_argument("--llm", help="启用解释层并指定 provider（默认不启用）")
    parser.add_argument("--model", help="模型名称")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    llm = None
    if args.llm:
        from tsa.llm.base import LLMConfig, LLMError
        from tsa.llm.registry import build_provider

        config = LLMConfig.from_env()
        config.provider = args.llm
        if args.model:
            config.model = args.model
        try:
            llm = build_provider(config)
            print(f"解释层已启用：{args.llm}")
        except LLMError as exc:
            print(f"解释层未启用：{exc}")

    if args.targets:
        pairs = []
        for item in args.targets.split(","):
            gene, _, cancer = item.strip().partition(":")
            if gene and cancer:
                pairs.append((gene.upper(), cancer.upper()))
        targets = tuple(pairs)
    else:
        targets = DEFAULT_TARGETS

    connectors = build_connectors(args.mock)
    cards = []
    for gene, cancer in targets:
        card = run_audit(AuditRequest(gene=gene, cancer=cancer), connectors, llm=llm)
        cards.append(card)
        rules = ",".join(c.rule_id for c in card.conflicts) or "-"
        print(f"  {gene:<8}{cancer:<6} {card.verdict:<28} {rules}")

    written = write_site(cards, args.out)
    print()
    print(f"已生成 {len(written)} 个文件到 {args.out}/")
    print(f"打开 {(Path(args.out) / 'index.html').resolve()} 查看")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
