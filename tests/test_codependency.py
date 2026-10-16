"""依赖共变表。

两层验证：用合成表测读写与边界（不需要真实数据）；用仓库里那份训练好的表
做回归检验 —— 已知关系必须还在里面，否则说明训练流程或量化出了问题。
"""

from __future__ import annotations

import sys
import shutil
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tsa.codependency import (  # noqa: E402
    CORR_QUANT,
    FLAT_THRESHOLD,
    load,
    resolve_path,
    save,
    summarize,
)

TMP_ROOT = Path(__file__).resolve().parent / ".tmp"


def make_workdir() -> Path:
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    for _ in range(100):
        candidate = TMP_ROOT / ("codep_" + uuid.uuid4().hex[:12])
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise RuntimeError("无法创建临时目录")


def write_table(
    path: Path,
    *,
    genes: list[str],
    k: int,
    ranges: list[float],
    records: list[tuple[list[int], list[float], list[int], list[float]]],
) -> None:
    import struct

    payload = bytearray()
    for pos_idx, pos_corr, neg_idx, neg_corr in records:
        payload += struct.pack(f"<{k}i", *pos_idx)
        payload += struct.pack(f"<{k}b", *[int(round(c * CORR_QUANT)) for c in pos_corr])
        payload += struct.pack(f"<{k}i", *neg_idx)
        payload += struct.pack(f"<{k}b", *[int(round(c * CORR_QUANT)) for c in neg_corr])
    save(path, genes=genes, k=k, ranges=ranges, payload=bytes(payload), meta={"generated_at": "test"})


class TestTableRoundTrip(unittest.TestCase):
    def setUp(self):
        self.dir = make_workdir()
        self.path = self.dir / "codependency.bin.lzma"

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_partners_round_trip(self):
        # 三个基因，k=1：A↔B 同向，A 的互补伙伴是 C
        write_table(
            self.path,
            genes=["AAA", "BBB", "CCC"],
            k=1,
            ranges=[0.6, 0.6, 0.6],
            records=[
                ([1], [0.42], [2], [-0.31]),
                ([0], [0.42], [2], [-0.05]),
                ([1], [0.05], [0], [-0.31]),
            ],
        )
        table = load(self.path)
        assert table is not None
        positive, negative = table.partners("AAA")
        self.assertEqual(positive, [("BBB", 0.42)])
        self.assertEqual(negative, [("CCC", -0.31)])

    def test_flat_gene_gets_no_lists(self):
        write_table(
            self.path,
            genes=["FLAT", "OTHER"],
            k=1,
            ranges=[FLAT_THRESHOLD - 0.05, 0.6],
            records=[([1], [0.9], [1], [-0.9]), ([0], [0.9], [0], [-0.9])],
        )
        table = load(self.path)
        assert table is not None
        self.assertTrue(table.is_flat("FLAT"))
        self.assertEqual(table.partners("FLAT"), ([], []))

    def test_unknown_gene_is_not_available(self):
        write_table(
            self.path,
            genes=["AAA"],
            k=1,
            ranges=[0.6],
            records=[([0], [0.0], [0], [0.0])],
        )
        brief = summarize("NOT_A_GENE", path=self.path)
        self.assertFalse(brief.available)
        self.assertIn("不在 DepMap", brief.note)

    def test_missing_file_degrades_quietly(self):
        brief = summarize("KRAS", path=self.dir / "不存在.bin.lzma")
        self.assertFalse(brief.available)
        self.assertIn("未就绪", brief.note)


class TestShippedTable(unittest.TestCase):
    """回归检验：仓库里那份表必须还能重现已知关系。

    这条测试的价值在于盯住训练流程 —— 换了方法、改了标准化或量化，
    已知关系一旦掉出前列就说明出问题了（第一版 SVD 方案就是这样被否掉的）。
    """

    def setUp(self):
        path = resolve_path()
        if not path.exists():
            self.skipTest("仓库里没有共变表，跳过（用 scripts/train_codependency.py 生成）")
        self.table = load(path)
        assert self.table is not None

    def assert_in_top(self, gene: str, partner: str, *, side: str, within: int):
        positive, negative = self.table.partners(gene)
        names = [g for g, _ in (positive if side == "同向" else negative)]
        self.assertIn(partner, names[:within], f"{gene} 的{side}伙伴里前 {within} 名没有 {partner}")

    def test_paralog_mutual_exclusivity(self):
        self.assert_in_top("SMARCA4", "SMARCA2", side="互补", within=5)

    def test_same_complex_partners(self):
        self.assert_in_top("CDK4", "CCND1", side="同向", within=5)

    def test_pathway_partners_survive(self):
        self.assert_in_top("KRAS", "RAF1", side="同向", within=10)

    def test_swi_snf_subunits_find_each_other(self):
        # SWI/SNF 的各个亚基应当互为同向伙伴，而 SMARCA2 是与 SMARCA4 互补的旁系同源。
        for subunit in ("SMARCB1", "SMARCD1", "SMARCC1"):
            with self.subTest(subunit=subunit):
                self.assert_in_top("SMARCA4", subunit, side="同向", within=10)

    def test_receptor_adaptors_find_each_other(self):
        # EGFR 的经典下游接头蛋白
        for adaptor in ("SHC1", "GRB2"):
            with self.subTest(adaptor=adaptor):
                self.assert_in_top("EGFR", adaptor, side="同向", within=10)

    def test_flat_genes_are_recorded_and_carry_no_partners(self):
        flat = self.table.meta.get("flat") or {}
        self.assertTrue(flat, "训练产物里应当保留被判为平坦的基因及其动态范围")
        gene = next(iter(flat))
        self.assertTrue(self.table.is_flat(gene))
        self.assertEqual(self.table.partners(gene), ([], []))
        brief = summarize(gene)
        self.assertTrue(brief.is_flat)
        self.assertIn("没有差异", brief.note)


if __name__ == "__main__":
    unittest.main()
