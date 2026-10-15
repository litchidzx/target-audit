"""跨癌种比较：同一个基因在不同癌种队列中是什么样。

这个脚本的存在本身就是系统通用性的证据 —— 换癌种不需要改任何代码，
只需要换一个参数。它同时也是真实的研究用法：同一个基因在不同瘤种里
的角色经常完全不同，而多数工具只给出单一疾病的视图。

用法：
    python scripts/cross_cancer.py KRAS PAAD LUAD COAD STAD
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tsa.connectors.base import ConnectorError, HttpClient  # noqa: E402
from tsa.connectors.tcga import (  # noqa: E402
    build_reference_distribution,
    fetch_clinical_summary,
    resolve_project,
    summarize,
)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2

    gene = argv[0]
    cancers = argv[1:]
    client = HttpClient()

    print(f"基因：{gene}")
    print()
    print(f"{'癌种':<8}{'项目':<14}{'队列':>5}{'改变':>9}{'分位':>7}{'突变':>7}{'缺失':>7}{'扩增':>7}  机制")
    print("-" * 82)

    started = time.time()
    for cancer in cancers:
        try:
            project = resolve_project(cancer)
        except ConnectorError as exc:
            print(f"{cancer:<8}跳过：{exc}")
            continue
        try:
            summary = fetch_clinical_summary(gene, project, client)
            reference = build_reference_distribution(project, client, exclude=gene)
        except ConnectorError as exc:
            print(f"{cancer:<8}{project:<14}跳过：{exc}")
            continue

        freq = summarize(summary, reference)[0]
        raw = freq.raw
        pct = f"{freq.percentile:.2f}" if freq.percentile is not None else "  — "
        mechanism = {
            "loss_dominant": "缺失主导",
            "amplification_dominant": "扩增主导",
            "mutation_dominant": "突变主导",
        }[summary.alteration_mode]
        print(
            f"{cancer:<8}{project:<14}{raw['n_cases']:>5}"
            f"{freq.value:>9.1%}{pct:>7}"
            f"{raw['n_mutated']:>7}{raw['n_deep_deleted']:>7}{raw['n_amplified']:>7}"
            f"  {mechanism}"
        )

    print("-" * 82)
    print(f"耗时 {time.time() - started:.0f}s（队列内参考分布首次构建较慢，之后走缓存）")
    print("分位是相对各自队列的癌基因面板计算的，所以跨癌种可直接比较。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
