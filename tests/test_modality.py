"""治疗模态接进审计链路后的行为。

要守住的三点：不指定模态时行为与以前完全一致；指定后覆盖率分母按适用维度收窄；
涉及不适用维度的冲突只做标注、不静默删除（全维不适用的才剔除）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tsa.engine import AuditRequest, run_audit  # noqa: E402
from tsa.mapping import (  # noqa: E402
    APPLICABILITY_FLAGGED,
    APPLICABILITY_OK,
    APPLICABILITY_SUPPRESSED,
    applicability,
)
from tsa.mock import mock_connector  # noqa: E402
from tsa.report import render_markdown  # noqa: E402
from tsa.schema import Dimension  # noqa: E402


def audit(**kwargs):
    return run_audit(
        AuditRequest(gene="KRAS", cancer="PAAD", **kwargs),
        [mock_connector()],
        include_variants=False,
    )


class TestApplicability(unittest.TestCase):
    def test_all_dimensions_not_applicable_means_suppressed(self):
        status = applicability([Dimension.DRUGGABILITY], [Dimension.DRUGGABILITY])
        self.assertEqual(status, APPLICABILITY_SUPPRESSED)

    def test_partially_not_applicable_is_flagged_only(self):
        status = applicability(
            [Dimension.GENETIC_CONSTRAINT, Dimension.DRUGGABILITY],
            [Dimension.DRUGGABILITY],
        )
        self.assertEqual(status, APPLICABILITY_FLAGGED)

    def test_untouched_conflict_is_ok(self):
        status = applicability([Dimension.DEPENDENCY, Dimension.SELECTIVITY], [Dimension.DRUGGABILITY])
        self.assertEqual(status, APPLICABILITY_OK)


class TestEngineWiring(unittest.TestCase):
    def test_without_modality_nothing_changes(self):
        card = audit()
        self.assertIsNone(card.mapping)
        self.assertEqual(card.not_applicable_dimensions, [])
        self.assertNotIn("评价标准", render_markdown(card))

    def test_modality_records_not_applicable_dimensions(self):
        card = audit(modality="cell_therapy")
        self.assertEqual(
            sorted(d.value for d in card.not_applicable_dimensions),
            ["druggability", "on_target_toxicity"],
        )
        self.assertIsNotNone(card.mapping)

    def test_verdict_says_the_denominator_was_narrowed(self):
        card = audit(modality="cell_therapy")
        self.assertIn("不适用维度", card.verdict)

    def test_conflict_touching_not_applicable_dimension_is_flagged_not_dropped(self):
        card = audit(modality="cell_therapy")
        flagged = [c for c in card.conflicts if c.metadata.get("mapping")]
        self.assertTrue(flagged, "涉及成药性的冲突应当被标注而不是删除")
        self.assertTrue(all("不适用维度" in c.metadata["mapping"] for c in flagged))

    def test_report_renders_mapping_section_with_sources(self):
        text = render_markdown(audit(modality="protac"))
        self.assertIn("## 评价标准", text)
        self.assertIn("降解剂", text)
        self.assertIn("来源：内置默认", text)

    def test_modality_variants_differ(self):
        # 同一靶点换一个模态，不适用维度应当不同，否则这层没有起作用
        antibody = audit(modality="antibody")
        cell = audit(modality="cell_therapy")
        self.assertNotEqual(
            sorted(d.value for d in antibody.not_applicable_dimensions),
            sorted(d.value for d in cell.not_applicable_dimensions),
        )


if __name__ == "__main__":
    unittest.main()
