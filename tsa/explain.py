"""解释层：用大语言模型解释规则层已经检出的矛盾。

分工是刻意的。规则层负责判断"这里存在矛盾"，这一步是确定性的、可复现的；
模型只负责回答"这个矛盾更可能是怎么来的"，这一步需要领域判断，也正是模型
擅长的地方。模型不参与检出，所以换模型不会改变矛盾是否存在。

三道约束防止模型自由发挥：
  1. 原因只能取自受控词表，schema 层面就是枚举；
  2. 必须引用具体证据的编号，引用不存在的编号直接判为无效；
  3. 输出不过校验就重试一次，再不过就降级到规则层的候选原因。

调用方拿到的结果因此始终是可用的：要么是模型给出的解释，要么是规则层的兜底，
不会出现"模型说了句没根据的话"这种情况。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from .llm.base import LLMError, LLMProvider, extract_json
from .dimensions import label
from .schema import CAUSE_LABEL_ZH, Conflict, ConflictCause

log = logging.getLogger("tsa.explain")

MAX_EXPLANATION_CHARS = 800

SYSTEM_PROMPT = """你是一名肿瘤靶点证据审计助手。

给你的是一个已经被规则系统判定为"存在证据矛盾"的靶点。各条证据的方向与强度
已经由本系统计算完成，你的任务不是重新判断矛盾是否存在，而是解释这个矛盾的
可能成因，并指出用哪一个实验可以把不同的解释区分开。

严格要求：
1. 成因只能从给定的"候选原因"列表中选取，不得提出列表之外的新成因。
2. 必须引用具体证据的编号作为依据，编号来自输入中给出的证据列表。
3. 如果现有证据不足以在几个候选成因之间做出区分，应当在解释中明确说明这一点，
   并同时给出最有区分度的实验设计，而不是给出一个确定的结论。
4. 只输出一个 JSON 对象，不要输出任何其他文字。

输出 JSON 的字段：
  cause                     字符串，取自候选原因列表
  explanation               字符串，200 字以内的中文解释，说明矛盾的可能成因
  evidence_refs             整数数组，引用支持该解释的证据编号
  discriminating_experiment 字符串，一个能区分不同解释的实验设计
  confidence                0 到 1 之间的小数，表示该解释的把握程度
"""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "cause",
        "explanation",
        "evidence_refs",
        "discriminating_experiment",
        "confidence",
    ],
    "properties": {
        "cause": {"type": "string", "enum": [c.value for c in ConflictCause]},
        "explanation": {"type": "string", "maxLength": MAX_EXPLANATION_CHARS},
        "evidence_refs": {"type": "array", "items": {"type": "integer"}, "minItems": 1},
        "discriminating_experiment": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}

POLARITY_TEXT = {"favorable": "支持推进", "unfavorable": "提示风险", "neutral": "中性", "unknown": "未知"}


@dataclass
class Explanation:
    cause: ConflictCause
    explanation: str
    evidence_refs: list[int]
    discriminating_experiment: str
    confidence: float
    source: str = "llm"


def build_user_prompt(conflict: Conflict, gene: str, cancer: str) -> str:
    """把冲突对象整理成模型可读的输入。

    证据编号与 conflict.evidence 的下标一致，模型引用编号时直接对应到证据。
    """
    payload = {
        "gene": gene,
        "cancer": cancer,
        "rule_id": conflict.rule_id,
        "conflict_type": conflict.type.value,
        "headline": conflict.headline,
        "involved_dimensions": [label(d) for d in conflict.dimensions],
        "severity": round(conflict.severity, 3),
        "evidence": [
            {
                "index": i,
                "dimension": label(r.dimension),
                "source": r.source,
                "polarity": POLARITY_TEXT.get(r.polarity.value, r.polarity.value),
                "strength": round(r.strength, 3),
                "statement": r.statement,
            }
            for i, r in enumerate(conflict.evidence)
        ],
        "candidate_causes": [
            {"value": c.value, "description": CAUSE_LABEL_ZH[c]}
            for c in conflict.cause_candidates
        ],
        "output_schema": RESPONSE_SCHEMA,
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


def _coerce_float(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 不是数值：{value!r}") from exc
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{field} 超出 0-1 范围：{result}")
    return result


def validate(payload: dict[str, Any], conflict: Conflict) -> Explanation:
    """校验模型输出。任何一条不满足都抛 ValueError，由调用方决定重试或降级。"""
    if not isinstance(payload, dict):
        raise ValueError("输出不是 JSON 对象")

    allowed = {c.value for c in conflict.cause_candidates}
    cause_value = str(payload.get("cause", "")).strip()
    if cause_value not in allowed:
        raise ValueError(f"cause 不在候选原因列表中：{cause_value!r}")

    explanation = str(payload.get("explanation", "")).strip()
    if len(explanation) < 10:
        raise ValueError("explanation 过短")
    if len(explanation) > MAX_EXPLANATION_CHARS:
        raise ValueError("explanation 过长")

    refs = payload.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        raise ValueError("evidence_refs 必须是非空数组")
    normalised: list[int] = []
    for ref in refs:
        if not isinstance(ref, int) or isinstance(ref, bool):
            raise ValueError(f"evidence_refs 含非整数：{ref!r}")
        if not 0 <= ref < len(conflict.evidence):
            raise ValueError(f"evidence_refs 引用了不存在的证据编号：{ref}")
        normalised.append(ref)

    experiment = str(payload.get("discriminating_experiment", "")).strip()
    if len(experiment) < 5:
        raise ValueError("discriminating_experiment 过短")

    return Explanation(
        cause=ConflictCause(cause_value),
        explanation=explanation,
        evidence_refs=normalised,
        discriminating_experiment=experiment,
        confidence=_coerce_float(payload.get("confidence", 0.0), "confidence"),
        source="llm",
    )


def fallback_explanation(conflict: Conflict) -> Explanation:
    """规则层兜底。不调用模型，只用已算出的候选原因。"""
    cause = conflict.cause_candidates[0]
    refs = list(range(len(conflict.evidence)))
    return Explanation(
        cause=cause,
        explanation=(
            f"未能获得模型解释，以下为规则层的候选判断：{CAUSE_LABEL_ZH[cause]}。"
            f"该结论未经模型复核，仅供参考。"
        ),
        evidence_refs=refs,
        discriminating_experiment=conflict.suggested_experiment or "需要补充实验以区分不同解释。",
        confidence=0.3,
        source="fallback",
    )


def explain_conflict(
    conflict: Conflict,
    provider: LLMProvider,
    gene: str,
    cancer: str,
    max_attempts: int = 2,
) -> Explanation:
    """解释单条冲突。失败时降级到规则层，不抛异常。"""
    user_prompt = build_user_prompt(conflict, gene, cancer)
    prompt_hash = hashlib.sha256((SYSTEM_PROMPT + user_prompt).encode("utf-8")).hexdigest()[:16]

    meta: dict[str, Any] = {
        "provider": getattr(provider, "name", "unknown"),
        "prompt_hash": prompt_hash,
        "attempts": 0,
    }

    attempt_prompt = user_prompt
    for attempt in range(1, max_attempts + 1):
        meta["attempts"] = attempt
        try:
            response = provider.complete(SYSTEM_PROMPT, attempt_prompt, json_mode=True)
            meta["model"] = response.model
            meta["latency_ms"] = response.latency_ms
            payload = extract_json(response.text)
            result = validate(payload, conflict)
        except (LLMError, ValueError) as exc:
            meta["last_error"] = str(exc)
            log.warning("冲突 %s 第 %d 次解释失败：%s", conflict.rule_id, attempt, exc)
            if attempt < max_attempts:
                # 把失败原因回传，让模型有机会自行修正
                attempt_prompt = (
                    user_prompt
                    + f"\n\n上一次输出未通过校验，原因：{exc}\n请修正后重新输出一个 JSON 对象。"
                )
            continue

        result.source = "llm"
        meta["source"] = "llm"
        conflict.explanation = result.explanation
        conflict.suggested_experiment = result.discriminating_experiment
        conflict.metadata = meta
        return result

    result = fallback_explanation(conflict)
    meta["source"] = "fallback"
    conflict.explanation = result.explanation
    conflict.suggested_experiment = result.discriminating_experiment
    conflict.metadata = meta
    return result


def explain_conflicts(
    conflicts: list[Conflict],
    provider: LLMProvider,
    gene: str,
    cancer: str,
) -> list[Explanation]:
    """批量解释，原地填充 conflict 的解释字段。"""
    return [explain_conflict(c, provider, gene, cancer) for c in conflicts]
