"""Ticket B — sandbox preamble 可测化：build_preamble 纯函数。

纯字符串断言，不 spawn 子进程/网络。阻塞策略作为数据注入：
- blocked 缺省为 _RISKY_MODULES，可用参数覆盖
- allowed 参数落地（注入 _safe_allow），缺省为空（不限制，保持 blocklist-only 现状）
"""

import mathmodelingagents.tools as tools
from mathmodelingagents.tools import build_preamble


class TestBuildPreamble:
    def test_default_blocked_equals_risky_set(self):
        p = build_preamble()
        assert f"_blocked = {sorted(tools._RISKY_MODULES)!r}" in p

    def test_custom_blocked_injected(self):
        p = build_preamble(blocked_modules={"socket", "os"})
        assert "socket" in p
        assert "os" in p
        # 默认 risky 里、但不在自定义集合中的额外模块不应出现在 _blocked 串中
        for m in ("requests", "urllib", "http", "ctypes"):
            assert m not in p

    def test_allowed_param_lands(self):
        # allowed_modules 真正注入 preamble（注入 _safe_allow，而非摆设）
        p = build_preamble(allowed_modules=["numpy", "pandas"])
        assert "numpy" in p
        assert "pandas" in p
        assert "_safe_allow" in p

    def test_default_allowed_no_restriction(self):
        # 缺省 allowed 为空 → 不引入额外限制（保持 blocklist-only 行为）
        p = build_preamble()
        assert "_safe_allow" in p

    def test_keeps_cjk_font_section(self):
        # 中文字体 preamble 原样保留（另一关注点，本 ticket 不动）
        p = build_preamble()
        assert "matplotlib Chinese font auto-config" in p