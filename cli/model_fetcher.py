"""按 provider 实时拉取可用模型列表（OpenAI 兼容 /models 端点）。

已实测（计划调研）：volcengine 的 `/api/coding/v3/models` 与
`/api/plan/v3/models` 端点存在（返回真实 AuthenticationError，说明带真实 key
即可列出模型）。其它 provider 端点见映射表。

设计：拉取失败（无 key / 认证错 / 超时 / provider 不支持）时返回 []，
绝不抛出异常 —— 由调用方降级到静态清单，保证交互菜单永不阻塞。
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

# provider → (endpoint_suffix, api_key_env)
_ENDPOINTS: dict[str, tuple[str, str]] = {
    "volcengine":       ("https://ark.cn-beijing.volces.com/api/coding/v3/models", "VOLCENGINE_API_KEY"),
    "volcengine-plan":  ("https://ark.cn-beijing.volces.com/api/plan/v3/models", "VOLCENGINE_PLAN_API_KEY"),
    "opencode":         ("https://opencode.ai/zen/go/v1/models", "OPENCODE_GO_API_KEY"),
    "deepseek":         ("https://api.deepseek.com/v1/models", "DEEPSEEK_API_KEY"),
}


def provider_models_endpoint(provider: str) -> str | None:
    """返回某 provider 的模型列表端点（无则 None）。"""
    return _ENDPOINTS.get(provider.lower(), (None, None))[0]


def fetch_models(provider: str, timeout: float = 8.0) -> list[str]:
    """实时拉取某 provider 的可用模型名列表。

    Returns:
        模型名字符串列表；拉取失败（无 key/认证错/超时/provider 不支持）返回 []。
        绝不抛出异常。
    """
    entry = _ENDPOINTS.get(provider.lower())
    if not entry:
        return []
    url, key_env = entry
    api_key = os.getenv(key_env) or (os.getenv("VOLCENGINE_API_KEY") if provider.lower() == "volcengine-plan" else None)
    if not api_key:
        logger.info("%s 未设置 %s，跳过实时拉取模型列表", provider, key_env)
        return []
    try:
        resp = requests.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        model_ids = [m.get("id") if isinstance(m, dict) else str(m) for m in data.get("data", [])]
        return [mid for mid in model_ids if mid]
    except Exception as e:  # noqa: BLE001 — 拉取失败不阻塞,交由降级链
        logger.warning("拉取 %s 模型列表失败，降级到静态清单: %s", provider, e)
        return []
