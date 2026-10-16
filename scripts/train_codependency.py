"""在 DepMap 依赖矩阵上算依赖共变表（开发端，需要 numpy）。

    python scripts/train_codependency.py             # 训练并写出共变表
    python scripts/train_codependency.py --validate  # 训练后检验已知关系

做法：对每个基因，算它的依赖性曲线与其余所有基因的皮尔逊相关系数，
取最强的 k 个正相关与 k 个负相关伙伴。相关系数只看**同起同落**，
不解释因果，所以输出里用的是"同向／互补"这种描述，而不是"通路"。

为什么不用降维：先试过 z-score 后截断 SVD 再取余弦近邻，验证没通过
（KRAS 的近邻是随谱系走的基因，CDKN2A 的近邻是噪声）。降维会把这种
0.2 到 0.4 量级的真实相关冲淡，而直接算相关系数留得住。

计算量：18434 个基因两两相关太大，所以按行分块做矩阵乘法，
并用动态范围预筛掉没有依赖结构的基因。一次训练几分钟。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tsa.codependency import CORR_QUANT, FLAT_THRESHOLD, save as save_table  # noqa: E402
from tsa.depmapbin import MISSING, load as load_matrix  # noqa: E402

DEFAULT_OUT = ROOT / "data" / "depmap" / "codependency.bin.lzma"
BLOCK = 256  # 每次处理多少个查询基因

# 已知关系，用来检验结果是否合常识。这些先验来自文献中反复报告的共依赖/互斥现象，
# 训练脚本只用来判断"有没有信号"，不参与训练本身。
KNOWN_PAIRS = [
    ("SMARCA4", "SMARCA2", "旁系同源互斥（负数方向）"),
    ("CDK4", "CCND1", "同一复合物（正数方向）"),
    ("KRAS", "RAF1", "同一通路（正数方向）"),
    ("KRAS", "SHOC2", "同一通路（正数方向）"),
    ("CDK4", "RB1", "上下游关系（负数方向）"),
]


def load_profiles(bin_path: Path) -> tuple[np.ndarray, list[str], list[str]]:
    print(f"读取 {bin_path.name} ……")
    matrix = load_matrix(bin_path)
    raw = np.frombuffer(matrix.rows, dtype=np.uint8).reshape(matrix.shape)
    signed = raw.view(np.int8).astype(np.float32)
    values = signed * matrix.scale
    values[signed == MISSING] = np.nan
    return values.T, matrix.cell_lines, matrix.genes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="训练依赖共变表")
    parser.add_argument("--k", type=int, default=25, help="每个基因保留多少个伙伴，默认 25")
    parser.add_argument("--matrix", help="DepMap 紧凑矩阵路径")
    parser.add_argument("--out", help="输出路径")
    parser.add_argument("--validate", action="store_true", help="检验已知关系是否在结果里")
    opts = parser.parse_args(argv)

    matrix_path = (
        Path(opts.matrix) if opts.matrix else ROOT / "data" / "depmap" / "gene_effect.bin.lzma"
    )
    if not matrix_path.exists():
        print(f"找不到矩阵：{matrix_path}", file=sys.stderr)
        return 1

    started = time.time()
    profiles, cell_lines, genes = load_profiles(matrix_path)
    print(f"  矩阵 {profiles.shape[0]} 个基因 × {profiles.shape[1]} 个细胞系")

    # 每个基因的动态范围：90 分位减 10 分位要比标准差稳，不受极端值影响。
    with np.errstate(invalid="ignore"):
        dynamic = np.nanpercentile(profiles, 90, axis=1) - np.nanpercentile(profiles, 10, axis=1)
    dynamic = np.nan_to_num(dynamic)
    keep = dynamic >= FLAT_THRESHOLD
    kept_genes = [g for g, flag in zip(genes, keep) if flag]
    x = profiles[keep]
    print(f"  动态范围 ≥ {FLAT_THRESHOLD} 的基因 {len(kept_genes)} 个"
          f"（剔除 {int((~keep).sum())} 个没有依赖结构的基因）")

    # 按基因做 z-score（等价于皮尔逊相关的预处理），缺失按均值补 0。
    mean = np.nanmean(x, axis=1, keepdims=True)
    std = np.nanstd(x, axis=1, keepdims=True)
    std[std == 0] = np.nan
    z = (x - mean) / std
    z = np.nan_to_num(z).astype(np.float32)
    valid = np.sum(~np.isnan(x), axis=1)
    z[valid < 100] = 0.0
    z /= np.sqrt(max(1, z.shape[1]))

    n = len(kept_genes)
    k = min(opts.k, n - 1)
    payload = bytearray()
    name_to_local = {g: i for i, g in enumerate(kept_genes)}

    print(f"  正在计算两两相关（分块 {BLOCK}）……")
    for start in range(0, n, BLOCK):
        end = min(start + BLOCK, n)
        block = z[start:end]
        sims = block @ z.T  # 已做过 z-score 与归一化，点积即相关系数
        for row in range(end - start):
            scores = sims[row].copy()
            gene = kept_genes[start + row]
            self_index = name_to_local[gene]
            # 自己不能出现在伙伴列表里。取 k+1 个候选再剔除自己，
            # 比把自身设成 ±inf 稳妥 —— inf 一旦混进量化就会溢出。
            scores[self_index] = -np.inf
            cand = np.argpartition(-scores, k)[: k + 1]
            order_pos = np.array([i for i in cand if i != self_index][:k])
            order_pos = order_pos[np.argsort(-scores[order_pos])]
            scores[self_index] = np.inf
            cand = np.argpartition(scores, k)[: k + 1]
            order_neg = np.array([i for i in cand if i != self_index][:k])
            order_neg = order_neg[np.argsort(scores[order_neg])]
            idx_bytes = struct_pack_indices(order_pos) + struct_pack_indices(order_neg)
            corr_bytes = struct_pack_corrs(
                list(scores[order_pos]) + list(scores[order_neg])
            )
            # 记录格式：正相关索引 | 正相关系数 | 负相关索引 | 负相关系数
            payload += idx_bytes[: 4 * k]
            payload += corr_bytes[:k]
            payload += idx_bytes[4 * k :]
            payload += corr_bytes[k:]
        if (start // BLOCK) % 10 == 0:
            done = end / n
            print(f"    {done:5.1%}  用时 {time.time() - started:.0f} 秒", flush=True)

    out_path = Path(opts.out) if opts.out else DEFAULT_OUT
    save_table(
        out_path,
        genes=kept_genes,
        k=k,
        ranges=[float(v) for v in dynamic[keep]],
        payload=bytes(payload),
        meta={
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source": matrix_path.name,
            "cell_lines": len(cell_lines),
            "genes": len(kept_genes),
            # 被平坦度筛掉的基因也记下来：报告要能区分"不在 DepMap 里"
            # 和"在里面但没有依赖结构"，这两句话对使用者的含义完全不同。
            "flat": {
                g: int(round(float(v) * 100))
                for g, v, flag in zip(genes, dynamic, keep)
                if not flag
            },
            "method": "皮尔逊相关（基因级 z-score 后点积），取最强正负各 k 个",
            "note": "相关系数只表示同起同落，不表示通路归属或因果关系",
        },
    )
    print(f"  已写出 {out_path}（{out_path.stat().st_size / 1e6:.1f} MB，"
          f"{len(kept_genes)} 个基因 × 各 {k} 个伙伴）")
    print(f"  总用时 {time.time() - started:.0f} 秒")

    if opts.validate:
        from tsa.codependency import load as load_table

        table = load_table(out_path)
        assert table is not None
        print("\n已知关系检验（先验与训练数据无关）：")
        for pair, other, note in KNOWN_PAIRS:
            if not table.has(pair):
                print(f"  {pair}：曲线平坦，已排除，无法检验")
                continue
            positive, negative = table.partners(pair)
            names_pos = [g for g, _ in positive]
            names_neg = [g for g, _ in negative]
            where = "同向伙伴" if other in names_pos else ("互补伙伴" if other in names_neg else "未进入前 25")
            rank = (
                names_pos.index(other) + 1 if other in names_pos
                else (names_neg.index(other) + 1 if other in names_neg else None)
            )
            corr = None
            for g, c in positive + negative:
                if g == other:
                    corr = c
            print(f"  {pair} / {other}：r={corr:+.2f}，位于{where}"
                  + (f"第 {rank} 位" if rank else "") + f"（{note}）")
    return 0


def struct_pack_indices(values) -> bytes:
    import struct as _struct

    return _struct.pack(f"<{len(values)}i", *[int(v) for v in values])


def struct_pack_corrs(values) -> bytes:
    import struct as _struct

    return _struct.pack(f"<{len(values)}b", *[int(np.clip(round(v * CORR_QUANT), -128, 127)) for v in values])


if __name__ == "__main__":
    raise SystemExit(main())
