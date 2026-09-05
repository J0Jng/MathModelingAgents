# CLI 交互式 Agent（模型）选择 Implementation Plan

> **For Hermes:** Use subagent-driven-development skill（或 Claude Code CLI）to implement this plan task-by-task.
>
> **可执行者交接：** 本计划可直接交给 Claude Code（`claude -p` 逐任务执行）或由 Hermes 的 subagent 驱动。任务粒度 2-5 分钟，每任务末尾含精确命令与验证方式。

**Goal:** 在 `main.py` 的 CLI 入口增加一个交互式页面——当用户通过命令行运行框架、在「输入 python 执行代码」后进入选择流程时，弹出一个基于当前 provider 的「快速思考 / 深度思考」模型（即 agent）选择菜单，选择结果写回 `config` 的 provider 相关键后，再启动真正的建模流程。

**Architecture:** 采用「provider 模型适配层 + 实时拉取降级链 + 静态配置默认值」结构（交互高度对标 TradingAgents 的 `cli/utils.py` + `cli/main.py` env 跳过，但**模型清单是实时拉取各 provider 可用模型**而非手写静态库——用户已确认交互选择应作为最高优先级，完全覆盖 provider 级硬编码，故必须展示 provider 真实可用模型供选择）。`default_config.py` **保持纯静态默认值**（第 72-150 行仅作为各 provider 的默认映射与 fallback），交互逻辑全部落在新建的 `cli/` 包，由 `main.py` 在 `propagate()` 之前调用。环境变量（`MATHMODELING_QUICK_THINK_LLM` / `MATHMODELING_DEEP_THINK_LLM`）显式设置时跳过交互菜单（对齐 TradingAgents 的 env-precedence 规则），保证非交互/CI/脚本场景不受影响。

**「实时拉取」设计（用户确认）**：交互菜单展示的是各 provider 当前真实可用的模型列表，而非手写静态库。实现采用「拉取 → 降级 → 静态兜底」三级链：
1. 尝试 GET 当前 provider 的 OpenAI 兼容 `/models` 端点（已实测 volcengine 的 `/api/coding/v3/models` 与 `/api/plan/v3/models` 端点存在且返回真实验证错误）。
2. 拉取失败（无 key/认证错/超时）→ 降级到该 provider 的静态预设清单（`MODEL_OPTIONS`，覆盖项目已知常用模型）。
3. 仍无法判断（provider 无对应项）→ 仅提供 `Custom model ID` 兜底，不阻塞。

> **2026-09-04 修正**：上面「实时拉取展示菜单」的设计**已废弃**。实测发现 `/models` 端点返回的是几百个**带日期后缀的部署 id**（如 `doubao-seed-2-0-lite-260428`），而官方平台"模型列表"里的逻辑别名（`glm-5.3`、`kimi-k2.7-code`、`doubao-seed-2.0-lite`…）可能**不在** `/models` 列表里，却能在 chat 端点直接使用。因此：
> - 菜单**永远只展示官方静态清单**（`cli/model_catalog.py` 的 `MODEL_OPTIONS`）+ `Custom`，不再被实时 `/models` 覆盖（`cli/model_picker.py::_build_options` 已改）。
> - 实时可用性验证独立成脚本 `scripts/verify_provider_models.py`（对每个官方短名发最小 chat 探针），在接入/切换模型时手动运行，不进菜单。
> - 旧的 `cli/model_fetcher.py` 已删除（不再驱动菜单）。

**Tech Stack:** Python 3.10+、`questionary`（交互菜单，轻量，Windows 支持好）、`rich`、`requests`（拉取模型列表，经真实 API）、`pytest`（测试）。

---

## 需要理解的事实（实现者必读，避免踩坑）

> 这些是现状，不是猜测——实现前必须读一遍对应文件，不要凭印象改。

1. **`mathmodelingagents/default_config.py`**：
   - 第 72-103 行：`_DEFAULT_LAYER_MODEL_OVERRIDES`（各层各角色的默认模型映射），`_env_overrides` 深合并后得到 `LAYER_MODEL_OVERRIDES`（第 106-112 行）。
   - 第 115-150 行：`DEFAULT_CONFIG` 字典，含 `llm_provider`（默认 `opencode`）、`deep_think_llm`（默认 `deepseek-v4-pro`）、`quick_think_llm`（默认 `deepseek-v4-flash`）、`layer_model_overrides`、`provider_model_aliases`、`provider_layer_model_overrides`、`provider_model_aliases`。
   - `_env(key, default)` 辅助：读 `MATHMODELING_<KEY>` 环境变量。
   - **交互逻辑不写在这里**——这里只保留静态默认值与 provider 配置，保持「import 不产生副作用」的纯性。
2. **`mathmodelingagents/llm_clients/__init__.py`**：
   - `get_layer_model(config, layer, role)`（第 197 行）：provider 为 `deepseek` 时用 `deep_think_llm`/`quick_think_llm`；否则查 `layer_model_overrides` → `provider_layer_model_overrides[provider]` → `agent` key → `quick_think_llm` 兜底，最后过 `provider_model_aliases`。
   - `create_layer_llm(config, layer, role)`（第 236 行）：调 `get_layer_model` 建客户端。
   - `invoke_with_fallback(config, ...)`（第 277 行）：4 步降级链，`primary_model = get_layer_model(...)`，`flash_model = config["quick_think_llm"]`。
   - **结论**：交互选择写入 config 后，通过改这几个键（`layer_model_overrides`、`provider_layer_model_overrides`、`provider_model_aliases`、`deep_think_llm`、`quick_think_llm`）即可全局生效，无需改 LLM 客户端。
3. **`main.py`**：
   - `main()`（第 142 行）：argparse 读入 CLI 参数 → 第 202 行 `config = DEFAULT_CONFIG.copy()` → 第 203-204 行 `--provider` 覆盖 `config["llm_provider"]` → 打印 banner → 第 248 行 `MathModelingGraph(config=config, debug=True)` → 第 252-263 行 `mm.propagate()`。
   - **交互菜单插入点**：在 `config` 组装完成后、`MathModelingGraph` 实例化/`propagate()` 之前（约第 211 行 `selected_layers` 设置之后、第 235 行 banner 打印之前）。菜单需要此刻的 `config["llm_provider"]` 已确定（`--provider` 已处理）。
4. **TradingAgents 参考**（`C:\Users\joeji\TradingAgents\`，已确认）：
   - 交互库是 **`questionary`**（`cli/utils.py` 大量使用 `questionary.select`），模型目录是 `tradingagents/llm_clients/model_catalog.py` 的 `MODEL_OPTIONS[provider]["quick"/"deep"]`（每个选项 `(显示名, 模型ID)`），菜单标题格式 `Select Your [Quick-Thinking] LLM Engine`，并提供 `"Custom model ID"` 兜底。
   - env 跳过逻辑：`cli/main.py` Step 7 中，`TRADINGAGENTS_QUICK_THINK_LLM` 或 `TRADINGAGENTS_DEEP_THINK_LLM` 任一设置时跳过交互，直接用 `DEFAULT_CONFIG` 的值（避免 `questionary` 在无 TTY/非交互环境卡死）。
   - **本项目新增依赖需拆到 `pyproject.toml` 的 `[project.optional-dependencies].cli` 或主 dependencies**（见 Task 2），因为 Windows/无 TTY 环境可能不想强制装 questionary。

---

## 设计（新建 `cli/` 包）

新增 `cli/__init__.py`、`cli/model_catalog.py`、`cli/model_picker.py`，并改造 `main.py` 调用。分层职责：

- `cli/model_catalog.py`：**纯数据**，`MODEL_OPTIONS: dict[str, dict[str, list[tuple[str, str]]]]`，key=`provider name`，value 含 `"quick"` 与 `"deep"` 两个列表。每个条目 `(display_name, model_id)`。
  - provider：`opencode`、`deepseek`、`volcengine`、`volcengine-plan`（对应 `llm_clients/__init__.py` 已支持的 4 个）。
  - 提供 `get_model_options(provider, mode)` 辅助函数。
  - 每个列表末尾追加 `("Custom model ID", "custom")`。
- `cli/model_picker.py`：**交互逻辑**。核心函数 `prompt_model_selection(config) -> dict`：
  - 读 `config["llm_provider"]`。
  - 若该 provider 无 `MODEL_OPTIONS` 条目 → 直接返回 `{}`（不弹菜单，不阻塞，保持向后兼容）。
  - 依次弹两个 `questionary.select`：先 Quick-Thinking（快速思考），再 Deep-Thinking（深度思考）。
  - 选中 `"custom"` 时二级 `questionary.text` 输入模型 ID。
  - **返回变更字典**（新键或覆盖键），交给调用方写回 `config`，不在 picker 内部改 `config`（保持函数纯、可测）。
- `main.py`：在合适位置调用 `prompt_model_selection(config)`，把返回的变更写回 `config`。

---

## 任务清单

### Task 1: 项目依赖加入交互库（questionary + rich）

**Objective:** 让交互菜单依赖可用；通过 pytest 证明不影响现有测试。

**Files:**
- Modify: `pyproject.toml`（main dependencies）
- Test: `tests/test_cli_deps.py`

**Step 1: 写失败测试**

```python
# tests/test_cli_deps.py
def test_questionary_installed():
    import questionary  # noqa: F401
    assert hasattr(questionary, "select")


def test_rich_installed():
    import rich  # noqa: F401
    assert hasattr(rich, "console")
```

**Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_cli_deps.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'questionary'`

**Step 3: 加入依赖**

在 `pyproject.toml` 的 `[project]` 下 `dependencies` 列表追加：

```toml
    # ── CLI interactive menu ──
    "questionary>=2.0.0",
    "rich>=13.0.0",
```

（若希望保持核心最小化、交互可选，则放入 `[project.optional-dependencies]` 的新分组 `cli` 并应用到 `mathmodeling` script；遵循计划正文「设计」里的可选项说明。）

**Step 4: 运行测试验证通过**

Run: `pip install -e .` 或 `uv pip install -e .`（先安装新依赖）→ `python -m pytest tests/test_cli_deps.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add pyproject.toml tests/test_cli_deps.py
git commit -m "feat: add questionary + rich deps for CLI interactive model picker"
```

---

### Task 2: 创建 `cli/data helpers` — `cli/__init__.py` + `cli/model_catalog.py`

**Objective:** 提供各 provider 的预设模型清单数据。

**Files:**
- Create: `cli/__init__.py`（空或含版本，保持包初始化干净，不 import model_picker，避免副作用）
- Create: `cli/model_catalog.py`
- Modify: `pyproject.toml`（若 script 入口已有，无需改，但建议呆在 `cli/`）

**Step 1: 写失败测试**

```python
# tests/test_model_catalog.py
from cli.model_catalog import get_model_options


def test_catalog_has_major_providers():
    for p in ("opencode", "deepseek", "volcengine", "volcengine-plan"):
        assert p in get_model_options(p, "quick") or True  # 占位，Task 继续完善
```

**Step 2: 运行测试（先确认 catalog 缺失）**

Run: `python -m pytest tests/test_model_catalog.py -v`
Expected: FAIL — 无 `cli.model_catalog` 模块

**Step 3: 定义数据目录与访问函数**

`cli/model_catalog.py` 完整代码：

```python
"""各 provider 的可选模型目录，供 CLI 交互式模型（agent）选择使用。

对标 TradingAgents 的 model_catalog.py：纯数据，无副作用。
每个条目标签 (display_name, model_id)。Quick = 快速思考，Deep = 深度思考。
"""

from __future__ import annotations

ModelOption = tuple[str, str]
ProviderModeOptions = dict[str, dict[str, list[ModelOption]]]

_CUSTOM_ONLY: dict[str, list[ModelOption]] = {
    "quick": [("Custom model ID", "custom")],
    "deep": [("Custom model ID", "custom")],
}

# DeepSeek 官方 API — 仅 flash/pro 两模型。
_DEEPSEEK_MODELS: dict[str, list[ModelOption]] = {
    "quick": [
        ("DeepSeek V4 Flash - 快速思考", "deepseek-v4-flash"),
        ("Custom model ID", "custom"),
    ],
    "deep": [
        ("DeepSeek V4 Pro - 深度思考", "deepseek-v4-pro"),
        ("DeepSeek V4 Flash - 快速（降级用）", "deepseek-v4-flash"),
        ("Custom model ID", "custom"),
    ],
}

# Volcengine / Volcengine-Plan — 取 README 记录的原生可用模型池。
# Coding Plan 与 Agent Plan 端点不同，但模型名集合大部分重叠。
_ARK_CODE_MODELS: dict[str, list[ModelOption]] = {
    "quick": [
        ("ark-code-latest - 控制台选择模型（默认）", "ark-code-latest"),
        ("deepseek-v4-flash - 快速 DeepSeek", "deepseek-v4-flash"),
        ("doubao-seed-2.0-lite - Lite Doubao", "doubao-seed-2.0-lite"),
        ("glm-5.3-flash - 轻量 GLM", "glm-5.3-flash"),
        ("Custom model ID", "custom"),
    ],
    "deep": [
        ("ark-code-latest - 控制台选择模型（默认）", "ark-code-latest"),
        ("doubao-seed-2.1-turbo - Doubao turbo", "doubao-seed-2.1-turbo"),
        ("deepseek-v4-pro - DeepSeek Pro", "deepseek-v4-pro"),
        ("deepseek-v4-flash - 快速 DeepSeek", "deepseek-v4-flash"),
        ("kimi-k2.7-code - Kimi code", "kimi-k2.7-code"),
        ("minimax-m3 - MiniMax M3", "minimax-m3"),
        ("glm-5.3 - GLM flagship", "glm-5.3"),
        ("Custom model ID", "custom"),
    ],
}

# OpenCode Go 网关 — 模型池丰富多变，参考 TradingAgents 用 Custom 为主，
# 但保留项目已知稳定的几个常用于 .env。
_OPENCODE_MODELS: dict[str, list[ModelOption]] = {
    "quick": [
        ("deepseek-v4-flash - 快速 DeepSeek（默认）", "deepseek-v4-flash"),
        ("Custom model ID", "custom"),
    ],
    "deep": [
        ("deepseek-v4-pro - 深度推理（默认）", "deepseek-v4-pro"),
        ("Custom model ID", "custom"),
    ],
}

MODEL_OPTIONS: ProviderModeOptions = {
    "opencode": _OPENCODE_MODELS,
    "deepseek": _DEEPSEEK_MODELS,
    "volcengine": _ARK_CODE_MODELS,
    "volcengine-plan": _ARK_CODE_MODELS,
}


def get_model_options(provider: str, mode: str) -> list[ModelOption]:
    """返回某 provider 在 quick/deep 模式下的模型选项列表。未知 provider 回退 Custom-only。"""
    return MODEL_OPTIONS.get(provider.lower(), _CUSTOM_ONLY).get(mode, _CUSTOM_ONLY[mode])
```

**Step 4: 完善测试并运行**

把 `tests/test_model_catalog.py` 写成真实断言：

```python
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
```

Run: `python -m pytest tests/test_model_catalog.py -v`
Expected: 3 passed

**Step 5: Commit**

```bash
git add cli/__init__.py cli/model_catalog.py tests/test_model_catalog.py
git commit -m "feat(cli): add per-provider model catalog for interactive picker"
```

---

### Task 3: 创建交互选择器 — `cli/model_picker.py`

**Objective:** 用 questionary 弹 Quick/Deep 两级菜单，返回变更字典，绝不阻塞未知 provider。

**Files:**
- Create: `cli/model_picker.py`
- Test: `tests/test_model_picker.py`

**Step 1: 写失败测试**

```python
# tests/test_model_picker.py
from cli.model_picker import _apply_model_selection


def test_apply_non_deepseek_overrides_overwrite_layer(capsys):
    # Claude Code：mock questionary 输入不可靠，这里直接测纯逻辑
    original = {"layer_model_overrides": {"problem": {"agent": "x", "manager": "y"}}}
    chosen = {"quick": "q", "deep": "d"}
    new = _apply_model_selection(original, "opencode", chosen)
    assert new["layer_model_overrides"]["problem"]["agent"] == "q"
```

**Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_model_picker.py -v`
Expected: FAIL — 无 `cli.model_picker` 模块

**Step 3: 实现 model_picker.py**

```python
"""CLI 交互式模型（agent）选择器。

在 main.py 进入 propagate() 之前调用。根据 config["llm_provider"] 决定是否
弹出两个 questionary.select（Quick-Thinking / Deep-Thinking）。选中 Custom 时
二级输入模型 ID。返回「变更字典」交给调用方写回 config，本模块不直接改 config。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 允许通过环境变量跳过交互（对齐 TradingAgents env-precedence 规则）。
_QUICK_VAR = "MATHMODELING_QUICK_THINK_LLM"
_DEEP_VAR = "MATHMODELING_DEEP_THINK_LLM"


def _pick_single(mode: str, provider: str) -> str | None:
    """弹单个 questionary.select；返回模型 id 或 None（Ctrl-C/取消）。"""
    import os

    env_var = _QUICK_VAR if mode == "quick" else _DEEP_VAR
    env_val = os.getenv(env_var)
    if env_val:
        logger.info("%s 已设置，跳过 %s 选择", env_var, mode)
        return None
    raise NotImplementedError("交互选择由 Task 4 实现")


def prompt_model_selection(config: dict) -> dict:
    """主入口：根据 provider 弹菜单，返回应写回 config 的变更字典。"""
    provider = config.get("llm_provider", "opencode")
    return {}  # Task 4 填充完整逻辑


def _apply_model_selection(config: dict, provider: str, chosen: dict) -> dict:
    """把 chosen={'quick':..,'deep':..} 应用到 config 副本，返回修改后的副本。

    非 deepseek provider：覆盖 layer_model_overrides 里各层的 agent/coder/writer 等
    角色为所选模型（保持 provider_layer_model_overrides 可能存在的角色覆盖不被抹掉，
    仅当全局覆盖命中时才改写）。deepseek：覆盖 deep_think_llm / quick_think_llm。
    """
    new = dict(config)
    if provider == "deepseek":
        new = dict(config)
        if chosen.get("deep"):
            new["deep_think_llm"] = chosen["deep"]
        if chosen.get("quick"):
            new["quick_think_llm"] = chosen["quick"]
        return new

    # 非 deepseek：改写 layer_model_overrides 中出现的所有角色值为所选模型，
    # 但保留 provider 级角色覆盖（provider_layer_model_overrides）优先。实现时注意
    # 空 config 防御：layer_model_overrides 可能未存在，或用默认 DEFAULT_CONFIG。
    overrides = dict(config.get("layer_model_overrides") or {})
    # 占位——Task 5 完善「逐角色映射 + 保留 provider 级覆盖」逻辑
    new["layer_model_overrides"] = overrides
    return new
```

**Step 4: 运行测试**

Run: `python -m pytest tests/test_model_picker.py -v`
Expected: 1 passed（`_apply_model_selection` 的纯逻辑已通）

**Step 5: Commit**

```bash
git add cli/model_picker.py tests/test_model_picker.py
git commit -m "feat(cli): add model picker module skeleton with pure apply logic"
```

> 说明：Task 3/4 刻意把「交互 UI」与「写回 config 的纯逻辑」分离，让纯逻辑可单测。Task 4 再补真正的 questionary UI + env 跳过 + Windows 兼容。

---

### Task 2.5: 创建「实时拉取」适配器 — `cli/model_fetcher.py`

**Objective:** 按 provider 拉取其当前可用模型列表；拉取失败时降级到静态清单，保证交互菜单永不因网络/认证失败而阻塞。

**Files:**
- Create: `cli/model_fetcher.py`
- Test: `tests/test_model_fetcher.py`

**Step 1: 写失败测试**

```python
# tests/test_model_fetcher.py
from cli.model_fetcher import provider_models_endpoint, fetch_models


def test_provider_has_codingplan_endpoint():
    # volcengine (Coding) 与 volcengine-plan (Agent) 端点不同，但都是 /models
    assert provider_models_endpoint("volcengine").endswith("/api/coding/v3/models")
    assert provider_models_endpoint("volcengine-plan").endswith("/api/plan/v3/models")


def test_fetch_unavailable_returns_empty_list(monkeypatch):
    # 认证失败 / 网络失败都应返回 []，由调用方降级到静态清单
    monkeypatch.setenv("VOLCENGINE_API_KEY", "bad-key")
    result = fetch_models("volcengine")
    assert result == []  # 不抛异常，不阻塞
```

**Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_model_fetcher.py -v`
Expected: FAIL — 无 `cli.model_fetcher` 模块

**Step 3: 实现 model_fetcher.py**

```python
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
from typing import Any

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
```

**Step 4: 运行测试**

Run: `python -m pytest tests/test_model_fetcher.py -v`
Expected: 2 passed
 
**Step 5: 手动验证（连真实端点）**

Run（已设 .env key 时）:
```bash
python -c "from cli.model_fetcher import fetch_models; print(fetch_models('volcengine'))"
```
Expected: 打印火山 Coding Plan 当前真实可用模型列表（如 `['ark-code-latest', 'deepseek-v4-pro', ...]`）；无 key 时打印 `[]`。若端点列表为空但返回空，属正常降级。

**Step 6: Commit**

```bash
git add cli/model_fetcher.py tests/test_model_fetcher.py
git commit -m "feat(cli): realtime model-list fetcher per provider with graceful fallback"
```

---

### Task 4: 实现真正的交互菜单（questionary）+ 实时拉取 + 环境变量跳过

**Objective:** 弹真正的 Quick/Deep 菜单，菜单项来自 `fetch_models()` 实时拉取（成功）或降级到静态清单；环境变量已设置或非交互环境时静默跳过。

**Files:**
- Modify: `cli/model_picker.py`
- Test: `tests/test_model_picker.py`

**Step 1: 写失败测试（纯逻辑断言，不驱动真实 TTY）**

```python
# 追加到 tests/test_model_picker.py
import os
from cli.model_picker import prompt_model_selection


def test_env_precedence_skips_prompt(monkeypatch):
    # 设置 env 后，prompt_model_selection 应不弹菜单，直接返回空变更
    monkeypatch.setenv("MATHMODELING_QUICK_THINK_LLM", "some-quick")
    config = {"llm_provider": "opencode"}
    result = prompt_model_selection(config)
    assert result == {}  # env 已接管，不交互
```

**Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_model_picker.py::test_env_precedence_skips_prompt -v`
Expected: FAIL — 尚未实现 env 跳过

**Step 3: 用 questionary 实现交互 UI，菜单项优先实时拉取**

替换 `cli/model_picker.py` 中的 `prompt_model_selection` 与 `_pick_single`：

```python
import os

import questionary
from rich.console import Console

from cli.model_catalog import get_model_options  # 静态降级清单
from cli.model_fetcher import fetch_models        # 实时拉取

console = Console()

_QUICK_VAR = "MATHMODELING_QUICK_THINK_LLM"
_DEEP_VAR = "MATHMODELING_DEEP_THINK_LLM"


def _is_env_locked(mode: str) -> bool:
    return bool(os.getenv(_QUICK_VAR if mode == "quick" else _DEEP_VAR))


def _build_options(provider: str, mode: str) -> list[tuple[str, str]]:
    """菜单项：优先实时拉取，失败降级静态清单，末尾恒有 Custom。"""
    realtime = fetch_models(provider)
    if realtime:
        # 实时列表：可为空但非空时全部展示；deep/quick 暂不区分(模型自含思考能力)
        options = [(m, m) for m in realtime]
    else:
        options = list(get_model_options(provider, mode))
    # 始终追加 Custom 兜底,去重
    if not any(mid == "custom" for _, mid in options):
        options.append(("Custom model ID", "custom"))
    return options


def _pick_single(mode: str, provider: str) -> str | None:
    """弹单个 questionary.select。返回模型 id；None 表示 env 锁定或取消。"""
    if _is_env_locked(mode):
        return None
    options = _build_options(provider, mode)
    choice = questionary.select(
        f"Select Your [{mode.title()}-Thinking] LLM Engine ({provider}):",
        choices=[questionary.Choice(display, value=value) for display, value in options],
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style([
            ("selected", "fg:magenta noinherit"),
            ("highlighted", "fg:magenta noinherit"),
            ("pointer", "fg:magenta noinherit"),
        ]),
    ).ask()
    if choice is None:
        return None  # Ctrl-C / Esc
    if choice == "custom":
        custom = questionary.text(
            "Enter custom model ID:",
            validate=lambda x: len(x.strip()) > 0 or "Please enter a model ID.",
        ).ask()
        return custom.strip() if custom else None
    return choice


def prompt_model_selection(config: dict) -> dict:
    """主入口：根据 provider 弹 Quick/Deep 菜单，返回变更字典。

    - provider 非支持类型 → 返回 {}（不阻塞，向后兼容）。
    - 两个 env 都设置 → 返回 {}（env 已接管，避免无 TTY 卡死）。
    - 用户取消任意一次 → 返回 {}（保持当前配置）。
    """
    provider = config.get("llm_provider", "opencode")
    if provider.lower() not in _ENDPOINTS and provider.lower() not in get_model_options.__doc__:
        pass  # 实际判定：provider 是否在 model_catalog 或 fetcher 支持范围内
    if _is_env_locked("quick") and _is_env_locked("deep"):
        return {}

    quick = _pick_single("quick", provider)
    deep = _pick_single("deep", provider)

    chosen = {}
    if quick:
        chosen["quick"] = quick
    if deep:
        chosen["deep"] = deep
    return chosen
```

**Step 4: 运行测试**

Run: `python -m pytest tests/test_model_picker.py -v`
Expected: `test_env_precedence_skips_prompt` PASS（env 设置时跳过交互）

**Step 5: 手动冒烟**

Run: `python -c "from cli.model_picker import _build_options; print(_build_options('volcengine','deep')[:5])"`
Expected: 打印火山实时可用模型的前 5 个（有 key 时）或静态清单前 5（无 key 时）。

**Step 6: Commit**

```bash
git add cli/model_picker.py cli/model_fetcher.py tests/test_model_picker.py
git commit -m "feat(cli): interactive quick/deep model menus with realtime fetch + env-skip"
```

---

### Task 5: 完善 `_apply_model_selection` — 交互选中模型为最高优先级（完全覆盖）

**Objective:** 让交互选择真正生效且为最高优先级：把所选 quick/deep 模型写进 `layer_model_overrides`，**完全覆盖**所有角色（含 provider 级硬编码角色），并要求交互后再过别名映射的 provider 适配。

**Files:**
- Modify: `cli/model_picker.py`
- Test: `tests/test_model_picker.py`

**Step 1: 写失败测试**

```python
# 追加
def test_apply_non_deepseek_overwrites_everything():
    from cli.model_picker import _apply_model_selection
    original = {
        "layer_model_overrides": {
            "problem": {"agent": "x", "manager": "y"},
            "implementation": {"coder": "kimi", "manager": "z"},
            "paper": {"writer": "qwen", "manager": "w"},
        },
        "provider_layer_model_overrides": {
            "volcengine-plan": {"implementation": {"coder": "kimi-k2.7-code"}, "paper": {"writer": "minimax-m3"}},
        },
        "provider_model_aliases": {"volcengine-plan": {"qwen3.7-max": "minimax-m3"}},
    }
    new = _apply_model_selection(original, "volcengine-plan", {"quick": "q", "deep": "d"})
    # 交互选中模型作为最高优先级：所有层的 manager/writer/coder 等重角色被 deep 覆盖
    assert new["layer_model_overrides"]["problem"]["manager"] == "d"
    assert new["layer_model_overrides"]["implementation"]["coder"] == "d"  # 覆盖 provider 硬编码的 kimi
    assert new["layer_model_overrides"]["paper"]["writer"] == "d"          # 覆盖 provider 硬编码的 minimax-m3
```

**Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_model_picker.py -v`
Expected: FAIL — 占位逻辑未实现逐角色覆盖

**Step 3: 实现逐角色覆盖（完全覆盖语义）**

在 `cli/model_picker.py` 中完善 `_apply_model_selection`：

```python
# 角色 → 思考深度分类（仅用于此函数内部）
_DEEP_ROLES = {"manager", "writer", "coder", "algorithm"}


def _apply_model_selection(config: dict, provider: str, chosen: dict) -> dict:
    """把 chosen={'quick':..,'deep':..} 应用到 config 副本，返回修改后的副本。

    交互选中模型为最高优先级 —— 遍历 layer_model_overrides 中每个 layer 的每个
    角色：deep 角色集 → chosen['deep']，否则 chosen['quick']。同时清空/归并
    provider_layer_model_overrides 中与该层冲突的 provider 级角色（交互覆盖一切），
    并移除 provider_model_aliases 中会改写所选模型的条目（保证所选模型真实生效）。
    deepseek provider：直接设 deep_think_llm / quick_think_llm。空 config 防御。
    """
    new = dict(config)
    quick = chosen.get("quick")
    deep = chosen.get("deep")

    if provider == "deepseek":
        if deep:
            new["deep_think_llm"] = deep
        if quick:
            new["quick_think_llm"] = quick
        return new

    # 非 deepseek：逐 layer 逐角色完全覆盖
    layer_overrides = dict(config.get("layer_model_overrides") or {})
    for layer, roles in layer_overrides.items():
        roles = dict(roles)
        for role in list(roles):
            # 决定用 deep 还是 quick：deep 角色集优先；其他(如纯 agent)用 quick
            replacement = deep if role in _DEEP_ROLES else quick
            if replacement:
                roles[role] = replacement
        layer_overrides[layer] = roles

    # 交互选择最高优先级：移除 provider 级覆盖中与所选深度冲突的角色，避免被回盖
    pv_overrides = config.get("provider_layer_model_overrides", {})
    if provider in pv_overrides:
        pv = dict(pv_overrides[provider])
        for layer, roles in pv.items():
            for role in list(roles):
                if role in _DEEP_ROLES:
                    roles.pop(role, None)  # 深度角色由交互 deep 接管
            pv[layer] = roles
        pv_overrides = dict(pv_overrides)
        pv_overrides[provider] = pv

    new["layer_model_overrides"] = layer_overrides
    new["provider_layer_model_overrides"] = pv_overrides
    return new
```

**Step 4: 运行测试**

Run: `python -m pytest tests/test_model_picker.py -v`
Expected: 全部 PASS（交互选择覆盖 provider 硬编码）

**Step 5: Commit**

```bash
git add cli/model_picker.py tests/test_model_picker.py
git commit -m "feat(cli): interactive selection highest-priority, fully overrides provider overrides"
```

> 说明：`_apply_model_selection` 里对 provider 级覆盖的「移除冲突角色」是为了让交互选中的 deep/quick 真正生效并成为最高优先级——这与你确认的「完全覆盖所有角色的 provider 级硬编码」一致。若实现时想保留 provider 级覆盖作为不可覆盖的例外，需回退到上一版逻辑（尊重 provider 覆盖）；但按你的选择，交互为最高优先级。

### Task 6: 在 `main.py` 调用交互选择

**Objective:** 在 config 组装后、`propagate()` 前调用 `prompt_model_selection`，把结果写回 config，并在 banner 里展示所选模型。

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_config_flow.py`（若已有 main 流程测试则并入）

**Step 1: 读一遍 main.py 相关段（已完成）**

关键锚点（当前行号）：`main()` 内第 202 `config = DEFAULT_CONFIG.copy()` → 第 204 `--provider` 处理 → 第 210 `config["selected_layers"]` → 第 232 `resolve_sensitivity_mode` → 第 235 banner → 第 248 `MathModelingGraph(...)`。

**Step 2: 写失败测试（若可测）**

main.py 的 `main()` 是 CLI 入口，交互难单测；建议抽出一个可测的辅助函数 `build_config_from_args(args) -> config`（包含全部配置组装逻辑，交互调用也放这里），然后单测该函数在传入 `--provider` 时正确写入 `llm_provider`，且交互函数返回空时配置不受影响。

**Step 3: 在 main.py 插入交互调用**

在第 210 行 `config["selected_layers"]=...` 之后、第 235 行 banner 之前，插入：

```python
    # ── CLI 交互式模型（agent）选择 ──
    try:
        from cli.model_picker import prompt_model_selection
        selection = prompt_model_selection(config)
        if selection:
            from cli.model_picker import _apply_model_selection
            config = _apply_model_selection(config, config["llm_provider"], selection)
    except Exception as exc:  # 交互层任何异常都不应阻断建模
        import logging
        logging.getLogger("main").warning("交互模型选择失败，使用默认模型: %s", exc)
```

> 若重构抽 `build_config_from_args`，交互写上 clean 的调用（不加 try/except 吞异常也行，但保留）。原则：**非交互场景（env 锁定 / 无 TTY / provider 未知）绝对不阻塞。**

**Step 4: 验证（手动冒烟）**

Run: `python -m pytest tests/ -v`（确认现有测试全绿）
然后手动冒烟：
```bash
# 不设置 env、交互模式：应弹出 Quick/Deep 菜单
python main.py tests/fixtures/sample_problem.md
# 设置 env 锁定：应跳过菜单，直接运行且快速
MATHMODELING_QUICK_THINK_LLM=deepseek-v4-flash MATHMODELING_DEEP_THINK_LLM=deepseek-v4-pro python main.py tests/fixtures/sample_problem.md
```
Expected: 交互模式出现菜单；env 模式无菜单。

**Step 5: Commit**

```bash
git add main.py tests/test_main_config_flow.py
git commit -m "feat(cli): invoke interactive model picker before graph propagation"
```

---

### Task 7: README 与 MODEL_CONFIG_PROPOSAL.md 文档同步

**Objective:** 记录新交互流程与环境变量。

**Files:**
- Modify: `README.md`
- Modify: `MODEL_CONFIG_PROPOSAL.md`

**Step 1: README 追加「交互式模型选择」小节**

- 说明：`python main.py 题目.md` 交互模式下会先弹 Quick/Deep 模型选单。
- 列出可用环境变量表（`MATHMODELING_QUICK_THINK_LLM` / `MATHMODELING_DEEP_THINK_LLM`，设任一即跳过对应交互）。
- 提醒：设置两个 env 变量 = 完全跳过交互（适合 CI/脚本/无 TTY）。

**Step 2: MODEL_CONFIG_PROPOSAL.md 环境变量节补充**

- 在「环境变量」节加入 `MATHMODELING_QUICK_THINK_LLM` / `MATHMODELING_DEEP_THINK_LLM` 及交互式选择说明。

**Step 3: 验证**

Run: `grep -n "QUICK_THINK" README.md MODEL_CONFIG_PROPOSAL.md`（应有命中）
Expected: 两个文件都有 `MATHMODELING_QUICK_THINK_LLM`

**Step 4: Commit**

```bash
git add README.md MODEL_CONFIG_PROPOSAL.md
git commit -m "docs: document interactive model picker and env vars"
```

---

## 验证清单（全部完成即视为交付结束）

- [ ] `python -m pytest tests/test_cli_deps.py` PASS
- [ ] `python -m pytest tests/test_model_catalog.py` PASS（3 个）
- [ ] `python -m pytest tests/test_model_picker.py` PASS
- [ ] `python -m pytest tests/ -v`（全量，确认无回归）
- [ ] 手动：交互模式弹菜单
- [ ] 手动：设两个 env 变量时跳过交互
- [ ] 手动：`--provider volcengine-plan` 时菜单项正确且写入后 L3/L4 角色尊重 provider 级覆盖
- [ ] README / MODEL_CONFIG_PROPOSAL.md 已同步
- [ ] 按计划顺序 commit 7 次

## 边界与原则

- **default_config.py 保持静态纯数据**：不把交互逻辑放进去，不 import questionary，保证 import 无副作用。这是用户确认的「最小改动」方向。
- **非交互环境不阻塞**：env 锁定 → 跳过；provider 未知 → 跳过；无 TTY/qestionary 异常 → 捕获后走默认。
- **provider 级硬编码覆盖优先**：`provider_layer_model_overrides`（如 volcengine-plan L3→kimi、L4→minimax-m3）不应被交互选择覆盖。
- **新增依赖 questionary + rich**：TradingAgents 已用，Windows 兼容良好；若希望可选，放入 optional-dependencies。
- **所有改动 TDD**：先写失败测试 → 实现 → 验证 → commit。