"""冲突检测与归一化的单元测试。

这些测试不需要网络，是保证"规则层可复现"的基础。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tsa.conflicts import detect_conflicts  # noqa: E402
from tsa.engine import AuditRequest, run_audit  # noqa: E402
from tsa.mock import mock_connector  # noqa: E402
from tsa.normalize import percentile_rank, to_signal  # noqa: E402
from tsa.schema import ConflictType, Dimension, Polarity  # noqa: E402


class TestNormalize(unittest.TestCase):
    def test_percentile_rank_basic(self):
        pop = [1, 2, 3, 4, 5]
        self.assertAlmostEqual(percentile_rank(3, pop), 0.5)
        self.assertGreater(percentile_rank(5, pop), 0.8)
        self.assertLess(percentile_rank(1, pop), 0.2)

    def test_high_is_favorable(self):
        polarity, strength = to_signal(0.95, high_is_favorable=True)
        self.assertIs(polarity, Polarity.FAVORABLE)
        self.assertGreater(strength, 0.8)

    def test_high_is_unfavorable(self):
        polarity, _ = to_signal(0.95, high_is_favorable=False)
        self.assertIs(polarity, Polarity.UNFAVORABLE)

    def test_middle_is_neutral(self):
        polarity, strength = to_signal(0.5, high_is_favorable=True)
        self.assertIs(polarity, Polarity.NEUTRAL)
        self.assertAlmostEqual(strength, 0.0)


class TestConflictRules(unittest.TestCase):
    def test_kras_triggers_expected_conflicts(self):
        card = run_audit(AuditRequest(gene="KRAS", cancer="LUAD"), [mock_connector()])
        types = {c.type for c in card.conflicts}
        self.assertIn(ConflictType.NARROW_THERAPEUTIC_WINDOW, types)
        self.assertIn(ConflictType.LACKS_UNIQUENESS, types)
        self.assertIn(ConflictType.FEASIBLE_BUT_UNSAFE, types)

    def test_clean_target_has_no_conflicts(self):
        card = run_audit(
            AuditRequest(gene="SELECTIVE_EXAMPLE", cancer="LUAD"), [mock_connector()]
        )
        self.assertEqual([c.type for c in card.conflicts], [])
        self.assertEqual(card.verdict, "值得推进")

    def test_conflicts_sorted_by_severity(self):
        card = run_audit(AuditRequest(gene="KRAS", cancer="LUAD"), [mock_connector()])
        severities = [c.severity for c in card.conflicts]
        self.assertEqual(severities, sorted(severities, reverse=True))

    def test_weak_signals_do_not_trigger(self):
        """强度低于阈值时不应触发冲突 —— 防止噪声被当成矛盾。"""
        from tsa.normalize import make_record

        records = [
            make_record(
                dimension=Dimension.DEPENDENCY,
                source="t",
                metric="m",
                statement="s",
                percentile=0.60,
            ),
            make_record(
                dimension=Dimension.NORMAL_EXPRESSION,
                source="t",
                metric="m",
                statement="s",
                percentile=0.60,
            ),
        ]
        self.assertEqual(detect_conflicts(records, gene="X"), [])

    def test_every_conflict_has_cause_candidates(self):
        card = run_audit(AuditRequest(gene="KRAS", cancer="LUAD"), [mock_connector()])
        for conflict in card.conflicts:
            self.assertTrue(conflict.cause_candidates, "每个冲突都要给出候选原因")
            self.assertTrue(conflict.suggested_experiment, "每个冲突都要给出区分实验")


class TestEngine(unittest.TestCase):
    def test_missing_dimensions_reported(self):
        class EmptyConnector:
            name = "empty"

            def fetch(self, req):
                return []

        card = run_audit(AuditRequest(gene="X", cancer="LUAD"), [EmptyConnector()])
        self.assertEqual(len(card.missing_dimensions), len(list(Dimension)))

    def test_failing_connector_does_not_break_audit(self):
        from tsa.connectors.base import ConnectorError

        class BrokenConnector:
            name = "broken"

            def fetch(self, req):
                raise ConnectorError("模拟数据源故障")

        card = run_audit(
            AuditRequest(gene="KRAS", cancer="LUAD"), [mock_connector(), BrokenConnector()]
        )
        self.assertTrue(card.records, "坏掉的数据源不应影响其他数据源的结果")
        self.assertIn("数据源未返回结果", card.headline)


if __name__ == "__main__":
    unittest.main()
