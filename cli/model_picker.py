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

# 允许通过环境变量跳过交互（显式设置才跳过）。
# 注意：不加 read QUICK/DEEP_THINK_LLM —— 那俩只当默认模型值，不能隐式关闭交互。
_SKIP_PROMPT_VAR = "MATHMODELING_SKIP_MODEL_PROMPT"
_SKIP_TRUE = {"1", "true", "yes", "on"}


def _skip_prompt_requested() -> bool:
    """仅当 MATHMODELING_SKIP_MODEL_PROMPT 显式置真时跳过交互。"""
    return os.getenv(_SKIP_PROMPT_VAR, "").strip().lower() in _SKIP_TRUE


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
    """弹单个 questionary.select。返回模型 id；None 表示取消（Ctrl-C/Esc）。"""
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
    - MATHMODELING_SKIP_MODEL_PROMPT 显式置真 → 返回 {}（env 已接管，CI/无 TTY 用）。
    - 用户取消任意一次 → 返回 {}（保持当前配置）。
    """
    provider = str(config.get("llm_provider", "opencode")).lower()
    # 实际判定：provider 是否在 model_catalog 支持范围内（实时拉取同源）。
    if provider not in MODEL_OPTIONS:
        logger.info("provider %s 无交互模型目录，跳过模型选择", provider)
        return {}
    if _skip_prompt_requested():
        logger.info("MATHMODELING_SKIP_MODEL_PROMPT 已设置，跳过交互模型选择")
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

    交互选中模型为最高优先级 —— 遍历 layer_model_overrides 中每个 layer 的每个
    角色：deep 角色集 → chosen['deep']，否则 chosen['quick']。同时清空/归并
    provider_layer_model_overrides 中与该层冲突的 provider 级角色（交互覆盖一切），
    并移除 provider_model_aliases 中会改写所选模型的条目（保证所选模型真实生效）。
    deepseek provider：直接设 deep_think_llm / quick_think_llm。空 config 防御。
    """
    new = dict(config)
    quick = chosen.get("quick")
    deep = chosen.get("deep")

    if provider == "deepseek":
        if deep:
            new["deep_think_llm"] = deep
        if quick:
            new["quick_think_llm"] = quick
        return new

    # 非 deepseek：逐 layer 逐角色完全覆盖
    layer_overrides = {
        layer: dict(roles) for layer, roles in (config.get("layer_model_overrides") or {}).items()
    }
    for layer, roles in layer_overrides.items():
        for role in list(roles):
            # 决定用 deep 还是 quick：deep 角色集优先；其他(如纯 agent)用 quick
            replacement = deep if role in _DEEP_ROLES else quick
            if replacement:
                roles[role] = replacement

    # 交互选择最高优先级：移除 provider 级覆盖中与所选深度冲突的角色，避免被回盖
    # （get_layer_model 中 provider 级覆盖后合并胜出，不移除则交互选择形同虚设）
    pv_overrides = {
        p: {layer: dict(roles) for layer, roles in layers.items()}
        for p, layers in (config.get("provider_layer_model_overrides") or {}).items()
    }
    if provider in pv_overrides:
        pv = pv_overrides[provider]
        for layer, roles in pv.items():
            for role in list(roles):
                if role in _DEEP_ROLES:
                    roles.pop(role, None)  # 深度角色由交互 deep 接管

    new["layer_model_overrides"] = layer_overrides
    new["provider_layer_model_overrides"] = pv_overrides
    return new
