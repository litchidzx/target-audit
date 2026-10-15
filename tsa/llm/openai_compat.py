"""OpenAI 兼容接口的通用实现。

绝大多数服务商都提供 /chat/completions 这一套接口，包括：
  阿里百炼、DeepSeek、Moonshot、智谱、OpenAI、SiliconFlow，
  以及本地的 Ollama、vLLM、LM Studio。

所以这里只写一个实现就够了，服务商之间的差异全部落在 base_url 和 model 上。
用的是标准库 urllib，不引入额外依赖。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .base import LLMConfig, LLMError, LLMResponse

USER_AGENT = "target-selectivity-audit/0.1"


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self, config: LLMConfig):
        if not config.base_url:
            raise LLMError("缺少 base_url")
        self.config = config
        self.endpoint = config.base_url.rstrip("/") + "/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _body(self, system: str, user: str, json_mode: bool) -> dict:
        body: dict = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if json_mode and self.config.native_json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    def complete(self, system: str, user: str, json_mode: bool = False) -> LLMResponse:
        payload = json.dumps(self._body(system, user, json_mode)).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint, data=payload, headers=self._headers(), method="POST"
        )
        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise LLMError(f"HTTP {exc.code}：{detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise LLMError(f"调用失败：{exc}") from exc

        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"返回结构与预期不符：{str(data)[:200]}") from exc

        return LLMResponse(
            text=text,
            model=data.get("model", self.config.model),
            latency_ms=int((time.time() - started) * 1000),
        )
