"""模型接入的公共定义。

只有两件事需要在所有服务商之间统一：
  1. 一次调用长什么样（system + user -> 文本）
  2. 怎么从返回的文本里可靠地取出 JSON

第 2 件事是不绑定服务商的关键。很多服务支持原生 JSON 模式，但支持程度和
参数名各不相同；如果依赖它们，换一家就要改代码。这里的做法是自己做容错解析，
所以即使对方只返回一段带解释文字的普通文本，也能跑通。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol


class LLMError(RuntimeError):
    """模型不可用或调用失败。调用方应当降级，而不是中断。"""


@dataclass
class LLMConfig:
    """一次接入所需的全部配置。provider 名决定用哪个实现。"""

    provider: str = "stub"
    model: str = ""
    base_url: str | None = None
    api_key: str | None = None
    temperature: float = 0.0
    max_tokens: int = 1024
    timeout: int = 60
    # 是否向服务端请求原生 JSON 模式。默认关闭，因为各家参数名不一致，
    # 靠 extract_json 做容错解析更稳。
    native_json_mode: bool = False

    @classmethod
    def from_env(cls, prefix: str = "TSA_LLM_") -> "LLMConfig":
        """从环境变量读取配置，便于在不同服务商之间切换。

            TSA_LLM_PROVIDER=deepseek
            TSA_LLM_MODEL=deepseek-chat
            TSA_LLM_API_KEY=sk-...
        """

        def get(name: str, default: Any = None) -> Any:
            return os.environ.get(prefix + name, default)

        return cls(
            provider=get("PROVIDER", "stub"),
            model=get("MODEL", ""),
            base_url=get("BASE_URL") or None,
            api_key=get("API_KEY") or None,
            temperature=float(get("TEMPERATURE", 0.0)),
            max_tokens=int(get("MAX_TOKENS", 1024)),
            timeout=int(get("TIMEOUT", 60)),
            native_json_mode=get("NATIVE_JSON", "").lower() in {"1", "true", "yes"},
        )


@dataclass
class LLMResponse:
    text: str
    model: str = ""
    latency_ms: int = 0


class LLMProvider(Protocol):
    """接入层唯一的接口。实现这个协议就能被系统使用。"""

    name: str

    def complete(self, system: str, user: str, json_mode: bool = False) -> LLMResponse:
        """返回模型生成的文本。失败时抛 LLMError。"""
        ...


def _scan_balanced_object(text: str) -> str | None:
    """从任意文本里扫出第一个完整的 JSON 对象。

    按括号配对扫描，并且跳过字符串内部的括号，避免把正文里的花括号当成结构。
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        start = text.find("{", start + 1)
    return None


def extract_json(text: str) -> Any:
    """从模型输出里取出 JSON，容忍三种常见形态。

    1. 纯 JSON
    2. 被 ``` 或 ```json 代码块包住
    3. 前后带解释文字

    这个函数的存在，使系统不依赖任何服务商的原生结构化输出能力。
    """
    if not text or not text.strip():
        raise LLMError("模型返回为空")

    candidates: list[str] = []
    stripped = text.strip()
    candidates.append(stripped)

    # 去掉 markdown 代码块围栏
    if stripped.startswith("```"):
        body = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        if body.rstrip().endswith("```"):
            body = body.rstrip()[:-3]
        candidates.append(body.strip())

    # 括号配对扫描
    scanned = _scan_balanced_object(stripped)
    if scanned:
        candidates.append(scanned)

    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    raise LLMError(f"无法从模型输出中解析出 JSON，原始内容前 200 字：{text[:200]}")
