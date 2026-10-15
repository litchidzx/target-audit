# 靶点审计 · EGFR / LUAD

**判断：** 安全边界待确认（另有：抑制深度需严格控制）

**主标题：** EGFR 做得出来，但安全边界可能很窄。

**治疗窗：** moderate（部分正常侧风险）

*生成时间 2026-10-15T09:41:19+00:00*

## 冲突

### R5 · feasible_but_unsafe

**EGFR 有现成的成药路径，但同靶点药物已知的不良反应提示在靶毒性风险高。**

- 涉及维度：成药性与结构 × 经验性在靶毒性
- 严重度：0.94
- 候选原因（受控词表，待解释层确认）：
  - 模型体系差异（细胞系 / 类器官 / PDX 结论不一致）
  - 样本量不足或选择偏倚
- 建议的区分实验：按不良反应涉及的器官系统做靶点表达和功能验证，估算可耐受的抑制深度。

### R6 · genetic_fragility

**EGFR 具备成药条件，但人类遗传学证据提示功能缺失不耐受 —— 抑制深度需要严格控制。**

- 涉及维度：人类遗传学耐受性 × 成药性与结构
- 严重度：0.87
- 候选原因（受控词表，待解释层确认）：
  - 通路冗余（有代偿通路）
  - 时间动态（不同疾病阶段结论不同）
- 建议的区分实验：查清 LoF 携带者的表型谱，确认毒性与抑制深度之间的剂量关系。

## 证据矩阵

**肿瘤侧：有多重要、有多独特**

  · **肿瘤依赖性** · 分位 0.53 · 强度 0.05 · [DepMap CRISPRGeneEffect](https://depmap.org/portal/)
    Lung 谱系 126 个细胞系中，中位 gene effect -0.106（值越负表示越依赖）。分位已按依赖程度换算，越高表示在本谱系中越依赖。
  － **跨癌种选择性** · 分位 0.14 · 强度 0.72 · [DepMap CRISPRGeneEffect](https://depmap.org/portal/)
    本谱系相对其他谱系的选择性差值为 -0.047（正值表示本谱系更依赖）。

**临床侧：真实病人里是什么样**

  ＋ **临床相关性** · 分位 0.91 · 强度 0.83 · [GDC / TCGA](https://portal.gdc.cancer.gov/projects/TCGA-LUAD)
    TCGA-LUAD 的 585 个病例中，162 例存在改变（27.7%）；其中点突变 78 例（13.3%）、纯合缺失 0 例（0.0%）、扩增 114 例（19.5%）。改变频率是三类取并集后的结果，不等于突变频率。注意这里只统计结构性改变，未包含启动子甲基化沉默和杂合性缺失，所以对于以表观沉默为主失活的基因，真实失活率会高于此值。

**正常侧：打它会不会伤到好人**

  · **正常组织暴露** · 分位 0.67 · 强度 0.33 · [HPA](https://www.proteinatlas.org/ENSG00000146648.json)
    正常组织分布：Detected in many；组织特异性：Tissue enhanced；单细胞层面分布：Detected in many；单细胞特异性：Cell type enhanced。广泛表达程度为 0.67（由分类变量映射到有序等级后合成，不是连续测量值）。
  － **人类遗传学耐受性** · 分位 0.85 · 强度 0.69 · [gnomAD v2.1.1 constraint](https://gnomad.broadinstitute.org/gene/EGFR)
    EGFR 的功能缺失约束：pLI 0.368、LOEUF 0.349。以 gnomAD 全部 19197 个基因为参照，该基因对功能缺失的不耐受程度位于第 85% 分位（分位越高越不耐受，抑制它的风险越大）。判定为对功能缺失不耐受（LOEUF 0.349 低于不耐受阈值）。
  － **经验性在靶毒性** · 分位 0.90 · 强度 0.80 · [openFDA FAERS](https://open.fda.gov/apis/drug/event/)
    同靶点药物 CETUXIMAB 的自发上报中记录到 13018 例不良反应，主要构成为：DIARRHOEA（1188 例）、NAUSEA（948 例）、OFF LABEL USE（899 例）、VOMITING（809 例）、NEUTROPENIA（747 例）。共查询了 3 个同靶点药物，其中 3 个取到记录。需要注意，上报记录里的反应字段可能混入适应证本身（例如把肺癌当作反应上报），不能直接读作药物引起的事件。FAERS 无分母，无法计算发生率，且存在上报偏倚，本条仅作为需要权衡的安全信号，不构成因果关系判断。

**可做性**

  ＋ **成药性与结构** · 分位 0.95 · 强度 0.90 · [ChEMBL](https://www.ebi.ac.uk/chembl/target_report_card/CHEMBL203/)
    成药路径：已有获批上市的药物（已上市药物 20 个，临床阶段药物 56 个，机制记录 90 条）。作用类型：ANTAGONIST、BINDING AGENT、CROSS-LINKING AGENT、INHIBITOR、NEGATIVE ALLOSTERIC MODULATOR、VACCINE ANTIGEN。相关药物：ERLOTINIB HYDROCHLORIDE、CP-358、LAPATINIB DITOSYLATE、GW-572016F、CETUXIMAB、ABP-494。该判定由研发阶段映射到有序等级得到，不是连续测量值。

## 缺失证据

- 肿瘤依赖性（DepMap CRISPR gene effect）
- 正常组织暴露（HPA（RNA + 蛋白 IHC + 单细胞）/ GTEx）

---

本报告为研究用途的靶点证据整理，不构成任何医疗或投资建议。所有结论均可通过上方链接回溯至原始数据源。