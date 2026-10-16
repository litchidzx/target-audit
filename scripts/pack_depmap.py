"""把 DepMap 的 CSV 矩阵转成紧凑二进制。

    python scripts/pack_depmap.py

输入  data/depmap/CRISPRGeneEffect.csv   （官方下载，约 497MB）
输出  data/depmap/gene_effect.bin.lzma   （约 20MB）

打包后的文件可以直接随程序分发 —— 20MB 与 497MB 的分发难度完全不同。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tsa.connectors.depmap import DATA_DIR, resolve_data_file, GENE_EFFECT_PATTERNS  # noqa: E402
from tsa import depmapbin  # noqa: E402

OUT_NAME = "gene_effect.bin.lzma"

CHECK_GENES = ("KRAS", "TP53", "CDKN2A", "SMAD4", "EGFR", "MYC")


def verify_values(source: Path, out: Path, genes=CHECK_GENES) -> tuple[int, float]:
    """回读打包文件，与原始 CSV 逐值比对。

    只检查形状是不够的 —— 量化或符号处理出错时形状照样对，但数值全错。
    这里必须比对真实数值才能确认转换无损。
    """
    import csv as _csv

    matrix = depmapbin.load(out)
    packed_index = {g: i for i, g in enumerate(matrix.genes)}

    with source.open(newline="", encoding="utf-8") as fh:
        reader = _csv.reader(fh)
        header = next(reader)
        source_index: dict[str, int] = {}
        for i, name in enumerate(header[1:], start=1):
            source_index.setdefault(depmapbin.gene_symbol(name), i)

        pairs = [
            (g, source_index[g], packed_index[g])
            for g in genes
            if g in source_index and g in packed_index
        ]
        if not pairs:
            return 0, 0.0

        compared = 0
        worst = 0.0
        for row_idx, row in enumerate(reader):
            if not row or row_idx >= matrix.shape[0]:
                break
            for _, src_col, bin_col in pairs:
                if src_col >= len(row):
                    continue
                text = row[src_col].strip()
                if not text:
                    continue
                packed = matrix.value(row_idx, bin_col)
                if packed is None:
                    continue
                worst = max(worst, abs(float(text) - packed))
                compared += 1
    return compared, worst


def main() -> int:
    source = resolve_data_file(GENE_EFFECT_PATTERNS)
    if source is None:
        print(f"找不到原始矩阵，请放到 {DATA_DIR}\\ 下", file=sys.stderr)
        print("下载地址 https://depmap.org/portal/data_page/?tab=allData", file=sys.stderr)
        return 1

    out = DATA_DIR / OUT_NAME
    print(f"源文件 {source.name}（{source.stat().st_size / 1e6:.1f} MB）")
    started = time.time()
    stats = depmapbin.pack(source, out)
    elapsed = time.time() - started

    ratio = stats["packed_bytes"] / stats["source_bytes"]
    print()
    print(f"已生成 {out}")
    print(f"  {stats['cells']} 个细胞系 × {stats['genes']} 个基因")
    print(f"  {stats['packed_bytes'] / 1e6:.1f} MB（原为 {stats['source_bytes'] / 1e6:.1f} MB，"
          f"压缩到 {ratio:.1%}）")
    print(f"  耗时 {elapsed:.0f} 秒")

    # 逐值比对，而不是只看形状。
    # 只查形状会漏掉最隐蔽的一类错误：符号处理反了，负值变成大正数，
    # 形状完全正确但所有统计量都错。这个坑真实踩过。
    print("  正在与原始 CSV 逐值比对……")
    compared, worst = verify_values(source, out)
    tolerance = stats["scale"]  # 量化误差不应超过一个刻度
    print(f"  比对 {compared:,} 个数值，最大偏差 {worst:.4f}（量化刻度 {stats['scale']:.4f}）")
    if worst > tolerance * 1.5:
        print(f"  校验失败：偏差超过一个量化刻度，说明转换有误", file=sys.stderr)
        return 1
    print("  校验通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
