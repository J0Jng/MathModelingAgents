"""main.py 配置组装 + 交互模型选择接线测试。

build_config_from_args 是 main() 中「CLI 参数 → config 组装 → 交互模型选择」
的可测辅助函数；交互函数经 monkeypatch 模拟，不驱动真实 TTY。
"""

import argparse

from mathmodelingagents.default_config import DEFAULT_CONFIG
from main import build_config_from_args


def _args(**kw) -> argparse.Namespace:
    base = dict(
        problem_path="dummy.md",
        output=None,
        sensitivity=None,
        max_rounds=10,
        provider=None,
        start_layer=1,
        from_layer1=None,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def test_provider_flag_writes_llm_provider(monkeypatch):
    monkeypatch.delenv("MATHMODELING_QUICK_THINK_LLM", raising=False)
    monkeypatch.delenv("MATHMODELING_DEEP_THINK_LLM", raising=False)
    monkeypatch.setattr("cli.model_picker.prompt_model_selection", lambda config: {})
    config = build_config_from_args(_args(provider="volcengine-plan"))
    assert config["llm_provider"] == "volcengine-plan"


def test_empty_selection_keeps_defaults(monkeypatch):
    monkeypatch.delenv("MATHMODELING_QUICK_THINK_LLM", raising=False)
    monkeypatch.delenv("MATHMODELING_DEEP_THINK_LLM", raising=False)
    monkeypatch.setattr("cli.model_picker.prompt_model_selection", lambda config: {})
    config = build_config_from_args(_args())
    assert config["quick_think_llm"] == DEFAULT_CONFIG["quick_think_llm"]
    assert config["deep_think_llm"] == DEFAULT_CONFIG["deep_think_llm"]
    assert config["layer_model_overrides"]["paper"]["writer"] == "qwen3.7-max"


def test_interaction_selection_applied_to_config(monkeypatch):
    monkeypatch.delenv("MATHMODELING_QUICK_THINK_LLM", raising=False)
    monkeypatch.delenv("MATHMODELING_DEEP_THINK_LLM", raising=False)
    monkeypatch.setattr(
        "cli.model_picker.prompt_model_selection",
        lambda config: {"quick": "q1", "deep": "d1"},
    )
    config = build_config_from_args(_args(provider="opencode"))
    # 交互选中的 quick/deep 已覆盖全局层角色映射
    assert config["layer_model_overrides"]["paper"]["writer"] == "d1"
    assert config["layer_model_overrides"]["paper"]["manager"] == "d1"
    assert config["layer_model_overrides"]["problem"]["agent"] == "q1"


def test_env_lock_skips_interaction(monkeypatch):
    # 任一 env 设置 → 交互被跳过（prompt_model_selection 本身返回 {}），
    # 这里验证整条链路在 env 锁定下不改 config。
    monkeypatch.setenv("MATHMODELING_QUICK_THINK_LLM", "env-quick")
    config = build_config_from_args(_args(provider="opencode"))
    assert config["layer_model_overrides"]["paper"]["writer"] == "qwen3.7-max"
