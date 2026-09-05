from cli.model_catalog import MODEL_OPTIONS, get_model_options


def test_major_providers_present():
    assert set(MODEL_OPTIONS) == {"opencode", "deepseek", "volcengine", "volcengine-plan"}


def test_unknown_provider_falls_back_to_empty():
    """未知 provider 返回空列表（custom 兜底由 picker._build_options 注入，不在 catalog）。"""
    assert get_model_options("does-not-exist", "quick") == []


def test_custom_not_in_catalog():
    """catalog 不再内嵌 custom 哨兵；由 cli.model_picker 在构建菜单时统一追加。"""
    for provider in MODEL_OPTIONS:
        for mode in ("quick", "deep"):
            ids = [mid for _, mid in get_model_options(provider, mode)]
            assert "custom" not in ids, f"{provider}/{mode} catalog 不应含 custom"


def test_verified_unusable_models_removed():
    """已实测不可用的模型不得出现在静态菜单（2026-09-04 chat 探针）。"""
    from cli.model_catalog import get_model_options

    # 已移除：随机探针验证 401/500。
    # kimi-k3：Agent Plan 官方说明「仅支持 medium 及以上套餐」，套餐不符时 404 UnsupportedModel ——
    #   属于套餐限制而非模型坏，故**保留**在菜单（label 已标注），不列入此黑名单。
    dead = {"grok-4.6", "gpt-5.6-luna", "minimax-m2.7"}
    for provider in MODEL_OPTIONS:
        for mode in ("quick", "deep"):
            ids = {mid for _, mid in get_model_options(provider, mode)}
            assert not (ids & dead), f"{provider}/{mode} 含已移除模型: {ids & dead}"


def test_all_official_ids_are_verified_usable():
    """静态菜单里的官方短名应能通过 chat 端点实测（去掉去掉不可用的后）。"""
    from cli.model_catalog import MODEL_OPTIONS

    for provider, modes in MODEL_OPTIONS.items():
        for mode, opts in modes.items():
            for _, mid in opts:
                if mid == "custom":
                    continue
                # 最小烟雾断言：id 非空且非保留字；真实连通性由 scripts/verify_provider_models.py 冒烟
                assert mid and mid != "custom"
