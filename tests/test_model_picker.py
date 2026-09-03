import os

from cli.model_picker import prompt_model_selection, _apply_model_selection, _skip_prompt_requested


def test_apply_non_deepseek_overrides_overwrite_layer(capsys):
    # Claude Code：mock questionary 输入不可靠，这里直接测纯逻辑
    original = {"layer_model_overrides": {"problem": {"agent": "x", "manager": "y"}}}
    chosen = {"quick": "q", "deep": "d"}
    new = _apply_model_selection(original, "opencode", chosen)
    assert new["layer_model_overrides"]["problem"]["agent"] == "q"


def test_env_precedence_skips_prompt(monkeypatch):
    # QUICK/DEEP_THINK_LLM 只当默认模型值，不再隐式关闭交互 → 应正常弹菜单（不返回空变更）。
    # 这里避免驱动真实 TTY，仅验证这两个 env 设置不会触发跳过（否则会试图弹 questionary，
    # 但我们用不会命中跳过分支的断言来确认）。真正跳过要看 SKIP_MODEL_PROMPT。
    config = {"llm_provider": "opencode"}
    # 只设 QUICK 模型值：不应被当作跳过开关
    monkeypatch.setenv("MATHMODELING_QUICK_THINK_LLM", "some-quick")
    assert not _skip_prompt_requested()  # QUICK env 不会触发跳过


def test_skip_prompt_is_the_only_skip(monkeypatch):
    from cli.model_picker import _skip_prompt_requested
    # 未设置 → 不跳过
    monkeypatch.delenv("MATHMODELING_SKIP_MODEL_PROMPT", raising=False)
    assert not _skip_prompt_requested()
    # 显式置真 → 跳过
    monkeypatch.setenv("MATHMODELING_SKIP_MODEL_PROMPT", "1")
    assert _skip_prompt_requested()
    monkeypatch.setenv("MATHMODELING_SKIP_MODEL_PROMPT", "true")
    assert _skip_prompt_requested()
    # 置假/非真值 → 不跳过
    monkeypatch.setenv("MATHMODELING_SKIP_MODEL_PROMPT", "0")
    assert not _skip_prompt_requested()
    # 即使 QUICK/DEEP 模型值已设置，未显式 SKIP 就不跳过
    monkeypatch.setenv("MATHMODELING_QUICK_THINK_LLM", "q")
    monkeypatch.setenv("MATHMODELING_DEEP_THINK_LLM", "d")
    monkeypatch.delenv("MATHMODELING_SKIP_MODEL_PROMPT", raising=False)
    assert not _skip_prompt_requested()


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


def test_modeler_roles_go_deep_not_quick():
    # modeling 层的 agent 是 modeler_*（数学建模师），应归 deep（与 default_config L2=pro 一致），
    # 而非普通 quick。L1/L5 的 agent 仍归 quick。
    original = {
        "layer_model_overrides": {
            "problem": {"agent": "x", "manager": "y"},
            "modeling": {"agent": "z", "manager": "w"},
            "sensitivity": {"agent": "s", "manager": "m"},
            "explanation": {"agent": "e"},
        },
    }
    new = _apply_model_selection(original, "opencode", {"quick": "q", "deep": "d"})
    # modeler (modeling.agent) -> deep
    assert new["layer_model_overrides"]["modeling"]["agent"] == "d"
    # L1/L5 agent -> quick
    assert new["layer_model_overrides"]["problem"]["agent"] == "q"
    assert new["layer_model_overrides"]["sensitivity"]["agent"] == "q"
    # explainer (explanation.agent) -> quick
    assert new["layer_model_overrides"]["explanation"]["agent"] == "q"
    # managers -> deep
    assert new["layer_model_overrides"]["modeling"]["manager"] == "d"
    assert new["layer_model_overrides"]["problem"]["manager"] == "d"
    # 端到端：经 get_layer_model 真实解析
    new["llm_provider"] = "opencode"
    from mathmodelingagents.llm_clients import get_layer_model
    assert get_layer_model(new, "modeling", "agent") == "d"
    assert get_layer_model(new, "problem", "agent") == "q"


def test_apply_non_deepseek_overwrites_everything():
    original = {
        "layer_model_overrides": {
            "problem": {"agent": "x", "manager": "y"},
            "implementation": {"coder": "kimi", "manager": "z"},
            "paper": {"writer": "qwen", "manager": "w"},
        },
        "provider_layer_model_overrides": {
            "volcengine-plan": {"implementation": {"coder": "kimi-k2.7-code"}, "paper": {"writer": "minimax-m3"}},
        },
        "provider_model_aliases": {"volcengine-plan": {"qwen3.7-max": "minimax-m3"}},
    }
    new = _apply_model_selection(original, "volcengine-plan", {"quick": "q", "deep": "d"})
    # 交互选中模型作为最高优先级：所有层的 manager/writer/coder 等重角色被 deep 覆盖
    assert new["layer_model_overrides"]["problem"]["manager"] == "d"
    assert new["layer_model_overrides"]["implementation"]["coder"] == "d"  # 覆盖 provider 硬编码的 kimi
    assert new["layer_model_overrides"]["paper"]["writer"] == "d"          # 覆盖 provider 硬编码的 minimax-m3
    # 端到端：经 get_layer_model 真实解析后交互选择仍然生效（pv 级冲突角色已被清理）
    new["llm_provider"] = "volcengine-plan"
    from mathmodelingagents.llm_clients import get_layer_model
    assert get_layer_model(new, "implementation", "coder") == "d"
    assert get_layer_model(new, "paper", "writer") == "d"
    assert get_layer_model(new, "problem", "agent") == "q"
