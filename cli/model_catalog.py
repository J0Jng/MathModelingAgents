"""各 provider 的可选模型目录，供 CLI 交互式模型（agent）选择使用。

对标 TradingAgents 的 model_catalog.py：纯数据，无副作用。
每个条目标签 (display_name, model_id)。Quick = 快速思考，Deep = 深度思考。
"""

from __future__ import annotations

ModelOption = tuple[str, str]
ProviderModeOptions = dict[str, dict[str, list[ModelOption]]]

_CUSTOM_ONLY: dict[str, list[ModelOption]] = {
    "quick": [("Custom model ID", "custom")],
    "deep": [("Custom model ID", "custom")],
}

# DeepSeek 官方 API — 仅 flash/pro 两模型。
_DEEPSEEK_MODELS: dict[str, list[ModelOption]] = {
    "quick": [
        ("DeepSeek V4 Flash - 快速思考", "deepseek-v4-flash"),
        ("Custom model ID", "custom"),
    ],
    "deep": [
        ("DeepSeek V4 Pro - 深度思考", "deepseek-v4-pro"),
        ("DeepSeek V4 Flash - 快速（降级用）", "deepseek-v4-flash"),
        ("Custom model ID", "custom"),
    ],
}

# Volcengine / Volcengine-Plan — 取 README 记录的原生可用模型池。
# Coding Plan 与 Agent Plan 端点不同，但模型名集合大部分重叠。
_ARK_CODE_MODELS: dict[str, list[ModelOption]] = {
    "quick": [
        ("ark-code-latest - 控制台选择模型（默认）", "ark-code-latest"),
        ("deepseek-v4-flash - 快速 DeepSeek", "deepseek-v4-flash"),
        ("doubao-seed-2.0-lite - Lite Doubao", "doubao-seed-2.0-lite"),
        ("glm-5.3-flash - 轻量 GLM", "glm-5.3-flash"),
        ("Custom model ID", "custom"),
    ],
    "deep": [
        ("ark-code-latest - 控制台选择模型（默认）", "ark-code-latest"),
        ("doubao-seed-2.1-turbo - Doubao turbo", "doubao-seed-2.1-turbo"),
        ("deepseek-v4-pro - DeepSeek Pro", "deepseek-v4-pro"),
        ("deepseek-v4-flash - 快速 DeepSeek", "deepseek-v4-flash"),
        ("kimi-k2.7-code - Kimi code", "kimi-k2.7-code"),
        ("minimax-m3 - MiniMax M3", "minimax-m3"),
        ("glm-5.3 - GLM flagship", "glm-5.3"),
        ("Custom model ID", "custom"),
    ],
}

# OpenCode Go 网关 — 模型池丰富多变，参考 TradingAgents 用 Custom 为主，
# 但保留项目已知稳定的几个常用于 .env。
_OPENCODE_MODELS: dict[str, list[ModelOption]] = {
    "quick": [
        ("deepseek-v4-flash - 快速 DeepSeek（默认）", "deepseek-v4-flash"),
        ("Custom model ID", "custom"),
    ],
    "deep": [
        ("deepseek-v4-pro - 深度推理（默认）", "deepseek-v4-pro"),
        ("Custom model ID", "custom"),
    ],
}

MODEL_OPTIONS: ProviderModeOptions = {
    "opencode": _OPENCODE_MODELS,
    "deepseek": _DEEPSEEK_MODELS,
    "volcengine": _ARK_CODE_MODELS,
    "volcengine-plan": _ARK_CODE_MODELS,
}


def get_model_options(provider: str, mode: str) -> list[ModelOption]:
    """返回某 provider 在 quick/deep 模式下的模型选项列表。未知 provider 回退 Custom-only。"""
    return MODEL_OPTIONS.get(provider.lower(), _CUSTOM_ONLY).get(mode, _CUSTOM_ONLY[mode])
