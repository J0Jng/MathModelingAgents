from cli.model_picker import _apply_model_selection


def test_apply_non_deepseek_overrides_overwrite_layer(capsys):
    # Claude Code：mock questionary 输入不可靠，这里直接测纯逻辑
    original = {"layer_model_overrides": {"problem": {"agent": "x", "manager": "y"}}}
    chosen = {"quick": "q", "deep": "d"}
    new = _apply_model_selection(original, "opencode", chosen)
    assert new["layer_model_overrides"]["problem"]["agent"] == "q"
