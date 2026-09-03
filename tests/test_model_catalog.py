from cli.model_catalog import MODEL_OPTIONS, get_model_options


def test_major_providers_present():
    assert set(MODEL_OPTIONS) == {"opencode", "deepseek", "volcengine", "volcengine-plan"}


def test_each_mode_has_custom_fallback():
    for provider in MODEL_OPTIONS:
        for mode in ("quick", "deep"):
            ids = [mid for _, mid in get_model_options(provider, mode)]
            assert "custom" in ids, f"{provider}/{mode} 应提供 Custom 兜底"


def test_unknown_provider_falls_back_to_custom():
    assert get_model_options("does-not-exist", "quick")[0][1] == "custom"
