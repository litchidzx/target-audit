"""维度元数据。

`high_is_favorable` 是归一化层的方向开关：
高的数值对这个靶点是有利还是不利。整个引擎只有这一处定义方向，
避免方向判断散落在各处导致互相矛盾。
"""

from __future__ import annotations

from dataclasses import dataclass

from .schema import Dimension, Group


@dataclass(frozen=True)
class DimensionMeta:
    dimension: Dimension
    group: Group
    label_zh: str
    question: str
    high_is_favorable: bool
    source_hint: str


DIMENSION_META: dict[Dimension, DimensionMeta] = {
    Dimension.DEPENDENCY: DimensionMeta(
        dimension=Dimension.DEPENDENCY,
        group=Group.TUMOR,
        label_zh="肿瘤依赖性",
        question="在这个癌种的模型里敲掉它，细胞会不会死",
        high_is_favorable=True,
        source_hint="DepMap CRISPR gene effect",
    ),
    Dimension.SELECTIVITY: DimensionMeta(
        dimension=Dimension.SELECTIVITY,
        group=Group.TUMOR,
        label_zh="跨癌种选择性",
        question="这份依赖是本癌种独有，还是到处都依赖",
        high_is_favorable=True,
        source_hint="DepMap 本癌种 vs 其他谱系",
    ),
    Dimension.CLINICAL: DimensionMeta(
        dimension=Dimension.CLINICAL,
        group=Group.CLINICAL,
        label_zh="临床相关性",
        question="真实病人里它是什么样：突变、表达、和生存的关系",
        high_is_favorable=True,
        source_hint="TCGA-PAAD / CPTAC",
    ),
    Dimension.NORMAL_EXPRESSION: DimensionMeta(
        dimension=Dimension.NORMAL_EXPRESSION,
        group=Group.NORMAL,
        label_zh="正常组织暴露",
        question="正常组织里哪些细胞类型表达它，是否富集在更新组织",
        high_is_favorable=False,
        source_hint="HPA（RNA + 蛋白 IHC + 单细胞）/ GTEx",
    ),
    Dimension.GENETIC_CONSTRAINT: DimensionMeta(
        dimension=Dimension.GENETIC_CONSTRAINT,
        group=Group.NORMAL,
        label_zh="人类遗传学耐受性",
        question="人类缺了这个基因会怎样（用天然实验预判毒性）",
        high_is_favorable=False,
        source_hint="gnomAD pLI / LOEUF + ClinVar",
    ),
    Dimension.ON_TARGET_TOXICITY: DimensionMeta(
        dimension=Dimension.ON_TARGET_TOXICITY,
        group=Group.NORMAL,
        label_zh="经验性在靶毒性",
        question="已经有同靶点的药了吗，它的真实不良反应谱是什么",
        high_is_favorable=False,
        source_hint="openFDA FAERS + IMPC 小鼠敲除表型",
    ),
    Dimension.DRUGGABILITY: DimensionMeta(
        dimension=Dimension.DRUGGABILITY,
        group=Group.FEASIBILITY,
        label_zh="成药性与结构",
        question="做不做得出来：有没有已知活性分子、有没有可用结构",
        high_is_favorable=True,
        source_hint="ChEMBL + PDB / AlphaFold DB",
    ),
}


def group_of(dimension: Dimension) -> Group:
    return DIMENSION_META[dimension].group


def label(dimension: Dimension) -> str:
    return DIMENSION_META[dimension].label_zh
