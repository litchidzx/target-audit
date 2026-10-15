"""确定性兜底实现：不调用任何模型。

它的存在有两个作用：
  1. 没有 API key 时，整条管线仍可运行、可演示、可测试；
  2. 任何真实模型调用失败或输出不合格时，系统降级到这里，而不是中断。

返回的是规则层已经算出的候选原因，所以内容朴素但不会无中生有。
"""

from __future__ import annotations

import json

from .base import LLMResponse


class StubProvider:
    name = "stub"

    def __init__(self, cause: str | None = None):
        # 允许测试指定固定的返回内容
        self.cause = cause

    def complete(self, system: str, user: str, json_mode: bool = False) -> LLMResponse:
        try:
            request = json.loads(user)
        except json.JSONDecodeError:
            request = {}

        candidates = request.get("candidate_causes") or []
        first = candidates[0] if candidates else None
        # 提示词里候选原因是 {"value": ..., "description": ...} 的形式，
        # 这里必须取值而不是整个字典，否则兜底输出会连自己的校验都过不了。
        if isinstance(first, dict):
            first = first.get("value")
        cause = self.cause or first or ""
        refs = [item.get("index", 0) for item in request.get("evidence", []) if isinstance(item, dict)]
        payload = {
            "cause": cause,
            "explanation": (
                f"依据规则层的候选原因给出初步解释：{cause}。"
                f"该判断来自证据方向的一致性分析，未经模型复核。"
            ),
            "evidence_refs": refs[:3],
            "discriminating_experiment": "按规则层建议的实验设计进行区分。",
            "confidence": 0.3,
        }
        return LLMResponse(text=json.dumps(payload, ensure_ascii=False), model="stub")
