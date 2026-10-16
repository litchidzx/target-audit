"""跑一批审计，把结果渲染成可离线打开的工作台。

用法：
    # 用真实数据（需要网络，首次较慢，之后走缓存）
    python scripts/build_site.py --out docs

    # 离线跑通（不需要任何凭据）
    python scripts/build_site.py --out docs --mock

产出可直接托管，也可下载后双击 docs/index.html 使用。审计结果在构建时算好并固化，
所以演示现场不依赖网络，也不会因为外部接口抽风而失败。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tsa.engine import AuditRequest, run_audit  # noqa: E402
from tsa.mock import mock_connector  # noqa: E402
from tsa.schema import Dimension  # noqa: E402
from tsa.webexport import write_site  # noqa: E402

# 演示靶点集合。刻意覆盖多个癌种和多种结论类型，让工作台看起来像一个真正的
# 靶点库而不是几个例子：既有治疗窗告警的、也有缺失主导的、有成药路径明确的、
# 有表达与依赖脱节的，也有完全没检出矛盾的对照。
DEFAULT_TARGETS: tuple[tuple[str, str], ...] = (
    # 胰腺癌
    ("KRAS", "PAAD"),
    ("TP53", "PAAD"),
    ("SMAD4", "PAAD"),
    ("CDKN2A", "PAAD"),
    ("ARID1A", "PAAD"),
    ("TGFBR2", "PAAD"),
    # 肺腺癌
    ("KRAS", "LUAD"),
    ("EGFR", "LUAD"),
    ("TP53", "LUAD"),
    ("STK11", "LUAD"),
    ("KEAP1", "LUAD"),
    ("BRAF", "LUAD"),
    # 结肠癌
    ("APC", "COAD"),
    ("KRAS", "COAD"),
    ("TP53", "COAD"),
    ("SMAD4", "COAD"),
    ("PIK3CA", "COAD"),
    ("BRAF", "COAD"),
    # 乳腺癌
    ("BRCA1", "BRCA"),
    ("BRCA2", "BRCA"),
    ("PIK3CA", "BRCA"),
    ("TP53", "BRCA"),
    ("ERBB2", "BRCA"),
    ("CDH1", "BRCA"),
    # 胃癌
    ("TP53", "STAD"),
    ("KRAS", "STAD"),
    ("ARID1A", "STAD"),
    ("PIK3CA", "STAD"),
    ("ERBB2", "STAD"),
    # 肝癌
    ("TP53", "LIHC"),
    ("CTNNB1", "LIHC"),
    ("TERT", "LIHC"),
    # 卵巢癌
    ("BRCA1", "OV"),
    ("BRCA2", "OV"),
    ("BRCA2", "PAAD"),
)


def existing_coverage(out_dir: str) -> dict[str, int]:
    """读上一版站点里每个靶点的维度覆盖数，用来发现"这次构建反而更差"。

    为什么需要这个守卫：离线重放（TSA_OFFLINE=1）时，只要某个数据源的响应
    没进缓存，那个维度就会静默变成"缺失"。实测遇到过一次：胰腺癌的临床维度
    整批没命中缓存，七个靶点一起丢掉 R3／R4／R7，规则覆盖从 7/7 掉到 6/7 ——
    而构建脚本照常写出，站点看起来一切正常。这种退化必须吵出来。
    """

    data_file = Path(out_dir) / "data.js"
    if not data_file.exists():
        return {}
    try:
        text = data_file.read_text(encoding="utf-8")
        payload = json.loads(text[text.find("=") + 1 :].strip().rstrip(";"))
    except (OSError, ValueError):
        return {}
    total = len(list(Dimension))
    out: dict[str, int] = {}
    for card in payload.get("targets", []):
        key = f"{card.get('gene')}/{card.get('cancer')}"
        out[key] = total - len(card.get("missing") or [])
    return out


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
    parser.add_argument(
        "--allow-degraded",
        action="store_true",
        help="即使本次构建的维度覆盖低于上一版也照常写出。默认拒绝，见下方守卫说明。",
    )
    parser.add_argument(
        "--min-coverage",
        type=int,
        default=5,
        help="只收录证据覆盖达到该维度数的靶点（默认 5；设为 0 则全部收录）",
    )
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
    failed = []
    thin = []
    for gene, cancer in targets:
        try:
            card = run_audit(AuditRequest(gene=gene, cancer=cancer), connectors, llm=llm)
        except Exception as exc:  # noqa: BLE001 - 单个靶点失败不该中断整批构建
            failed.append((gene, cancer, str(exc)[:80]))
            print(f"  {gene:<8}{cancer:<6} 跳过：{exc}")
            continue
        covered = len(list(Dimension)) - len(card.missing_dimensions)
        if covered < args.min_coverage:
            thin.append((gene, cancer, covered))
            print(f"  {gene:<8}{cancer:<6} 覆盖 {covered}/7，暂不收录（数据源本轮不可用）")
            continue
        cards.append(card)
        rules = ",".join(c.rule_id for c in card.conflicts) or "-"
        print(f"  {gene:<8}{cancer:<6} {card.verdict:<28} {rules}")

    # 与上一版比对：覆盖下降说明这一轮有数据源没拿到，写出去会静默降级站点。
    total_dims = len(list(Dimension))
    before = existing_coverage(args.out)
    degraded = []
    for card in cards:
        key = f"{card.gene}/{card.cancer}"
        now = total_dims - len(card.missing_dimensions)
        was = before.get(key)
        if was is not None and now < was:
            lost = "、".join(d.value for d in card.missing_dimensions)
            degraded.append((key, was, now, lost))
    if degraded and not args.allow_degraded:
        print()
        # 提示符号只用 GBK 能编码的字符：Windows 控制台默认代码页是 GBK，
        # 写个 emoji 出来会让脚本在报告问题时自己崩掉。
        print(f"[注意] 本次构建有 {len(degraded)} 个靶点的维度覆盖低于上一版，已停止写出：")
        for key, was, now, lost in degraded:
            print(f"    {key}：{was}/7 -> {now}/7，缺失维度 {lost}")
        print("  多半是离线重放没命中缓存。联网重跑即可补齐；")
        print("  确有需要照常写出时加 --allow-degraded。")
        return 1

    written = write_site(cards, args.out)
    print()
    print(f"收录 {len(cards)} 个靶点")
    if thin:
        print(f"因证据不足暂未收录 {len(thin)} 个：" +
              "、".join(f"{g}/{c}({n}/7)" for g, c, n in thin))
        print("  这些靶点的失败请求没有进缓存，换个时间重跑会自动补齐。")
    if failed:
        print(f"出错 {len(failed)} 个：" + "、".join(f"{g}/{c}" for g, c, _ in failed))
    print(f"已生成 {len(written)} 个文件到 {args.out}/")
    print(f"打开 {(Path(args.out) / 'index.html').resolve()} 查看")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
