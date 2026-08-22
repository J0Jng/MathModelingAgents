"""Ticket A — web-search provider 决定收敛到 resolve_provider/config。

覆盖 seam `resolve_provider` / `resolve_tavily_key` / `web_search`：
- config 优先于 env；无 config 退化为纯 env 语义（与旧行为一致）
- web_search 无网络真值测试：provider=off → `[搜索未启用]` 前缀
（不 spawn 网络/子进程，纯内存断言）
"""

from importlib import import_module

ws = import_module("mathmodelingagents.tools.web_search")


class TestResolveProvider:
    def test_config_off(self):
        assert ws.resolve_provider({"web_search_provider": "off"}) == "off"

    def test_config_tavily(self):
        assert ws.resolve_provider({"web_search_provider": "tavily"}) == "tavily"

    def test_config_overrides_env(self, monkeypatch):
        # config 优先于 env（死配置不再生效时应以 config 为准）
        monkeypatch.setenv("MATHMODELING_WEB_SEARCH_PROVIDER", "off")
        assert ws.resolve_provider({"web_search_provider": "tavily"}) == "tavily"

    def test_env_only_auto_no_key_ddgs(self, monkeypatch):
        # 无 config 清掉 env 后 auto → ddgs（tavily key 不存在）
        monkeypatch.delenv("MATHMODELING_WEB_SEARCH_PROVIDER", raising=False)
        monkeypatch.delenv("MATHMODELING_TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        assert ws.resolve_provider(None) == "ddgs"

    def test_env_only_auto_with_key_tavily(self, monkeypatch):
        monkeypatch.delenv("MATHMODELING_WEB_SEARCH_PROVIDER", raising=False)
        monkeypatch.setenv("TAVILY_API_KEY", "k")
        assert ws.resolve_provider(None) == "tavily"

    def test_unknown_value_falls_back_off(self, monkeypatch):
        monkeypatch.delenv("MATHMODELING_WEB_SEARCH_PROVIDER", raising=False)
        assert ws.resolve_provider({"web_search_provider": "bogus"}) == "off"


class TestResolveTavilyKey:
    def test_config_wins(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "env")
        assert ws.resolve_tavily_key({"tavily_api_key": "cfg"}) == "cfg"

    def test_env_fallback(self, monkeypatch):
        monkeypatch.delenv("MATHMODELING_TAVILY_API_KEY", raising=False)
        monkeypatch.setenv("TAVILY_API_KEY", "env")
        assert ws.resolve_tavily_key(None) == "env"

    def test_empty_config_uses_env(self, monkeypatch):
        monkeypatch.delenv("MATHMODELING_TAVILY_API_KEY", raising=False)
        monkeypatch.setenv("TAVILY_API_KEY", "env")
        assert ws.resolve_tavily_key({}) == "env"


class TestWebSearchOff:
    def test_config_off_returns_disabled_prefix(self):
        # 不开网络：config=off 时直接返回禁用文案，断言前缀
        assert ws.web_search("anything", config={"web_search_provider": "off"}).startswith("[搜索未启用]")


class TestFormatResults:
    def test_formats_markdown_output(self):
        out = ws._format_results(
            "q", "tavily",
            [{"title": "标题", "url": "http://x", "content": "内容"}],
        )
        assert "provider: tavily" in out
        assert "[标题](http://x)" in out

    def test_truncates_long_content(self):
        out = ws._format_results("q", "tavily", [{"title": "t", "url": "u", "content": "x" * 500}])
        assert "…" in out
        assert len(out) < 300


class TestTavilyFailureFallsBackToDdgs:
    def test_relabels_provider_to_ddgs(self, monkeypatch):
        def fake_tavily(*a, **k):
            raise RuntimeError("tavily down")

        def fake_ddgs(*a, **k):
            return [{"title": "dd", "url": "http://d", "content": "c"}]

        monkeypatch.setattr(ws, "_search_tavily", fake_tavily)
        monkeypatch.setattr(ws, "_search_ddgs", fake_ddgs)
        out = ws.web_search(
            "q", config={"web_search_provider": "tavily", "tavily_api_key": "k"}
        )
        # tavily 失败 → 降级 ddgs 成功，输出正常且 provider 重标为 ddgs
        assert out.startswith("### 搜索结果")
        assert "provider: ddgs" in out