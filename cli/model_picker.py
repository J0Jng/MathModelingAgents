"""CLI 交互式模型（agent）选择器。

在 main.py 进入 propagate() 之前调用。根据 config["llm_provider"] 决定是否
弹出两个 questionary.select（Quick-Thinking / Deep-Thinking）。选中 Custom 时
二级输入模型 ID。返回「变更字典」交给调用方写回 config，本模块不直接改 config。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 角色 → 思考深度分类（仅用于本模块内部）。
_DEEP_ROLES = {"manager", "writer", "coder", "algorithm"}

# 允许通过环境变量跳过交互（对齐 TradingAgents env-precedence 规则）。
_QUICK_VAR = "MATHMODELING_QUICK_THINK_LLM"
_DEEP_VAR = "MATHMODELING_DEEP_THINK_LLM"


def _pick_single(mode: str, provider: str) -> str | None:
    """弹单个 questionary.select；返回模型 id 或 None（Ctrl-C/取消）。"""
    import os

    env_var = _QUICK_VAR if mode == "quick" else _DEEP_VAR
    env_val = os.getenv(env_var)
    if env_val:
        logger.info("%s 已设置，跳过 %s 选择", env_var, mode)
        return None
    raise NotImplementedError("交互选择由 Task 4 实现")


def prompt_model_selection(config: dict) -> dict:
    """主入口：根据 provider 弹菜单，返回应写回 config 的变更字典。"""
    provider = config.get("llm_provider", "opencode")
    return {}  # Task 4 填充完整逻辑


def _apply_model_selection(config: dict, provider: str, chosen: dict) -> dict:
    """把 chosen={'quick':..,'deep':..} 应用到 config 副本，返回修改后的副本。

    非 deepseek provider：覆盖 layer_model_overrides 里各层的 agent/coder/writer 等
    角色为所选模型（保持 provider_layer_model_overrides 可能存在的角色覆盖不被抹掉，
    仅当全局覆盖命中时才改写）。deepseek：覆盖 deep_think_llm / quick_think_llm。
    """
    new = dict(config)
    if provider == "deepseek":
        new = dict(config)
        if chosen.get("deep"):
            new["deep_think_llm"] = chosen["deep"]
        if chosen.get("quick"):
            new["quick_think_llm"] = chosen["quick"]
        return new

    # 非 deepseek：改写 layer_model_overrides 中出现的所有角色值为所选模型，
    # 但保留 provider 级角色覆盖（provider_layer_model_overrides）优先。实现时注意
    # 空 config 防御：layer_model_overrides 可能未存在，或用默认 DEFAULT_CONFIG。
    quick = chosen.get("quick")
    deep = chosen.get("deep")
    overrides = dict(config.get("layer_model_overrides") or {})
    for layer, roles in overrides.items():
        roles = dict(roles)
        for role in list(roles):
            replacement = deep if role in _DEEP_ROLES else quick
            if replacement:
                roles[role] = replacement
        overrides[layer] = roles
    new["layer_model_overrides"] = overrides
    return new
