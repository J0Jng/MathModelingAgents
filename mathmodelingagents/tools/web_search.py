"""Web search module — real search API integration for MathModelingAgents.

Provides a unified web_search() entry point with provider abstraction:
- tavily: POST https://api.tavily.com/search (requires API key)
- ddgs: DuckDuckGo Search via ddgs library (lazy import, no key needed)

Provider selection: MATHMODELING_WEB_SEARCH_PROVIDER env var
  "auto" (default): tavily if TAVILY_API_KEY set, else ddgs
  "tavily" / "ddgs" / "off": forced

Behavior rules:
- Timeout 10 seconds
- Any exception swallowed — returns "[搜索失败] ..." text
- Error summary truncated to ≤200 chars
- Default max_results=5
- tavily failure → auto fallback to ddgs; ddgs failure → failure text
"""

from __future__ import annotations

import json
import logging
import os
import socket
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

_TIMEOUT = 10  # seconds


# ═══════════════════════════════════════════════════════════════════════════════
# Provider selection
# ═══════════════════════════════════════════════════════════════════════════════


def _get_provider() -> str:
    """Resolve the effective web search provider.

    Returns:
        One of "tavily", "ddgs", or "off".
    """
    provider = (os.getenv("MATHMODELING_WEB_SEARCH_PROVIDER") or "auto").strip().lower()

    if provider == "off":
        return "off"

    if provider == "tavily":
        return "tavily"

    if provider == "ddgs":
        return "ddgs"

    if provider == "auto":
        if os.getenv("TAVILY_API_KEY") or os.getenv("MATHMODELING_TAVILY_API_KEY"):
            return "tavily"
        return "ddgs"

    # Unknown value → warn and treat as off
    logger.warning(
        "未知 MATHMODELING_WEB_SEARCH_PROVIDER=%s，已禁用搜索。有效值: auto | tavily | ddgs | off",
        provider,
    )
    return "off"


def _get_tavily_key() -> str | None:
    """Return the Tavily API key from env, if any."""
    return os.getenv("MATHMODELING_TAVILY_API_KEY") or os.getenv("TAVILY_API_KEY")


# ═══════════════════════════════════════════════════════════════════════════════
# Search backends
# ═══════════════════════════════════════════════════════════════════════════════


def _search_tavily(query: str, max_results: int = 5) -> list[dict]:
    """Search via Tavily API.

    Args:
        query: Search query string.
        max_results: Max number of results to return.

    Returns:
        List of dicts with keys: title, url, content.
    """
    api_key = _get_tavily_key()
    if not api_key:
        raise ValueError("TAVILY_API_KEY 未设置")

    body = json.dumps({
        "query": query,
        "max_results": min(max_results, 20),
        "search_depth": "basic",
    }).encode("utf-8")

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}

    # Prefer httpx, fallback to urllib
    try:
        import httpx
    except ImportError:
        return _search_tavily_urllib(body, headers)

    try:
        resp = httpx.post(
            "https://api.tavily.com/search",
            content=body,
            headers=headers,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
            for r in data.get("results", [])
        ]
    except Exception as e:
        raise RuntimeError(f"tavily: {_truncate_error(e)}") from e


def _search_tavily_urllib(body: bytes, headers: dict) -> list[dict]:
    """Fallback: search via Tavily using stdlib urllib."""
    import urllib.request
    import urllib.error

    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=body,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
            for r in data.get("results", [])
        ]
    except Exception as e:
        raise RuntimeError(f"tavily: {_truncate_error(e)}") from e


def _search_ddgs(query: str, max_results: int = 5) -> list[dict]:
    """Search via DuckDuckGo (ddgs library, lazy import).

    Args:
        query: Search query string.
        max_results: Max number of results to return.

    Returns:
        List of dicts with keys: title, url, content.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        logger.info("ddgs 未安装，跳过 DDGS 搜索（pip install duckduckgo-search）")
        return []

    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=max_results, region="wt-wt", safesearch="off"))
        return [
            {"title": r["title"], "url": r["href"], "content": r["body"]}
            for r in raw
        ]
    except Exception as e:
        raise RuntimeError(f"ddgs: {_truncate_error(e)}") from e


# ═══════════════════════════════════════════════════════════════════════════════
# Formatting
# ═══════════════════════════════════════════════════════════════════════════════


def _format_results(query: str, provider: str, results: list[dict]) -> str:
    """Format search results into LLM-friendly markdown.

    Args:
        query: Original search query.
        provider: Provider name (tavily / ddgs).
        results: List of result dicts.

    Returns:
        Formatted string.
    """
    count = len(results)
    lines = [f"### 搜索结果: {query}（provider: {provider}, {count} 条）"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "无标题")
        url = r.get("url", "")
        content = r.get("content", "")
        # Truncate content to 200 chars
        if len(content) > 200:
            content = content[:200] + "…"
        lines.append(f"{i}. [{title}]({url})")
        lines.append(f"   {content}")
    return "\n".join(lines)


def _truncate_error(e: Exception) -> str:
    """Truncate error message to ≤200 chars."""
    msg = str(e)
    return msg[:200] if len(msg) > 200 else msg


# ═══════════════════════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════════════════════


def web_search(query: str, max_results: int = 5) -> str:
    """Search the web and return formatted results.

    Args:
        query: Search query string.
        max_results: Max number of results (default 5).

    Returns:
        Formatted search results string, or "[搜索失败] …" / "[搜索未启用] …".
    """
    provider = _get_provider()

    if provider == "off":
        return (
            "[搜索未启用] 搜索功能当前已关闭。"
            "如需启用请设置环境变量 MATHMODELING_WEB_SEARCH_PROVIDER（auto | tavily | ddgs）"
        )

    logger.info("web_search: query=%r, provider=%s, max_results=%d", query, provider, max_results)

    # ── Primary search ──
    results: list[dict] = []
    error_summary: str | None = None

    try:
        if provider == "tavily":
            results = _search_tavily(query, max_results)
        else:
            results = _search_ddgs(query, max_results)
    except Exception as e:
        error_summary = _truncate_error(e)
        logger.warning("web_search: %s 搜索失败: %s", provider, error_summary)

    # ── Fallback: tavily failed → try ddgs ──
    if not results and provider == "tavily":
        logger.info("web_search: tavily 失败，降级尝试 ddgs…")
        try:
            results = _search_ddgs(query, max_results)
            if results:
                provider = "ddgs"  # relabel for output
                error_summary = None
            elif error_summary:
                error_summary = error_summary + " | ddgs: 无结果"
        except Exception as e:
            ddgs_err = _truncate_error(e)
            if error_summary:
                error_summary = error_summary + " | ddgs: " + ddgs_err
            else:
                error_summary = "ddgs: " + ddgs_err
            logger.warning("web_search: ddgs 降级也失败: %s", ddgs_err)

    # ── No results after all attempts ──
    if not results:
        # ddgs not installed but provider=ddgs (or was the fallback)
        if error_summary is None and (provider == "ddgs" or _get_provider() == "ddgs"):
            error_summary = "ddgs: 库未安装，请运行 pip install duckduckgo-search"
        return f"[搜索失败] {error_summary or '未知错误'}"

    return _format_results(query, provider, results)


# ═══════════════════════════════════════════════════════════════════════════════
# URL verification
# ═══════════════════════════════════════════════════════════════════════════════


def check_url(url: str) -> str:
    """Verify a URL is reachable. Returns a short verdict string (never raises).

    Verdicts:
      ✅ 可达 (HTTP xxx)              — 2xx/3xx
      ⚠️ 疑似存在但被拒绝访问 (HTTP xxx) — 401/403（反爬常见，不算失效）
      ⚠️ 服务器临时错误 (HTTP xxx)     — 5xx
      ❌ 失效 (HTTP xxx)              — 4xx（除 401/403）
      ❌ 无法连接 — <原因>             — 超时 / DNS / 连接失败
    """
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            code = resp.getcode()
            return f"✅ 可达 (HTTP {code})"
    except urllib.error.HTTPError as e:
        code = e.code
        if code in (401, 403):
            return f"⚠️ 疑似存在但被拒绝访问 (HTTP {code})"
        if 500 <= code < 600:
            return f"⚠️ 服务器临时错误 (HTTP {code})"
        return f"❌ 失效 (HTTP {code})"
    except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
        return f"❌ 无法连接 — {_truncate_error(e)}"
    except Exception as e:
        return f"❌ 无法连接 — {_truncate_error(e)}"
