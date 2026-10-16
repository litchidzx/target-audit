"""DepMap 基因效应矩阵的紧凑二进制格式。

官方发布的是 CSV，497MB（1361 个细胞系 × 18434 个基因）。
这个体积不适合放进下载包，也不适合放进容器镜像。

但基因效应值的范围很窄，实际落在 -3 到 +1 之间，而我们需要它做什么？
算谱系内的中位数、算基因之间的分位。这些统计量对 0.02 的精度完全不敏感。
所以把它量化成 int8，体积降到 1/20，统计结论不受影响。

文件格式（先 lzma 压缩，整体存成一个 .bin.lzma）：

    [4 字节]  头部长度，小端 uint32
    [头部]    JSON，含细胞系 ID、基因名、量化尺度，UTF-8
    [矩阵]    int8，行优先，形状 细胞系数 × 基因数

真值 = int8 值 × scale。
"""

from __future__ import annotations

import csv
import json
import lzma
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

MAGIC = b"DMGE"  # DepMap Gene Effect
HEADER_STRUCT = struct.Struct("<4sI")

# 缺失值的哨兵。量化后的有效值落在 -127..127，所以 -128 空出来做标记。
MISSING = -128
MISSING_BYTE = MISSING & 0xFF


def to_signed(byte_value: int) -> int:
    """把存进去的无符号字节还原成有符号整数。

    这一步漏掉会得到一个非常隐蔽的错误：负值全部变成接近 255 的大正数，
    形状校验能通过，但所有的中位数和分位全错。
    """
    return byte_value - 256 if byte_value > 127 else byte_value


@dataclass
class DepMapMatrix:
    cell_lines: list[str]
    genes: list[str]
    scale: float
    rows: bytes  # 长度 = len(cell_lines) * len(genes)
    shape: tuple[int, int]

    def value(self, row: int, col: int) -> float | None:
        raw = self.raw_at(row, col)
        return None if raw == MISSING else raw * self.scale

    def raw_at(self, row: int, col: int) -> int:
        return to_signed(self.rows[row * self.shape[1] + col])

    def column(self, gene: str) -> list[tuple[str, float]]:
        """取某个基因在各细胞系上的取值。基因不存在时返回空列表。"""
        try:
            col = self.genes.index(gene)
        except ValueError:
            return []
        n_cols = self.shape[1]
        out: list[tuple[str, float]] = []
        for i in range(self.shape[0]):
            raw = to_signed(self.rows[i * n_cols + col])
            if raw != MISSING:
                out.append((self.cell_lines[i], raw * self.scale))
        return out


def gene_symbol(column_name: str) -> str:
    """官方列名形如 'KRAS (3845)'，取基因符号部分。"""
    return column_name.split(" ")[0].strip().upper()


def pack(csv_path: Path, out_path: Path, *, progress: bool = True) -> dict[str, Any]:
    """把 CSV 矩阵转成紧凑二进制。返回统计信息。"""
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        genes = [gene_symbol(c) for c in header[1:]]

        cell_lines: list[str] = []
        raw_values: list[list[float]] = []
        for row in reader:
            if not row:
                continue
            cell_lines.append(row[0])
            raw_values.append([None if not x.strip() else float(x) for x in row[1:]])

    n_rows = len(raw_values)
    n_cols = len(genes)

    present = [abs(v) for r in raw_values for v in r if v is not None]
    peak = max(present, default=1.0)
    scale = peak / 127.0
    if progress:
        missing = sum(1 for r in raw_values for v in r if v is None)
        total = sum(len(r) for r in raw_values)
        print(f"  峰值 {peak:.3f}，量化尺度 {scale:.6f}（精度约 {scale:.4f}）")
        print(f"  缺失值 {missing} 个（{missing / max(total, 1):.2%}）")

    buf = bytearray(n_rows * n_cols)
    inv = 1.0 / scale
    for i, row in enumerate(raw_values):
        base = i * n_cols
        for j, v in enumerate(row):
            if v is None:
                buf[base + j] = MISSING_BYTE
            else:
                buf[base + j] = int(v * inv) & 0xFF  # 有符号转无符号存

    meta = {
        "format": "depmap-gene-effect-int8",
        "cell_lines": cell_lines,
        "genes": genes,
        "scale": scale,
        "shape": [n_rows, n_cols],
    }
    header_bytes = json.dumps(meta, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    payload = HEADER_STRUCT.pack(MAGIC, len(header_bytes)) + header_bytes + bytes(buf)
    if progress:
        print(f"  未压缩 {len(payload) / 1e6:.1f} MB，正在压缩……")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with lzma.open(out_path, "wb", preset=6) as fh:
        fh.write(payload)

    return {
        "cells": n_rows,
        "genes": n_cols,
        "scale": scale,
        "packed_bytes": out_path.stat().st_size,
        "source_bytes": csv_path.stat().st_size,
    }


def load(path: Path) -> DepMapMatrix:
    with lzma.open(path, "rb") as fh:
        magic, header_len = HEADER_STRUCT.unpack(fh.read(HEADER_STRUCT.size))
        if magic != MAGIC:
            raise ValueError(f"{path} 不是本格式的矩阵文件")
        meta = json.loads(fh.read(header_len).decode("utf-8"))
        rows = fh.read()

    n_rows, n_cols = meta["shape"]
    if len(rows) != n_rows * n_cols:
        raise ValueError(
            f"矩阵长度不符：期望 {n_rows * n_cols} 字节，实际 {len(rows)}"
        )
    return DepMapMatrix(
        cell_lines=meta["cell_lines"],
        genes=meta["genes"],
        scale=float(meta["scale"]),
        rows=rows,
        shape=(n_rows, n_cols),
    )


def load_index(path: Path) -> dict[str, Any]:
    """只读头部，不解压整个矩阵。用于快速检查文件是否可用。"""
    with lzma.open(path, "rb") as fh:
        magic, header_len = HEADER_STRUCT.unpack(fh.read(HEADER_STRUCT.size))
        if magic != MAGIC:
            raise ValueError(f"{path} 不是本格式的矩阵文件")
        return json.loads(fh.read(header_len).decode("utf-8"))


def iter_gene_effect_rows(matrix: DepMapMatrix) -> Iterator[tuple[str, dict[str, float]]]:
    """适配旧接口，逐个细胞系产出。仅用于兼容，不建议大矩阵上使用。"""
    n_cols = matrix.shape[1]
    for i, name in enumerate(matrix.cell_lines):
        base = i * n_cols
        yield name, {
            g: to_signed(matrix.rows[base + j]) * matrix.scale
            for j, g in enumerate(matrix.genes)
            if to_signed(matrix.rows[base + j]) != MISSING
        }
