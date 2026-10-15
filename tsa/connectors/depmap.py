"""DepMap：肿瘤依赖性 + 跨癌种选择性。

这一步贡献两个维度：DEPENDENCY（在本癌种模型里敲掉会不会死）和
SELECTIVITY（这份依赖是不是本癌种独有）。第二个维度是 Open Targets
结构上没有的比较 —— 它锚定单一疾病，"本癌种 vs 其他谱系"的对撞做不了。

数据准备（DepMap 是本地大文件，不是 API）：

    https://depmap.org/portal/data_page/?tab=allData

把两个文件放进这个目录（脚本会自动创建）：

    <项目根>/data/depmap/

完整路径：
    C:\\Users\\daizh\\Documents\\Codex\\2026-10-09\\new-chat-2\\work\\target-audit\\data\\depmap\\

需要两个文件：
    CRISPRGeneEffect.csv   ~400MB  行=细胞系 列=基因（列名形如 "KRAS (3845)"）
    Model.csv              细胞系注释，含 OncotreeLineage 字段

文件名不必严格一致 —— 下载页的文件名可能带版本号或 "(1)" 后缀，
resolve_data_file() 会按模式忽略大小写地匹配。如果 DepMap 提供的是整包 zip，
解压后只把这两个文件挑出来放进去即可，其他文件不需要。

计算逻辑已按纯函数写出来并可单测，接上文件即可跑。

已知的领域陷阱（必须在报告里写清楚，不能假装没这回事）：
  1. DepMap 只有癌细胞系，没有正常细胞，所以"正常细胞必需性"查不到，
     正常侧只能用 HPA / gnomAD 这类代理信号。
  2. 二维细胞系的依赖图谱和类器官 / PDX 存在已知差异，本癌种尤其明显。
     这条差异本身会在跨癌种比较里制造假信号，是 R3 冲突的常见成因。
"""

from __future__ import annotations

import csv
import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Sequence

from ..normalize import make_record, percentile_rank
from ..schema import Dimension, EvidenceRecord
from .base import Connector, ConnectorNotConfigured

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "depmap"
GENE_EFFECT_FILE = DATA_DIR / "CRISPRGeneEffect.csv"
MODEL_FILE = DATA_DIR / "Model.csv"

# 允许的文件名模式，按优先级排列。DepMap 的下载文件名会随版本变化，
# 让使用者为了改名而卡住是不必要的摩擦。
GENE_EFFECT_PATTERNS = ("CRISPRGeneEffect.csv", "CRISPRGeneEffect*.csv", "*GeneEffect*.csv")
MODEL_PATTERNS = ("Model.csv", "Model *.csv", "Model*.csv")


def resolve_data_file(patterns: Sequence[str], directory: Path = DATA_DIR) -> Path | None:
    """在数据目录里按模式找文件，忽略大小写。找不到返回 None。"""
    if not directory.exists():
        return None
    entries = {p.name.lower(): p for p in directory.iterdir() if p.is_file()}

    for pattern in patterns:
        exact = entries.get(pattern.lower())
        if exact is not None:
            return exact
    for pattern in patterns:
        translated = fnmatch.translate(pattern.lower())
        for name, path in entries.items():
            if re.match(translated, name):
                return path
    return None


def missing_data_hint(directory: Path = DATA_DIR) -> str:
    """数据文件缺失时给出一条能照着做的提示。"""
    found = sorted(p.name for p in directory.iterdir()) if directory.exists() else []
    listing = "、".join(found) if found else "（目录为空）"
    return (
        f"未在 {directory} 找到 DepMap 数据文件。\n"
        f"  当前目录内容：{listing}\n"
        f"  需要 CRISPRGeneEffect.csv 与 Model.csv，"
        f"下载地址 https://depmap.org/portal/data_page/?tab=allData"
    )

# 癌种代码 -> DepMap 的 OncotreeLineage 取值。
# 这些字面写法已于 2026-10-15 对着实际下载的 Model.csv 逐个核对过。
# 有几个和直觉不同：胃不在 "Stomach" 而在 "Esophagus/Stomach"，
# 卵巢在 "Ovary/Fallopian Tube"，膀胱在 "Bladder/Urinary Tract"。
CANCER_TO_LINEAGE = {
    "PAAD": "Pancreas",
    "LUAD": "Lung",
    "LUSC": "Lung",
    "BRCA": "Breast",
    "COAD": "Bowel",
    "READ": "Bowel",
    "STAD": "Esophagus/Stomach",
    "LIHC": "Liver",
    "CHOL": "Biliary Tract",
    "KIRC": "Kidney",
    "OV": "Ovary/Fallopian Tube",
    "UCEC": "Uterus",
    "CESC": "Cervix",
    "HNSC": "Head and Neck",
    "BLCA": "Bladder/Urinary Tract",
    "SKCM": "Skin",
    "GBM": "CNS/Brain",
    "PRAD": "Prostate",
    "THCA": "Thyroid",
}

# 用于计算分位的参照基因面板。
# 少了它，分位就会在"只有一个基因的分布"里计算，结果恒等于 0.5 —— 这个缺陷
# 不会报错，只会安静地让依赖性与选择性两个维度失去意义，所以必须显式加载。
REFERENCE_GENE_PANEL: tuple[str, ...] = (
    "TP53", "KRAS", "SMAD4", "CDKN2A", "ARID1A", "KDM6A", "RNF43", "TGFBR2",
    "KMT2C", "GNAS", "BRAF", "PIK3CA", "PTEN", "RB1", "APC", "ATM",
    "BRCA1", "BRCA2", "PALB2", "EGFR", "ERBB2", "MET", "MYC", "CCND1",
    "MDM2", "MTOR", "STK11", "NF1", "NRAS", "HRAS", "IDH1", "FBXW7",
    "ACVR1B", "ACVR2A", "ROBO2", "TERT", "CTNNB1", "VHL", "KEAP1",
)


def resolve_lineage(cancer: str) -> str:
    """解析癌种标识对应的 DepMap 谱系名。

    先查映射表；查不到时，如果传入值本身看起来就是一个谱系名（不是全大写缩写），
    就直接使用，便于传入 DepMap 提供的任意谱系。
    """
    key = (cancer or "").strip()
    mapped = CANCER_TO_LINEAGE.get(key.upper())
    if mapped:
        return mapped
    if key and not key.isupper():
        return key
    raise ConnectorNotConfigured(
        f"未知癌种 {cancer}，也没有对应谱系。"
        f"可传入已收录缩写（{', '.join(sorted(CANCER_TO_LINEAGE))}）或直接给 DepMap 谱系名。"
    )


@dataclass
class DependencyStats:
    """一个基因在目标谱系里的依赖性与选择性。"""

    gene: str
    lineage: str
    n_in_lineage: int
    n_outside: int
    median_effect_in: float
    median_effect_outside: float
    selectivity_delta: float
    dependency_percentile: float
    selectivity_percentile: float


def _gene_column(header: Sequence[str], gene: str) -> int | None:
    """DepMap 的列名形如 'KRAS (3845)'，用正则匹配基因符号。"""
    pattern = re.compile(rf"^{re.escape(gene)}\s*\((\d+)\)$", re.IGNORECASE)
    for i, col in enumerate(header):
        if pattern.match(col.strip()):
            return i
    return None


def _to_float(x: str) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_lineage_map(model_file: Path | None = None) -> dict[str, str]:
    """细胞系 -> 谱系。第一列是 ModelID。"""
    model_file = model_file or resolve_data_file(MODEL_PATTERNS) or MODEL_FILE
    if not model_file.exists():
        raise ConnectorNotConfigured(missing_data_hint())
    mapping: dict[str, str] = {}
    with model_file.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        field = "OncotreeLineage"
        if field not in (reader.fieldnames or []):
            raise ConnectorNotConfigured(f"{model_file} 里找不到 {field} 字段")
        id_field = reader.fieldnames[0]
        for row in reader:
            mapping[row[id_field]] = (row.get(field) or "").strip()
    return mapping


def _same_lineage(a: str, b: str) -> bool:
    """谱系名比对做大小写与空白归一，避免因版本命名差异漏配。"""
    return a.strip().casefold() == b.strip().casefold()


def iter_gene_effect(
    gene_effect_file: Path | None = None, genes: Iterable[str] | None = None
) -> tuple[list[str], list[tuple[str, dict[str, float]]]]:
    """流式读取 gene effect 矩阵。

    这个文件有几百兆、上万列，一次性读进内存不划算，所以按行流式处理，
    并且只保留关心的基因列。
    """
    gene_effect_file = gene_effect_file or resolve_data_file(GENE_EFFECT_PATTERNS) or GENE_EFFECT_FILE
    if not gene_effect_file.exists():
        raise ConnectorNotConfigured(missing_data_hint())

    wanted = {g.upper() for g in genes} if genes else None
    rows: list[tuple[str, dict[str, float]]] = []
    with gene_effect_file.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        column_index: dict[str, int] = {}
        for i, col in enumerate(header):
            name = col.split(" ")[0].strip().upper()
            if wanted is None or name in wanted:
                column_index[name] = i
        for row in reader:
            if not row:
                continue
            model_id = row[0]
            values: dict[str, float] = {}
            for gene, idx in column_index.items():
                if idx < len(row):
                    v = _to_float(row[idx])
                    if v is not None:
                        values[gene] = v
            rows.append((model_id, values))
    return list(column_index.keys()), rows


def _medians(
    gene: str,
    lineage: str,
    lineage_map: dict[str, str],
    matrix: Sequence[tuple[str, dict[str, float]]],
) -> tuple[list[float], list[float]]:
    inside: list[float] = []
    outside: list[float] = []
    for model_id, values in matrix:
        v = values.get(gene)
        if v is None:
            continue
        if _same_lineage(lineage_map.get(model_id, ""), lineage):
            inside.append(v)
        else:
            outside.append(v)
    return inside, outside


def compute_stats(
    gene: str,
    lineage: str,
    lineage_map: dict[str, str],
    matrix: Sequence[tuple[str, dict[str, float]]],
    all_genes: Sequence[str],
) -> DependencyStats:
    """纯函数：从矩阵算依赖性与选择性。不碰网络，可单测。"""
    gene = gene.upper()
    inside, outside = _medians(gene, lineage, lineage_map, matrix)
    if not inside or not outside:
        raise ValueError(
            f"{gene} 在 {lineage} 内外都需要有数据，当前 in={len(inside)} out={len(outside)}"
        )

    med_in = median(inside)
    med_out = median(outside)
    # gene effect 越负越依赖，所以 out - in 为正表示本谱系更依赖
    delta = med_out - med_in

    in_medians: list[float] = []
    deltas: list[float] = []
    for g in all_genes:
        gi, go = _medians(g, lineage, lineage_map, matrix)
        if gi:
            in_medians.append(median(gi))
        if gi and go:
            deltas.append(median(go) - median(gi))

    # gene effect 越负表示越依赖，而分位的语义是"越高越有利"（见 dimensions.py）。
    # 两者方向相反，必须翻转。不翻的后果是：KRAS 这类强依赖基因在胰腺癌模型里
    # 中位 gene effect 达 -1.78，却会因为落在分布的低端而拿到 0.04 分位，
    # 被系统判成"不利于推进"—— 结论与事实完全相反，而且不会报任何错。
    dep_pct = 1.0 - percentile_rank(med_in, in_medians) if in_medians else 0.5
    sel_pct = percentile_rank(delta, deltas) if deltas else 0.5

    return DependencyStats(
        gene=gene,
        lineage=lineage,
        n_in_lineage=len(inside),
        n_outside=len(outside),
        median_effect_in=med_in,
        median_effect_outside=med_out,
        selectivity_delta=delta,
        dependency_percentile=dep_pct,
        selectivity_percentile=sel_pct,
    )


def stats_to_records(stats: DependencyStats) -> list[EvidenceRecord]:
    return [
        make_record(
            dimension=Dimension.DEPENDENCY,
            source="DepMap CRISPRGeneEffect",
            metric="median_gene_effect",
            statement=(
                f"{stats.lineage} 谱系 {stats.n_in_lineage} 个细胞系中，中位 gene effect "
                f"{stats.median_effect_in:.3f}（值越负表示越依赖）。"
                f"分位已按依赖程度换算，越高表示在本谱系中越依赖。"
            ),
            percentile=stats.dependency_percentile,
            value=round(stats.median_effect_in, 4),
            unit="gene_effect",
            cohort=f"{stats.lineage} ({stats.n_in_lineage} lines)",
            url="https://depmap.org/portal/",
            confidence=0.7,
            raw={
                "n_outside": stats.n_outside,
                "median_outside": stats.median_effect_outside,
            },
        ),
        make_record(
            dimension=Dimension.SELECTIVITY,
            source="DepMap CRISPRGeneEffect",
            metric="lineage_selectivity_delta",
            statement=(
                f"本谱系相对其他谱系的选择性差值为 {stats.selectivity_delta:+.3f}"
                f"（正值表示本谱系更依赖）。"
            ),
            percentile=stats.selectivity_percentile,
            value=round(stats.selectivity_delta, 4),
            unit="gene_effect_delta",
            cohort=f"{stats.lineage} vs others",
            url="https://depmap.org/portal/",
            confidence=0.6,
        ),
    ]


class DepMapConnector(Connector):
    name = "depmap"
    dimensions = (Dimension.DEPENDENCY, Dimension.SELECTIVITY)

    def fetch(self, req: Any) -> list[EvidenceRecord]:
        gene = (getattr(req, "gene", None) or str(req)).upper()
        cancer = getattr(req, "cancer", None)
        if not cancer:
            raise ConnectorNotConfigured("请求未指定癌种；癌种是必填参数，系统不设默认值")
        lineage = resolve_lineage(cancer)

        lineage_map = load_lineage_map()
        # 必须连同参照面板一起加载，否则分位没有意义。见 REFERENCE_GENE_PANEL 的说明。
        panel = sorted({gene, *REFERENCE_GENE_PANEL})
        all_genes, matrix = iter_gene_effect(genes=panel)
        if gene not in all_genes:
            raise ConnectorNotConfigured(f"在 gene effect 矩阵里找不到 {gene}")
        try:
            stats = compute_stats(gene, lineage, lineage_map, matrix, all_genes)
        except ValueError as exc:
            raise ConnectorNotConfigured(str(exc)) from exc
        return stats_to_records(stats)
