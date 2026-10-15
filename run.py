"""命令行入口。

    python run.py demo --cancer LUAD                     # 离线跑通整条管线
    python run.py audit KRAS --cancer LUAD --mock        # 单个靶点，用假数据
    python run.py audit KRAS --cancer TCGA-LUAD          # 单个靶点，接真实数据源
    python run.py audit KRAS --cancer LUAD --llm stub    # 带解释层
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
        AuditRequest(gene=args.gene, cancer=args.cancer),
        connectors=build_connectors(args),
        llm=llm,
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
    p_audit.set_defaults(func=cmd_audit)

    p_demo = sub.add_parser("demo", help="离线跑通整条管线")
    p_demo.add_argument("--cancer", required=True, help="目标癌种")
    p_demo.set_defaults(func=cmd_demo)

    p_test = sub.add_parser("self-test", help="跑单元测试")
    p_test.set_defaults(func=cmd_self_test)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
