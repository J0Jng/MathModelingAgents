#!/usr/bin/env python
"""Real web_search invocation demo — no mocking.

Prints current provider info, calls web_search(), and exits 0 on success / 1 on total failure.
"""

from __future__ import annotations

import os
import sys

# 确保项目根在 sys.path 中（直接运行 scripts/*.py 时）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mathmodelingagents.tools.web_search import web_search, _get_provider


def main() -> int:
    provider = _get_provider()
    print(f"生效 provider: {provider}")

    # Show key status
    if provider == "tavily":
        key = os.getenv("TAVILY_API_KEY") or os.getenv("MATHMODELING_TAVILY_API_KEY")
        print(f"TAVILY_API_KEY: {'已设置' if key else '❌ 未设置'}")
    elif provider == "ddgs":
        try:
            import ddgs  # noqa: F401
            print("ddgs: 已安装 ✓")
        except ImportError:
            print("ddgs: ❌ 未安装（pip install duckduckgo-search）")
            print("搜索可能返回空结果或失败")

    print()
    print("=" * 60)
    print("搜索测试: web_search('2026 华为杯 数学建模', max_results=3)")
    print("=" * 60)
    result = web_search("2026 华为杯 数学建模", max_results=3)
    print(result)
    print("=" * 60)

    if result.startswith("[搜索失败]"):
        print("\n❌ 搜索全部失败")
        return 1
    elif result.startswith("[搜索未启用]"):
        print("\n⚠️  搜索未启用 (provider=off)")
        return 1
    else:
        print("\n✓ 搜索成功")
        return 0


if __name__ == "__main__":
    sys.exit(main())
