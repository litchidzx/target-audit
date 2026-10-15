"""解释层测试。

重点不是"模型说得好不好"，而是"模型出问题时系统会不会垮"。
所以这里用假的 provider 模拟各种坏输出，检查校验、重试与降级是否按设计工作。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tsa.engine import AuditRequest, run_audit  # noqa: E402
from tsa.explain import (  # noqa: E402
    build_user_prompt,
    explain_conflicts,
    fallback_explanation,
    validate,
)
from tsa.llm.base import LLMError, LLMResponse, extract_json  # noqa: E402
from tsa.llm.registry import build_provider  # noqa: E402
from tsa.llm.base import LLMConfig  # noqa: E402
from tsa.llm.stub import StubProvider  # noqa: E402
from tsa.mock import mock_connector  # noqa: E402


class ScriptedProvider:
    """按脚本返回预设文本，用来模拟各种异常输出。"""

    name = "scripted"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def complete(self, system, user, json_mode=False):
        self.calls += 1
        item = self.outputs.pop(0) if self.outputs else ""
        if isinstance(item, Exception):
            raise item
        return LLMResponse(text=item, model="scripted-1")


def _kras_conflicts():
    card = run_audit(AuditRequest(gene="KRAS", cancer="LUAD"), [mock_connector()])
    return card.conflicts


class TestJsonExtraction(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_fenced_json(self):
        text = '```json\n{"a": 1}\n```'
        self.assertEqual(extract_json(text), {"a": 1})

    def test_json_with_surrounding_prose(self):
        text = '好的，分析如下：\n{"a": 1, "b": "x"}\n以上。'
        self.assertEqual(extract_json(text), {"a": 1, "b": "x"})

    def test_braces_inside_strings_do_not_confuse_scanner(self):
        text = '前言 {"note": "这里有个 } 字符", "a": 2} 结尾'
        self.assertEqual(extract_json(text), {"note": "这里有个 } 字符", "a": 2})

    def test_empty_raises(self):
        with self.assertRaises(LLMError):
            extract_json("")

    def test_no_json_raises(self):
        with self.assertRaises(LLMError):
            extract_json("这段话里没有 JSON")


class TestValidation(unittest.TestCase):
    def setUp(self):
        self.conflict = _kras_conflicts()[0]

    def _payload(self, **overrides):
        base = {
            "cause": self.conflict.cause_candidates[0].value,
            "explanation": "这是一段足够长的解释，用于通过长度校验。",
            "evidence_refs": [0],
            "discriminating_experiment": "做这个实验来区分不同解释。",
            "confidence": 0.5,
        }
        base.update(overrides)
        return base

    def test_valid_payload_passes(self):
        result = validate(self._payload(), self.conflict)
        self.assertEqual(result.source, "llm")

    def test_cause_outside_vocabulary_rejected(self):
        with self.assertRaises(ValueError):
            validate(self._payload(cause="我自己编的原因"), self.conflict)

    def test_missing_evidence_refs_rejected(self):
        with self.assertRaises(ValueError):
            validate(self._payload(evidence_refs=[]), self.conflict)

    def test_out_of_range_evidence_ref_rejected(self):
        with self.assertRaises(ValueError):
            validate(self._payload(evidence_refs=[99]), self.conflict)

    def test_boolean_is_not_an_integer_ref(self):
        with self.assertRaises(ValueError):
            validate(self._payload(evidence_refs=[True]), self.conflict)

    def test_non_numeric_confidence_rejected(self):
        with self.assertRaises(ValueError):
            validate(self._payload(confidence="高"), self.conflict)

    def test_confidence_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            validate(self._payload(confidence=1.5), self.conflict)

    def test_short_explanation_rejected(self):
        with self.assertRaises(ValueError):
            validate(self._payload(explanation="太短"), self.conflict)


class TestExplainConflicts(unittest.TestCase):
    def test_good_model_output_is_used(self):
        conflicts = _kras_conflicts()
        target = conflicts[0]
        payload = {
            "cause": target.cause_candidates[0].value,
            "explanation": "模型给出的解释，长度足够通过校验。",
            "evidence_refs": [0],
            "discriminating_experiment": "按建议做区分实验。",
            "confidence": 0.7,
        }
        provider = ScriptedProvider([json.dumps(payload, ensure_ascii=False)])
        explain_conflicts([target], provider, "KRAS", "LUAD")
        self.assertEqual(target.metadata["source"], "llm")
        self.assertEqual(target.metadata["model"], "scripted-1")
        self.assertEqual(provider.calls, 1)

    def test_invalid_output_retries_then_falls_back(self):
        target = _kras_conflicts()[0]
        provider = ScriptedProvider(["不是 JSON", "仍然不是 JSON"])
        explain_conflicts([target], provider, "KRAS", "LUAD")
        self.assertEqual(provider.calls, 2, "应当重试一次")
        self.assertEqual(target.metadata["source"], "fallback")
        self.assertIn("规则层", target.explanation)

    def test_provider_exception_falls_back_without_raising(self):
        target = _kras_conflicts()[0]
        provider = ScriptedProvider([LLMError("服务不可用"), LLMError("服务不可用")])
        explain_conflicts([target], provider, "KRAS", "LUAD")
        self.assertEqual(target.metadata["source"], "fallback")

    def test_second_attempt_success_is_used(self):
        target = _kras_conflicts()[0]
        good = json.dumps(
            {
                "cause": target.cause_candidates[0].value,
                "explanation": "第二次尝试给出的解释，长度足够。",
                "evidence_refs": [0],
                "discriminating_experiment": "区分实验设计。",
                "confidence": 0.6,
            },
            ensure_ascii=False,
        )
        provider = ScriptedProvider(["坏的输出", good])
        explain_conflicts([target], provider, "KRAS", "LUAD")
        self.assertEqual(target.metadata["source"], "llm")
        self.assertEqual(target.metadata["attempts"], 2)

    def test_prompt_carries_controlled_causes_and_indexed_evidence(self):
        target = _kras_conflicts()[0]
        prompt = json.loads(build_user_prompt(target, "KRAS", "LUAD"))
        self.assertEqual(
            [c["value"] for c in prompt["candidate_causes"]],
            [c.value for c in target.cause_candidates],
        )
        self.assertEqual(len(prompt["evidence"]), len(target.evidence))
        self.assertEqual(prompt["evidence"][0]["index"], 0)

    def test_fallback_explanation_uses_rule_layer_output(self):
        target = _kras_conflicts()[0]
        result = fallback_explanation(target)
        self.assertEqual(result.source, "fallback")
        self.assertEqual(result.cause, target.cause_candidates[0])
        self.assertEqual(result.discriminating_experiment, target.suggested_experiment)


class TestProviderRegistry(unittest.TestCase):
    def test_stub_provider_builds_without_credentials(self):
        provider = build_provider(LLMConfig(provider="stub"))
        self.assertEqual(provider.name, "stub")

    def test_unknown_provider_is_rejected(self):
        with self.assertRaises(LLMError):
            build_provider(LLMConfig(provider="不存在这个服务商"))

    def test_preset_provider_without_model_is_rejected(self):
        with self.assertRaises(LLMError):
            build_provider(LLMConfig(provider="deepseek", api_key="x"))

    def test_stub_provider_output_passes_validation(self):
        """兜底实现必须也能通过校验，否则降级路径本身就是坏的。"""
        target = _kras_conflicts()[0]
        provider = StubProvider()
        explain_conflicts([target], provider, "KRAS", "LUAD")
        self.assertEqual(target.metadata["source"], "llm")


class TestEngineIntegration(unittest.TestCase):
    def test_run_audit_without_llm_leaves_explanation_empty(self):
        card = run_audit(AuditRequest(gene="KRAS", cancer="LUAD"), [mock_connector()])
        self.assertTrue(card.conflicts)
        for conflict in card.conflicts:
            self.assertIsNone(conflict.explanation)
            self.assertTrue(conflict.cause_candidates)

    def test_run_audit_with_llm_fills_explanations(self):
        card = run_audit(
            AuditRequest(gene="KRAS", cancer="LUAD"),
            [mock_connector()],
            llm=StubProvider(),
        )
        for conflict in card.conflicts:
            self.assertIsNotNone(conflict.explanation)
            self.assertIn("source", conflict.metadata)


if __name__ == "__main__":
    unittest.main()
