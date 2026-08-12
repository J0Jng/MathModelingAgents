"""Unit tests for check_url function — no real network calls."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from mathmodelingagents.tools.web_search import check_url


# ═══════════════════════════════════════════════════════════════════════════════
# check_url
# ═══════════════════════════════════════════════════════════════════════════════


class TestCheckUrlSuccess:
    def test_200_returns_reachable(self):
        """HTTP 200 → ✅ 可达."""
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = check_url("https://example.com")
            assert "✅ 可达" in result

    def test_redirect_returns_reachable(self):
        """HTTP 302 → ✅ 可达 (redirects are OK)."""
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 302
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = check_url("https://example.com/redirect")
            assert "✅ 可达" in result


class TestCheckUrlHttpError:
    def test_404_returns_dead_link(self):
        """HTTP 404 → ❌ 失效."""
        from urllib.error import HTTPError

        mock_error = HTTPError(
            "https://example.com/404", 404, "Not Found", {}, None
        )

        with patch("urllib.request.urlopen", side_effect=mock_error):
            result = check_url("https://example.com/404")
            assert "❌ 失效" in result

    def test_403_returns_warning(self):
        """HTTP 403 → ⚠️ (access denied, not dead)."""
        from urllib.error import HTTPError

        mock_error = HTTPError(
            "https://example.com/403", 403, "Forbidden", {}, None
        )

        with patch("urllib.request.urlopen", side_effect=mock_error):
            result = check_url("https://example.com/403")
            assert "⚠️" in result
            assert "❌" not in result

    def test_401_returns_warning(self):
        """HTTP 401 → ⚠️ (unauthorized, not dead)."""
        from urllib.error import HTTPError

        mock_error = HTTPError(
            "https://example.com/401", 401, "Unauthorized", {}, None
        )

        with patch("urllib.request.urlopen", side_effect=mock_error):
            result = check_url("https://example.com/401")
            assert "⚠️" in result
            assert "❌" not in result

    def test_5xx_returns_warning(self):
        """HTTP 503 → ⚠️ 服务器临时错误."""
        from urllib.error import HTTPError

        mock_error = HTTPError(
            "https://example.com/503", 503, "Service Unavailable", {}, None
        )

        with patch("urllib.request.urlopen", side_effect=mock_error):
            result = check_url("https://example.com/503")
            assert "⚠️" in result
            assert "❌" not in result

    def test_418_returns_dead_link(self):
        """HTTP 418 (non-401/403 4xx) → ❌ 失效."""
        from urllib.error import HTTPError

        mock_error = HTTPError(
            "https://example.com/418", 418, "I'm a teapot", {}, None
        )

        with patch("urllib.request.urlopen", side_effect=mock_error):
            result = check_url("https://example.com/418")
            assert "❌ 失效" in result


class TestCheckUrlConnectionErrors:
    def test_timeout_returns_unreachable(self):
        """TimeoutError → ❌ 无法连接, never raises."""
        import urllib.error

        mock_error = urllib.error.URLError("timed out")

        with patch("urllib.request.urlopen", side_effect=mock_error):
            result = check_url("https://example.com/timeout")
            assert "❌ 无法连接" in result
            assert result.startswith("❌")

    def test_arbitrary_exception_returns_unreachable(self):
        """Any unexpected exception → ❌, never raises."""
        with patch("urllib.request.urlopen", side_effect=ValueError("unexpected")):
            result = check_url("https://example.com/bad")
            assert "❌" in result
            # Must not raise


# ═══════════════════════════════════════════════════════════════════════════════
# create_paper_agent_tools includes check_url
# ═══════════════════════════════════════════════════════════════════════════════


class TestPaperAgentTools:
    def test_includes_check_url(self):
        """create_paper_agent_tools returns a tool containing 'check_url' in its name list."""
        from mathmodelingagents.tools import create_paper_agent_tools

        tools = create_paper_agent_tools(".")
        names = [t.name for t in tools]
        assert any("check_url" in n for n in names)
