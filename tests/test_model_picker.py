import os

from cli.model_picker import prompt_model_selection, _apply_model_selection


def test_apply_non_deepseek_overrides_overwrite_layer(capsys):
    # Claude Code：mock questionary 输入不可靠，这里直接测纯逻辑
    original = {"layer_model_overrides": {"problem": {"agent": "x", "manager": "y"}}}
    chosen = {"quick": "q", "deep": "d"}
    new = _apply_model_selection(original, "opencode", chosen)
    assert new["layer_model_overrides"]["problem"]["agent"] == "q"


def test_env_precedence_skips_prompt(monkeypatch):
    # 设置 env 后，prompt_model_selection 应不弹菜单，直接返回空变更
    monkeypatch.setenv("MATHMODELING_QUICK_THINK_LLM", "some-quick")
    config = {"llm_provider": "opencode"}
    result = prompt_model_selection(config)
    assert result == {}  # env 已接管，不交互


def test_is_env_locked(monkeypatch):
    from cli.model_picker import _is_env_locked
    monkeypatch.delenv("MATHMODELING_QUICK_THINK_LLM", raising=False)
    monkeypatch.delenv("MATHMODELING_DEEP_THINK_LLM", raising=False)
    assert not _is_env_locked("quick") and not _is_env_locked("deep")
    monkeypatch.setenv("MATHMODELING_DEEP_THINK_LLM", "d1")
    assert _is_env_locked("deep") and not _is_env_locked("quick")


def test_build_options_realtime_fallback_and_custom(monkeypatch):
    from cli import model_picker
    # 拉取成功 → 使用实时列表，并追加 Custom 兜底
    monkeypatch.setattr(model_picker, "fetch_models", lambda p: ["m-a", "m-b"])
    opts = model_picker._build_options("volcengine", "deep")
    assert opts == [("m-a", "m-a"), ("m-b", "m-b"), ("Custom model ID", "custom")]
    # 拉取失败 → 降级静态清单（自带 Custom，不重复追加）
    monkeypatch.setattr(model_picker, "fetch_models", lambda p: [])
    opts = model_picker._build_options("volcengine", "deep")
    ids = [mid for _, mid in opts]
    assert ids.count("custom") == 1 and "ark-code-latest" in ids
