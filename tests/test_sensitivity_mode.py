"""接缝 2：敏感性模式归一化纯函数。

旧配置（布尔开关 / 层选择含 5）到三档 sensitivity_mode 的映射真值表。
对应 spec「Implementation Decisions - 敏感性模式」与 ADR-0001。
"""

from mathmodelingagents.default_config import resolve_sensitivity_mode


class TestExplicitMode:
    """显式 sensitivity_mode 直接生效（最高优先级）。"""

    def test_auto(self):
        assert resolve_sensitivity_mode({"sensitivity_mode": "auto"}) == "auto"

    def test_always(self):
        assert resolve_sensitivity_mode({"sensitivity_mode": "always"}) == "always"

    def test_never(self):
        assert resolve_sensitivity_mode({"sensitivity_mode": "never"}) == "never"

    def test_case_insensitive(self):
        assert resolve_sensitivity_mode({"sensitivity_mode": "ALWAYS"}) == "always"
        assert resolve_sensitivity_mode({"sensitivity_mode": " Never "}) == "never"

    def test_empty_config_defaults_to_auto(self):
        assert resolve_sensitivity_mode({}) == "auto"

    def test_invalid_mode_falls_back(self):
        # 无效值不崩溃，回退到旧键检查 -> auto
        assert resolve_sensitivity_mode({"sensitivity_mode": "bogus"}) == "auto"

    def test_explicit_mode_overrides_legacy_keys(self):
        config = {
            "sensitivity_mode": "never",
            "enable_sensitivity": True,
            "selected_layers": [1, 2, 3, 4, 5],
        }
        assert resolve_sensitivity_mode(config) == "never"


class TestLegacyBooleanSwitch:
    """旧布尔开关 enable_sensitivity 的迁移语义。"""

    def test_true_maps_to_always(self):
        assert resolve_sensitivity_mode({"enable_sensitivity": True}) == "always"

    def test_false_maps_to_auto(self):
        # false 是旧默认值（未强制），映射到新默认 auto，不告警
        assert resolve_sensitivity_mode({"enable_sensitivity": False}) == "auto"


class TestLegacySelectedLayers:
    """旧 selected_layers 含 5 的迁移语义。"""

    def test_contains_5_maps_to_always(self):
        assert resolve_sensitivity_mode({"selected_layers": [1, 2, 3, 4, 5]}) == "always"

    def test_layers_without_5_stays_auto(self):
        assert resolve_sensitivity_mode({"selected_layers": [1, 2, 3, 4]}) == "auto"

    def test_boolean_wins_over_selected_layers(self):
        # 布尔 true 与含 5 同为 always，结果一致；此处验证优先级链不冲突
        config = {"enable_sensitivity": True, "selected_layers": [1, 2, 3, 4, 5]}
        assert resolve_sensitivity_mode(config) == "always"
