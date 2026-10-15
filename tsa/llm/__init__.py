"""大语言模型接入层。

设计目标：换模型只改配置，不改代码。

系统不绑定任何一家服务商。绝大多数主流服务（阿里百炼、DeepSeek、Moonshot、
智谱、OpenAI、SiliconFlow，以及本地的 Ollama、vLLM）都提供 OpenAI 兼容的
/chat/completions 接口，所以只需要一个 OpenAICompatProvider，加上一张
端点预设表，就能覆盖它们。

另外提供 StubProvider 作为确定性兜底：不调用任何模型，直接把规则层算出的
候选原因返回。它保证没有 API key 时整条管线仍然可运行、可复现。
"""

from .base import LLMConfig, LLMError, LLMProvider, LLMResponse, extract_json
from .openai_compat import OpenAICompatProvider
from .registry import PROVIDER_PRESETS, build_provider
from .stub import StubProvider

__all__ = [
    "LLMConfig",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "OpenAICompatProvider",
    "PROVIDER_PRESETS",
    "StubProvider",
    "build_provider",
    "extract_json",
]
