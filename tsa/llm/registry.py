"""服务商预设与工厂函数。

预设只是省去手打端点，任何时候都可以用 base_url 覆盖。
模型名不写死 —— 各家型号迭代很快，统一由配置指定。

用法：
    # 环境变量方式
    TSA_LLM_PROVIDER=deepseek TSA_LLM_MODEL=deepseek-chat TSA_LLM_API_KEY=sk-...

    # 代码方式
    build_provider(LLMConfig(provider="moonshot", model="...", api_key="..."))
"""

from __future__ import annotations

from .base import LLMConfig, LLMError, LLMProvider
from .openai_compat import OpenAICompatProvider
from .stub import StubProvider

# 这些端点都是各家提供的 OpenAI 兼容入口。若某家的入口有变动，
# 直接传 base_url 覆盖即可，不影响其他部分。
PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    "deepseek": {"base_url": "https://api.deepseek.com/v1"},
    "moonshot": {"base_url": "https://api.moonshot.cn/v1"},
    "zhipu": {"base_url": "https://open.bigmodel.cn/api/paas/v4"},
    "siliconflow": {"base_url": "https://api.siliconflow.cn/v1"},
    "openai": {"base_url": "https://api.openai.com/v1"},
    "ollama": {"base_url": "http://localhost:11434/v1", "api_key": "ollama"},
    "vllm": {"base_url": "http://localhost:8000/v1", "api_key": "none"},
}


def resolve_base_url(config: LLMConfig) -> str | None:
    if config.base_url:
        return config.base_url
    preset = PROVIDER_PRESETS.get(config.provider.lower())
    return preset.get("base_url") if preset else None


def build_provider(config: LLMConfig) -> LLMProvider:
    """按配置构造 provider。

    provider 取值为 "stub" 时返回确定性兜底；
    取值为预设名（qwen / deepseek / moonshot / ...）时使用对应的端点；
    取值为 "openai_compat" 时必须显式提供 base_url。
    """
    name = (config.provider or "stub").lower()

    if name == "stub":
        return StubProvider()

    if name not in PROVIDER_PRESETS and name != "openai_compat":
        raise LLMError(
            f"未知的 provider：{config.provider}。"
            f"可用取值：stub, openai_compat, {', '.join(sorted(PROVIDER_PRESETS))}"
        )

    preset = PROVIDER_PRESETS.get(name, {})
    resolved = LLMConfig(
        provider=name,
        model=config.model,
        base_url=resolve_base_url(config),
        api_key=config.api_key or preset.get("api_key"),
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout=config.timeout,
        native_json_mode=config.native_json_mode,
    )
    if not resolved.base_url:
        raise LLMError(f"provider {config.provider} 需要提供 base_url")
    if not resolved.model:
        raise LLMError(f"provider {config.provider} 需要提供 model 名称")
    return OpenAICompatProvider(resolved)
