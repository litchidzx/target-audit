"""离线假数据。

作用是在任何数据源接通之前先把整条管线跑通 —— 归一化、冲突检测、报告渲染、
前端联调都靠它。数值全部是示意值，不是真实测量结果。
"""

from __future__ import annotations

from typing import Any

from .connectors.base import Connector
from .normalize import make_record
from .schema import Dimension, EvidenceRecord


def _records_for(gene: str, cancer: str) -> list[EvidenceRecord]:
    """两个对照例子：一个治疗窗窄的经典靶点，一个干净的对照靶点。

    这里的所有文本都随传入的癌种变化，因为系统本身不绑定任何癌种。
    """
    project = cancer if cancer.upper().startswith("TCGA-") else f"TCGA-{cancer.upper()}"
    if gene.upper() == "KRAS":
        return [
            make_record(
                dimension=Dimension.DEPENDENCY,
                source="DepMap(fixture)",
                metric="gene_effect",
                statement=f"在 {cancer} 模型中敲除后生长显著受损。",
                percentile=0.92,
                value=-0.85,
                unit="gene_effect",
                cohort=f"{cancer} cell lines",
                url="https://depmap.org/portal/gene/KRAS",
                confidence=0.7,
            ),
            make_record(
                dimension=Dimension.SELECTIVITY,
                source="DepMap(fixture)",
                metric="lineage_selectivity",
                statement="依赖信号在其他多个谱系中同样存在。",
                percentile=0.22,
                value=0.0,
                cohort="all lineages",
                url="https://depmap.org/portal/gene/KRAS",
            ),
            make_record(
                dimension=Dimension.CLINICAL,
                source="TCGA-PAAD(fixture)",
                metric="mutation_frequency",
                statement=f"在 {cancer} 队列中改变频率极高，是最主要的驱动基因之一。",
                percentile=0.98,
                value=0.9,
                unit="fraction",
                cohort=project,
                url=f"https://portal.gdc.cancer.gov/projects/{project}",
                confidence=0.9,
            ),
            make_record(
                dimension=Dimension.NORMAL_EXPRESSION,
                source="HPA(fixture)",
                metric="tissue_breadth",
                statement="正常组织中广泛表达，无组织特异性。",
                percentile=0.9,
                value=None,
                cohort="HPA normal tissue",
                url="https://www.proteinatlas.org/",
            ),
            make_record(
                dimension=Dimension.GENETIC_CONSTRAINT,
                source="gnomAD+ClinVar(fixture)",
                metric="lof_intolerance",
                statement="生殖系变异导致 RAS 病（如 Noonan 综合征），LoF 耐受性低。",
                percentile=0.88,
                value=None,
                url="https://gnomad.broadinstitute.org/",
            ),
            make_record(
                dimension=Dimension.DRUGGABILITY,
                source="ChEMBL+PDB(fixture)",
                metric="chemical_matter_and_structure",
                statement="已有大量已知活性分子与共晶结构。",
                percentile=0.95,
                value=None,
                url="https://www.ebi.ac.uk/chembl/",
                confidence=0.85,
            ),
            make_record(
                dimension=Dimension.ON_TARGET_TOXICITY,
                source="openFDA FAERS(fixture)",
                metric="known_drug_ae_profile",
                statement="同靶点药物有明确的在靶不良反应谱。",
                percentile=0.85,
                value=None,
                url="https://open.fda.gov/apis/drug/event/",
            ),
        ]

    # 对照组：一个各维度方向一致、没有矛盾的假设靶点
    return [
        make_record(
            dimension=Dimension.DEPENDENCY,
            source="DepMap(fixture)",
            metric="gene_effect",
            statement=f"在 {cancer} 模型中敲除后生长显著受损。",
            percentile=0.90,
            value=-0.8,
            unit="gene_effect",
            cohort=f"{cancer} cell lines",
            url="https://depmap.org/portal/",
        ),
        make_record(
            dimension=Dimension.SELECTIVITY,
            source="DepMap(fixture)",
            metric="lineage_selectivity",
            statement="依赖信号集中在本谱系，其他谱系不明显。",
            percentile=0.90,
            value=0.4,
            cohort="all lineages",
            url="https://depmap.org/portal/",
        ),
        make_record(
            dimension=Dimension.CLINICAL,
            source="TCGA-PAAD(fixture)",
            metric="alteration_frequency",
            statement=f"在 {cancer} 队列中有中等频率的改变，与预后相关。",
            percentile=0.78,
            value=0.18,
            unit="fraction",
            cohort=project,
            url="https://portal.gdc.cancer.gov/",
        ),
        make_record(
            dimension=Dimension.NORMAL_EXPRESSION,
            source="HPA(fixture)",
            metric="tissue_breadth",
            statement="正常组织中表达极低且组织局限。",
            percentile=0.12,
            value=None,
            cohort="HPA normal tissue",
            url="https://www.proteinatlas.org/",
        ),
        make_record(
            dimension=Dimension.GENETIC_CONSTRAINT,
            source="gnomAD(fixture)",
            metric="lof_intolerance",
            statement="对功能缺失相对耐受。",
            percentile=0.20,
            value=None,
            url="https://gnomad.broadinstitute.org/",
        ),
        make_record(
            dimension=Dimension.DRUGGABILITY,
            source="ChEMBL+AlphaFold(fixture)",
            metric="chemical_matter_and_structure",
            statement="有少量活性分子，结构预测显示存在可成药口袋。",
            percentile=0.72,
            value=None,
            url="https://alphafold.ebi.ac.uk/",
        ),
        make_record(
            dimension=Dimension.ON_TARGET_TOXICITY,
            source="openFDA FAERS(fixture)",
            metric="known_drug_ae_profile",
            statement="尚无同靶点上市药，无经验性毒性数据。",
            percentile=None,
            value=None,
            url="https://open.fda.gov/apis/drug/event/",
            confidence=0.2,
        ),
    ]


class MockConnector(Connector):
    name = "mock"

    def fetch(self, req: Any) -> list[EvidenceRecord]:
        return _records_for(req.gene, getattr(req, "cancer", "unspecified"))


def mock_connector() -> Connector:
    return MockConnector()
