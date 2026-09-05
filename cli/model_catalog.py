"""各 provider 的可选模型目录，供 CLI 交互式模型（agent）选择使用。

对标 TradingAgents 的 model_catalog.py：纯数据，无副作用。
每个条目标签 (display_name, model_id)。Quick = 快速思考，Deep = 深度思考。

模型池按官方平台清单（2026-09）维护，实时拉取（/models 端点）成功时优先。
"""

from __future__ import annotations

ModelOption = tuple[str, str]
ProviderModeOptions = dict[str, dict[str, list[ModelOption]]]

# DeepSeek 官方 API — 仅 flash/pro 两模型。
_DEEPSEEK_MODELS: dict[str, list[ModelOption]] = {
    "quick": [
        ("DeepSeek V4 Pro - 深度思考", "deepseek-v4-pro"),
        ("DeepSeek V4 Flash - 快速思考", "deepseek-v4-flash"),
    ],
    "deep": [
        ("DeepSeek V4 Pro - 深度思考", "deepseek-v4-pro"),
        ("DeepSeek V4 Flash - 快速（降级用）", "deepseek-v4-flash"),
    ],
}

# =========================================================================
# 火山方舟 Coding Plan（9 个，2026-09 官方清单）
# 端点 /api/coding/v3；quick/deep 由轻量到旗舰划分。
# =========================================================================
_ARK_CODING_MODELS: dict[str, list[ModelOption]] = {
    "quick": [
        ("ark-code-latest - 控制台选择模型（默认）", "ark-code-latest"),
        ("kimi-k2.7-code - Kimi code", "kimi-k2.7-code"),
        ("minimax-m3 - MiniMax M3", "minimax-m3"),
        ("doubao-seed-2.0-lite - Lite Doubao", "doubao-seed-2.0-lite"),
        ("doubao-seed-2.1-turbo - Doubao turbo", "doubao-seed-2.1-turbo"),
        ("deepseek-v4-flash - 快速 DeepSeek", "deepseek-v4-flash"),
        ("deepseek-v4-pro - DeepSeek Pro", "deepseek-v4-pro"),
        ("glm-5.3-flash - 轻量 GLM", "glm-5.3-flash"),
        ("glm-5.3 - GLM flagship", "glm-5.3"),
    ],
    "deep": [
        ("ark-code-latest - 控制台选择模型（默认）", "ark-code-latest"),
        ("kimi-k2.7-code - Kimi code", "kimi-k2.7-code"),
        ("minimax-m3 - MiniMax M3", "minimax-m3"),
        ("doubao-seed-2.0-lite - Lite Doubao", "doubao-seed-2.0-lite"),
        ("doubao-seed-2.1-turbo - Doubao turbo", "doubao-seed-2.1-turbo"),
        ("deepseek-v4-flash - 快速 DeepSeek", "deepseek-v4-flash"),
        ("deepseek-v4-pro - DeepSeek Pro", "deepseek-v4-pro"),
        ("glm-5.3-flash - 轻量 GLM", "glm-5.3-flash"),
        ("glm-5.3 - GLM flagship", "glm-5.3"),
    ],
}

# =========================================================================
# 火山方舟 Agent Plan（11 个，2026-09 官方清单）
# 端点 /api/plan/v3；比 Coding 多 doubao-seed-2.0-mini 与 kimi-k3。
# =========================================================================
_ARK_AGENT_MODELS: dict[str, list[ModelOption]] = {
    "quick": [
        ("ark-code-latest - 控制台选择模型（默认）", "ark-code-latest"),
        ("doubao-seed-2.0-lite - Lite Doubao", "doubao-seed-2.0-lite"),
        ("doubao-seed-2.0-mini - Mini Doubao", "doubao-seed-2.0-mini"),
        ("doubao-seed-2.1-turbo - Doubao turbo", "doubao-seed-2.1-turbo"),
        ("kimi-k2.7-code - Kimi code", "kimi-k2.7-code"),
        ("kimi-k3 - Kimi（⚠️ 仅支持 medium 及以上套餐；当前火爆，遇卡顿建议切换其他模型）", "kimi-k3"),
        ("minimax-m3 - MiniMax M3", "minimax-m3"),
        ("deepseek-v4-flash - 快速 DeepSeek", "deepseek-v4-flash"),
        ("deepseek-v4-pro - DeepSeek Pro", "deepseek-v4-pro"),
        ("glm-5.3-flash - 轻量 GLM", "glm-5.3-flash"),
        ("glm-5.3 - GLM flagship", "glm-5.3"),

    ],
    "deep": [
        ("ark-code-latest - 控制台选择模型（默认）", "ark-code-latest"),
        ("doubao-seed-2.0-lite - Lite Doubao", "doubao-seed-2.0-lite"),
        ("doubao-seed-2.0-mini - Mini Doubao", "doubao-seed-2.0-mini"),
        ("doubao-seed-2.1-turbo - Doubao turbo", "doubao-seed-2.1-turbo"),
        ("kimi-k2.7-code - Kimi code", "kimi-k2.7-code"),
        ("kimi-k3 - Kimi（⚠️ 仅支持 medium 及以上套餐；当前火爆，遇卡顿建议切换其他模型）", "kimi-k3"),
        ("minimax-m3 - MiniMax M3", "minimax-m3"),
        ("deepseek-v4-flash - 快速 DeepSeek", "deepseek-v4-flash"),
        ("deepseek-v4-pro - DeepSeek Pro", "deepseek-v4-pro"),
        ("glm-5.3-flash - 轻量 GLM", "glm-5.3-flash"),
        ("glm-5.3 - GLM flagship", "glm-5.3"),
    ],
}

# =========================================================================
# OpenCode Go 网关 — 官方平台 24 个模型（2026-09），实测可用 21 个。
# 用户拍板：quick 与 deep 两栏都完整列全部模型（展示名 = id 的易读形式）。
# 已移除实测不可用 3 个（2026-09-04 chat 探针验证）:
#   - grok-4.6        → 401 ModelError: not supported for format oa-compat
#   - gpt-5.6-luna    → 500 Internal server error
#   - minimax-m2.7    → 500 Internal server error
# 注：hy4-preview 实测可正常回复（此前去 max_tokens 限制后 200 返回正文）。
# =========================================================================
_OPENCODE_MODELS: dict[str, list[ModelOption]] = {
    "quick": [
        ("GLM-5.3-Flash", "glm-5.3-flash"),
        ("GLM-5.3", "glm-5.3"),
        ("GLM-5.2", "glm-5.2"),
        ("GLM-5.1", "glm-5.1"),
        ("Kimi K3", "kimi-k3"),
        ("Kimi K2.7 Code", "kimi-k2.7-code"),
        ("Kimi K2.6", "kimi-k2.6"),
        ("LongCat-2.0", "longcat-2.0"),
        ("MiMo-V2.5", "mimo-v2.5"),
        ("MiMo-V2.5-Pro", "mimo-v2.5-pro"),
        ("MiniMax M3", "minimax-m3"),
        ("Qwen3.8 Max", "qwen3.8-max"),
        ("Qwen3.8 Flash", "qwen3.8-flash"),
        ("Qwen3.7 Max", "qwen3.7-max"),
        ("Qwen3.7 Plus", "qwen3.7-plus"),
        ("Qwen3.6 Plus", "qwen3.6-plus"),
        ("DeepSeek V4 Pro", "deepseek-v4-pro"),
        ("DeepSeek V4 Flash", "deepseek-v4-flash"),
        ("DeepSeek V4 Flash Vision Exp", "deepseek-v4-flash-vision-exp"),
        ("Hy4 preview", "hy4-preview"),
        ("Hy3", "hy3"),
    ],
    "deep": [
        ("GLM-5.3-Flash", "glm-5.3-flash"),
        ("GLM-5.3", "glm-5.3"),
        ("GLM-5.2", "glm-5.2"),
        ("GLM-5.1", "glm-5.1"),
        ("Kimi K3", "kimi-k3"),
        ("Kimi K2.7 Code", "kimi-k2.7-code"),
        ("Kimi K2.6", "kimi-k2.6"),
        ("LongCat-2.0", "longcat-2.0"),
        ("MiMo-V2.5", "mimo-v2.5"),
        ("MiMo-V2.5-Pro", "mimo-v2.5-pro"),
        ("MiniMax M3", "minimax-m3"),
        ("Qwen3.8 Max", "qwen3.8-max"),
        ("Qwen3.8 Flash", "qwen3.8-flash"),
        ("Qwen3.7 Max", "qwen3.7-max"),
        ("Qwen3.7 Plus", "qwen3.7-plus"),
        ("Qwen3.6 Plus", "qwen3.6-plus"),
        ("DeepSeek V4 Pro", "deepseek-v4-pro"),
        ("DeepSeek V4 Flash", "deepseek-v4-flash"),
        ("DeepSeek V4 Flash Vision Exp", "deepseek-v4-flash-vision-exp"),
        ("Hy4 preview", "hy4-preview"),
        ("Hy3", "hy3"),
    ],
}

MODEL_OPTIONS: ProviderModeOptions = {
    "opencode": _OPENCODE_MODELS,
    "deepseek": _DEEPSEEK_MODELS,
    "volcengine": _ARK_CODING_MODELS,
    "volcengine-plan": _ARK_AGENT_MODELS,
}


def get_model_options(provider: str, mode: str) -> list[ModelOption]:
    """返回某 provider 在 quick/deep 模式下的模型选项列表。未知 provider 返回空列表。"""
    return MODEL_OPTIONS.get(provider.lower(), {}).get(mode, [])