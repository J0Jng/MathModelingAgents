"""Verify a provider's official model short-names are actually usable via chat, NOT /models.

背景：API 平台的 /models 端点返回的是几百个"部署 id"（带日期后缀，如
doubao-seed-2-0-lite-260428），而官方平台"模型列表"里的逻辑别名（如
doubao-seed-2.0-lite、glm-5.3、kimi-k2.7-code…）是 chat 端点可直接使用的短名，
可能并不出现在 /models 列表里。因此可用性的权威验证是**发起一条最小 chat 请求**，
而非拉 /models。

用法：
    python scripts/verify_provider_models.py opencode
    python scripts/verify_provider_models.py volcengine        # Coding plan
    python scripts/verify_provider_models.py volcengine-plan   # Agent plan
    python scripts/verify_provider_models.py --all
    python scripts/verify_provider_models.py opencode --names deepseek-v4-flash,glm-5.3

失败（无 key / 认证错 / 网络错）会打印 FAIL 并统计，绝不抛异常。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

# 允许从任意 cwd 运行：把项目根（脚本父目录上一级）加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cli.model_catalog import MODEL_OPTIONS

load_dotenv()

# provider → (chat base_url, api_key_env, label)。真实验证走 chat 端点。
_PROVIDERS: dict[str, dict] = {
    "opencode": {
        "url": "https://opencode.ai/zen/go/v1/chat/completions",
        "key_env": "OPENCODE_GO_API_KEY",
        "key_fallback": "OPENCODE_API_KEY",
        "label": "OpenCode Go",
    },
    "volcengine": {
        "url": "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions",
        "key_env": "VOLCENGINE_API_KEY",
        "label": "火山方舟 Coding Plan",
    },
    "volcengine-plan": {
        "url": "https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions",
        "key_env": "VOLCENGINE_PLAN_API_KEY",
        "key_fallback": "VOLCENGINE_API_KEY",
        "label": "火山方舟 Agent Plan",
    },
    "deepseek": {
        "url": "https://api.deepseek.com/v1/chat/completions",
        "key_env": "DEEPSEEK_API_KEY",
        "label": "DeepSeek 官方",
    },
}


def _key(prov: dict) -> str:
    k = os.getenv(prov["key_env"]) or (os.getenv(prov.get("key_fallback", "")) or "")
    return k


def unique_model_ids(provider: str) -> list[str]:
    """从静态官方清单去重收集全部模型短名。"""
    seen: dict[str, str] = {}
    for mode in ("quick", "deep"):
        for label, mid in MODEL_OPTIONS.get(provider, {}).get(mode, []):
            if mid == "custom":
                continue
            seen[mid] = label
    return list(seen)


def verify_one(prov: dict, model: str, timeout: float = 60.0) -> tuple[bool, str]:
    """对单个 model 短名发最小 chat 请求，返回 (ok, 诊断)。"""
    key = _key(prov)
    if not key:
        return False, f"未设置 {prov['key_env']}"
    try:
        resp = requests.post(
            prov["url"],
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "请只回复两个字：正常"}],
            },
            timeout=timeout,
        )
        if resp.status_code == 200:
            content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                return True, "200 但 content 为空"
            return True, "200 OK"
        body = (resp.text or "")[:160].replace("\n", " ")
        return False, f"HTTP {resp.status_code}: {body}"
    except Exception as e:  # noqa: BLE001
        return False, f"ERR {type(e).__name__}: {e}"


def run(provider: str, names: list[str] | None = None) -> tuple[int, int]:
    prov = _PROVIDERS.get(provider)
    if not prov:
        print(f"未知 provider: {provider}（可选: {', '.join(_PROVIDERS)}）")
        return 0, 1
    targets = names or unique_model_ids(provider)
    if not targets:
        print(f"{provider} 无可验证模型（静态清单为空）")
        return 0, 1
    print(f"=== {prov['label']} ({provider}) — 通过 chat 端点验证 {len(targets)} 个模型 ===")
    ok = fail = 0
    for i, name in enumerate(targets, 1):
        success, diag = verify_one(prov, name)
        status = "✅" if success else "❌"
        ok += success
        fail += (not success)
        print(f"  [{i}/{len(targets)}] {status} {name:<42} {diag}")
    print(f"\n结果: {ok} 可用 / {fail} 不可用（共 {len(targets)}）")
    return bool(fail), fail


def main() -> int:
    parser = argparse.ArgumentParser(description="通过 chat 端点验证官方模型短名可用性")
    parser.add_argument("provider", nargs="?", default=None, help="opencode/volcengine/volcengine-plan/deepseek 或 --all")
    parser.add_argument("--all", action="store_true", help="验证全部 provider")
    parser.add_argument("--names", default=None, help="逗号分隔的模型名白名单（跳过静态清单）")
    args = parser.parse_args()
    if args.all:
        anyfail = 0
        for p in _PROVIDERS:
            _, f = run(p)
            anyfail += f
        return 1 if anyfail else 0
    if not args.provider:
        parser.print_help()
        return 2
    names = [n.strip() for n in args.names.split(",")] if args.names else None
    _, fail = run(args.provider, names)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())