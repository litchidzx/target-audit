"""命令行入口。

    python run.py demo --cancer LUAD                     # 离线跑通整条管线
    python run.py audit KRAS --cancer LUAD --mock        # 单个靶点，用假数据
    python run.py audit KRAS --cancer TCGA-LUAD          # 单个靶点，接真实数据源
    python run.py audit KRAS --cancer LUAD --llm stub    # 带解释层
    python run.py doctor                                 # 检查运行环境
    python run.py self-test                              # 跑单元测试

解释层不绑定任何模型服务商。用 --llm 指定 provider 名，或设置环境变量：

    TSA_LLM_PROVIDER=deepseek  TSA_LLM_MODEL=deepseek-chat  TSA_LLM_API_KEY=sk-...
    TSA_LLM_PROVIDER=ollama    TSA_LLM_MODEL=qwen3:8b

可选 provider 见 tsa/llm/registry.py 的 PROVIDER_PRESETS。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tsa.engine import AuditRequest, run_audit  # noqa: E402
from tsa.llm.base import LLMConfig, LLMError  # noqa: E402
from tsa.llm.registry import PROVIDER_PRESETS, build_provider  # noqa: E402
from tsa.mock import mock_connector  # noqa: E402
from tsa.report import render_markdown  # noqa: E402


def build_connectors(args):
    if args.mock:
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


def build_llm(args):
    """按命令行参数与环境变量构造模型 provider。未指定则不启用解释层。"""
    config = LLMConfig.from_env()
    if getattr(args, "llm", None):
        config.provider = args.llm
    if getattr(args, "model", None):
        config.model = args.model
    if config.provider == "stub" and not getattr(args, "llm", None):
        return None
    return build_provider(config)


def cmd_audit(args):
    try:
        llm = build_llm(args)
    except LLMError as exc:
        print(f"解释层未启用：{exc}")
        llm = None
    card = run_audit(
        AuditRequest(gene=args.gene, cancer=args.cancer, variant=args.variant),
        connectors=build_connectors(args),
        llm=llm,
        include_variants=not args.no_variants,
    )
    text = render_markdown(card)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已写入 {args.out}")
    else:
        print(text)
    return 0


def cmd_demo(args):
    for gene in ("KRAS", "SELECTIVE_EXAMPLE"):
        card = run_audit(
            AuditRequest(gene=gene, cancer=args.cancer), connectors=[mock_connector()]
        )
        print(render_markdown(card))
        print("\n" + "=" * 72 + "\n")
    return 0


def cmd_self_test(args):
    import unittest

    suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def cmd_doctor(args):
    """检查运行环境：哪些能力现在可用，缺什么、怎么补。

    这个命令的存在，是为了让"在别人电脑上能不能跑"变成一个能被回答的问题。
    """
    import platform
    import urllib.error
    import urllib.request

    from tsa.connectors.depmap import GENE_EFFECT_PATTERNS, MODEL_PATTERNS, resolve_data_file
    from tsa.connectors.gnomad import CONSTRAINT_FILE, CONSTRAINT_URL

    ok, optional, missing = "  [就绪] ", "  [可选] ", "  [缺失] "
    problems = 0

    print("靶点尽调 · 运行环境检查")
    print()

    if sys.version_info >= (3, 10):
        print(f"{ok} Python {platform.python_version()}")
    else:
        print(f"{missing} Python {platform.python_version()} —— 需要 3.10 或更高")
        problems += 1

    print(f"{ok} 引擎与冲突规则（无第三方依赖，可离线运行）")

    if CONSTRAINT_FILE.exists():
        print(f"{ok} gnomAD 约束表（{CONSTRAINT_FILE.stat().st_size / 1e6:.1f} MB）")
    else:
        print(f"{optional} gnomAD 约束表未就绪 —— 首次运行时自动下载（约 4.6MB）")
        print(f"         也可手动获取：{CONSTRAINT_URL}")

    effect_file = resolve_data_file(GENE_EFFECT_PATTERNS)
    model_file = resolve_data_file(MODEL_PATTERNS)
    if effect_file and model_file:
        print(f"{ok} DepMap 参考数据（{effect_file.name}，{effect_file.stat().st_size / 1e6:.0f} MB）")
    else:
        print(f"{optional} DepMap 参考数据未就绪 —— 肿瘤依赖性与跨癌种选择性将标记为证据不足")
        print("         下载 https://depmap.org/portal/data_page/?tab=allData")
        print("         需要 CRISPRGeneEffect.csv 与 Model.csv，放到 data/depmap/")

    print()
    print("在线数据源连通性（四个维度依赖它们）：")
    endpoints = (
        ("TCGA / GDC", "https://api.gdc.cancer.gov/status"),
        (
            "HPA",
            "https://www.proteinatlas.org/api/search_download.php"
            "?search=TP53&format=json&columns=g&compress=no",
        ),
        (
            "UniProt（ChEMBL 靶点解析用）",
            "https://rest.uniprot.org/uniprotkb/search?query=gene_exact:TP53&format=json&size=1",
        ),
        (
            "openFDA",
            'https://api.fda.gov/drug/event.json?search=patient.drug.medicinalproduct:"ASPIRIN"&limit=1',
        ),
    )
    reachable = 0
    for name, url in endpoints:
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "target-selectivity-audit/0.1"}
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.status < 400:
                    reachable += 1
                    print(f"{ok} {name}")
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                reachable += 1  # 4xx 说明服务是活的，只是这个探测请求不合它意
                print(f"{ok} {name}")
            else:
                print(f"{missing} {name}（HTTP {exc.code}）")
        except Exception as exc:  # noqa: BLE001
            print(f"{missing} {name}（{type(exc).__name__}）")

    print()
    if reachable == len(endpoints):
        print("结论：七个维度全部可用。")
    elif reachable:
        print(f"结论：{reachable}/{len(endpoints)} 个在线数据源可达，")
        print("      不可达的维度会被标记为证据不足，不影响其余部分。")
    else:
        print("结论：当前无网络。仍可用的是引擎、冲突规则、解释层兜底，")
        print("      以及 docs/index.html 里已经算好的结果集。")
    if problems:
        print()
        print(f"有 {problems} 项需要处理。")
    return 0 if problems == 0 else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tsa", description="靶点选择性审计")
    sub = parser.add_subparsers(dest="command", required=True)

    p_audit = sub.add_parser("audit", help="审计一个靶点")
    p_audit.add_argument("gene")
    p_audit.add_argument(
        "--cancer",
        required=True,
        help="目标癌种，接受 TCGA 项目编号（如 TCGA-LUAD）或已收录缩写（如 PAAD、LUAD）",
    )
    p_audit.add_argument("--mock", action="store_true", help="用离线假数据")
    p_audit.add_argument("--out", help="输出 Markdown 报告到文件")
    p_audit.add_argument(
        "--llm",
        help=f"启用解释层并指定 provider：stub, openai_compat, {', '.join(sorted(PROVIDER_PRESETS))}",
    )
    p_audit.add_argument("--model", help="模型名称，覆盖环境变量 TSA_LLM_MODEL")
    p_audit.add_argument("--variant", help="只审计某个亚型，例如 G12D")
    p_audit.add_argument("--no-variants", action="store_true", help="跳过亚型分析（更快）")
    p_audit.set_defaults(func=cmd_audit)

    p_demo = sub.add_parser("demo", help="离线跑通整条管线")
    p_demo.add_argument("--cancer", required=True, help="目标癌种")
    p_demo.set_defaults(func=cmd_demo)

    p_test = sub.add_parser("self-test", help="跑单元测试")
    p_test.set_defaults(func=cmd_self_test)

    p_doc = sub.add_parser("doctor", help="检查运行环境")
    p_doc.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
