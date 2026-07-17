"""默认配置 — 对标 TradingAgents 的 default_config.py。

敏感信息（API Key）和通用设置（模型名、超时等）全部通过 .env 管理。
.env 文件读取后由 MATHMODELING_ 前缀的环境变量覆盖。
"""

import json
import os
from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str | None = None) -> str | None:
    """读取环境变量，前缀 MATHMODELING_。"""
    return os.getenv(f"MATHMODELING_{key.upper()}", default)


def _parse_layer_overrides(env_val: str | None) -> dict:
    """解析 MATHMODELING_LAYER_MODEL_OVERRIDES JSON 字符串。

    用于覆盖各层各角色的模型分配，与代码中的 layer_model_overrides
    做 deep merge（env 中的条目追加/覆盖代码默认值）。

    示例:
        MATHMODELING_LAYER_MODEL_OVERRIDES={"paper":{"writer":"qwen3.7-max","manager":"deepseek-v4-pro"},"modeling":{"agent":"deepseek-v4-pro"}}
    """
    if not env_val:
        return {}
    try:
        return json.loads(env_val)
    except json.JSONDecodeError as e:
        import logging
        logging.getLogger(__name__).warning(f"无法解析 MATHMODELING_LAYER_MODEL_OVERRIDES: {e}")
        return {}


# ── 默认的层模型分配 ──
_DEFAULT_LAYER_MODEL_OVERRIDES: dict = {
    # ── Layer 1: 问题分析 ──
    "problem": {
        "agent": "deepseek-v4-flash",
        "manager": "deepseek-v4-pro",
    },
    # ── Layer 2: 数学建模 ──
    "modeling": {
        "agent": "deepseek-v4-pro",
        "manager": "deepseek-v4-pro",
    },
    # ── Layer 3: 代码实现 ──
    "implementation": {
        "algorithm": "deepseek-v4-flash",
        "coder": "deepseek-v4-pro",
        "visualizer": "deepseek-v4-flash",
        "manager": "deepseek-v4-pro",
    },
    # ── Layer 4: 论文写作 ──
    "paper": {
        "architect": "deepseek-v4-flash",
        "writer": "qwen3.7-max",
        "visualizer": "deepseek-v4-flash",
        "manager": "deepseek-v4-pro",
    },
    # ── Layer 5: 敏感性分析 ──
    "sensitivity": {
        "agent": "deepseek-v4-flash",
        "manager": "deepseek-v4-pro",
    },
}

# 构建最终 layer_model_overrides（env JSON 覆盖代码默认）
_base_overrides = _DEFAULT_LAYER_MODEL_OVERRIDES.copy()
_env_overrides = _parse_layer_overrides(_env("layer_model_overrides"))
for _layer_name, _layer_roles in _env_overrides.items():
    if _layer_name not in _base_overrides:
        _base_overrides[_layer_name] = {}
    _base_overrides[_layer_name].update(_layer_roles)
LAYER_MODEL_OVERRIDES: dict = _base_overrides


DEFAULT_CONFIG: dict = {
    # ═══════════════════════════════════════════════
    # LLM Provider
    # ═══════════════════════════════════════════════
    "llm_provider": _env("llm_provider", "opencode"),
    "backend_url": _env("backend_url"),

    # ═══════════════════════════════════════════════
    # 降级链 — 主 provider 不可用时自动切换
    # ═══════════════════════════════════════════════
    "fallback_provider": _env("fallback_provider", "deepseek"),
    "fallback_base_url": _env("fallback_base_url"),

    # ═══════════════════════════════════════════════
    # 模型名 — 按 provider 分配
    #   OpenCode 模式: 使用 layer_model_overrides
    #   DeepSeek 模式: 使用 deep_think_llm / quick_think_llm
    # ═══════════════════════════════════════════════
    "deep_think_llm": _env("deep_think_llm", "deepseek-v4-pro"),
    "quick_think_llm": _env("quick_think_llm", "deepseek-v4-flash"),
    "layer_model_overrides": LAYER_MODEL_OVERRIDES,

    # ═══════════════════════════════════════════════
    # 超时配置（每层，秒）— 不限时间，确保推理模型完整跑完
    # ═══════════════════════════════════════════════
    "layer_timeouts": {
        "problem": int(_env("layer_timeout_problem", "10800")),
        "modeling": int(_env("layer_timeout_modeling", "10800")),
        "implementation": int(_env("layer_timeout_implementation", "10800")),
        "paper": int(_env("layer_timeout_paper", "10800")),
        "sensitivity": int(_env("layer_timeout_sensitivity", "10800")),
    },

    # ═══════════════════════════════════════════════
    # 生成参数
    # ═══════════════════════════════════════════════
    "default_max_tokens": int(_env("default_max_tokens", "16384")),
    "default_temperature": 0.2,  # 全局默认值，按角色覆盖见下
    "max_tokens_overrides": {
        # agent_name 或 role → max_tokens 覆盖（当前全部使用统一上限）
    },
    "temperature_overrides": {
        # role → temperature 覆盖（优先级：agent_name > role > default）
        # 原则：一致性高的用低温，需要辩论多样性的用中温
        "manager": 0.1,         # 裁决需要稳定，同输入同输出
        "coder": 0.0,           # 代码必须是确定性的
        "algorithm": 0.1,       # 算法设计接近代码，稳定为主
        "visualizer": 0.1,      # 图表生成需要一致
        "architect": 0.3,       # 论文大纲可有些微调
        "writer": 0.5,          # 正文需要表达多样性
    },

    # ═══════════════════════════════════════════════
    # 辩论配置
    # ═══════════════════════════════════════════════
    "max_debate_rounds": int(_env("max_debate_rounds", "10")),
    "max_impl_retries": int(_env("max_impl_retries", "3")),

    # ═══════════════════════════════════════════════
    # 层控制
    # ═══════════════════════════════════════════════
    "enable_sensitivity": _env("enable_sensitivity", "false").lower() == "true",
    "selected_layers": [
        int(s.strip())
        for s in _env("selected_layers", "1,2,3,4").split(",")
        if s.strip().isdigit()
    ],

    # ═══════════════════════════════════════════════
    # 输出配置
    # ═══════════════════════════════════════════════
    "output_dir": _env("output_dir"),
    "output_format": _env("output_format", "obsidian_md"),
    "data_cache_dir": _env("data_cache_dir", "./cache"),
    "results_dir": _env("results_dir", "./results"),

    # ═══════════════════════════════════════════════
    # 计算沙盒配置
    # ═══════════════════════════════════════════════
    "code_execution": {
        "enabled": True,
        "timeout": int(_env("code_timeout", "30")),
        "max_memory_mb": int(_env("code_max_memory_mb", "512")),
        "allowed_modules": [
            "numpy", "scipy", "sympy", "pandas",
            "matplotlib", "sklearn", "statsmodels", "seaborn",
        ],
        "save_artifacts": True,
    },
    "enforce_code_computation": _env("enforce_code_computation", "true").lower() == "true",
}
