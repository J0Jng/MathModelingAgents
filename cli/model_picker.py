"""CLI 交互式模型（agent）选择器。

在 main.py 进入 propagate() 之前调用。根据 config["llm_provider"] 决定是否
弹出两个 questionary.select（Quick-Thinking / Deep-Thinking）。选中 Custom 时
二级输入模型 ID。返回「变更字典」交给调用方写回 config，本模块不直接改 config。
"""

from __future__ import annotations

import logging
import os

import questionary
from rich.console import Console

from cli.model_catalog import MODEL_OPTIONS, get_model_options  # 静态降级清单
from cli.model_fetcher import fetch_models  # 实时拉取

logger = logging.getLogger(__name__)

console = Console()

# 角色 → 思考深度分类（仅用于本模块内部）。
_DEEP_ROLES = {"manager", "writer", "coder", "algorithm"}

# 允许通过环境变量跳过交互（对齐 TradingAgents env-precedence 规则）。
_QUICK_VAR = "MATHMODELING_QUICK_THINK_LLM"
_DEEP_VAR = "MATHMODELING_DEEP_THINK_LLM"


def _is_env_locked(mode: str) -> bool:
    return bool(os.getenv(_QUICK_VAR if mode == "quick" else _DEEP_VAR))


def _build_options(provider: str, mode: str) -> list[tuple[str, str]]:
    """菜单项：优先实时拉取，失败降级静态清单，末尾恒有 Custom。"""
    realtime = fetch_models(provider)
    if realtime:
        # 实时列表：可为空但非空时全部展示；deep/quick 暂不区分(模型自含思考能力)
        options = [(m, m) for m in realtime]
    else:
        options = list(get_model_options(provider, mode))
    # 始终追加 Custom 兜底,去重
    if not any(mid == "custom" for _, mid in options):
        options.append(("Custom model ID", "custom"))
    return options


def _pick_single(mode: str, provider: str) -> str | None:
    """弹单个 questionary.select。返回模型 id；None 表示 env 锁定或取消。"""
    if _is_env_locked(mode):
        return None
    options = _build_options(provider, mode)
    choice = questionary.select(
        f"Select Your [{mode.title()}-Thinking] LLM Engine ({provider}):",
        choices=[questionary.Choice(display, value=value) for display, value in options],
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style([
            ("selected", "fg:magenta noinherit"),
            ("highlighted", "fg:magenta noinherit"),
            ("pointer", "fg:magenta noinherit"),
        ]),
    ).ask()
    if choice is None:
        return None  # Ctrl-C / Esc
    if choice == "custom":
        custom = questionary.text(
            "Enter custom model ID:",
            validate=lambda x: len(x.strip()) > 0 or "Please enter a model ID.",
        ).ask()
        return custom.strip() if custom else None
    return choice


def prompt_model_selection(config: dict) -> dict:
    """主入口：根据 provider 弹 Quick/Deep 菜单，返回变更字典。

    - provider 非支持类型 → 返回 {}（不阻塞，向后兼容）。
    - 任一 env（QUICK/DEEP）已设置 → 返回 {}（env 已接管，避免无 TTY 卡死）。
    - 用户取消任意一次 → 返回 {}（保持当前配置）。
    """
    provider = str(config.get("llm_provider", "opencode")).lower()
    # 实际判定：provider 是否在 model_catalog 支持范围内（实时拉取同源）。
    if provider not in MODEL_OPTIONS:
        logger.info("provider %s 无交互模型目录，跳过模型选择", provider)
        return {}
    if _is_env_locked("quick") or _is_env_locked("deep"):
        logger.info("MATHMODELING_QUICK/DEEP_THINK_LLM 已设置，跳过交互模型选择")
        return {}

    quick = _pick_single("quick", provider)
    deep = _pick_single("deep", provider)

    chosen: dict = {}
    if quick:
        chosen["quick"] = quick
    if deep:
        chosen["deep"] = deep
    return chosen


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
