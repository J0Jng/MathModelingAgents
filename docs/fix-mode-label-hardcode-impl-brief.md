# Implementation Brief: 修复 banner Mode 标签硬编码瑕疵

## Background

`main.py` 启动 banner 里的 `Mode:` 行硬编码为「完整流程 (L1→L4)」，未感知实际执行的 `config["selected_layers"]`。

当用户用 `MATHMODELING_SELECTED_LAYERS=1` 只跑 Layer 1 时，banner 仍显示「完整流程 (L1→L4)」，与实际执行层数不符，误导用户。

## Design

抽取纯函数 `_mode_label(config: dict) -> str`，根据 `selected_layers` 生成标签：

- `config.get("explain_mode")` 为真 → 保持「恢复 (L1 已有 → L2·L3 → 解释)」不变。
- 否则按 `config.get("selected_layers")`（list[int]）映射：
  - 全层 `{1,2,3,4}`（或空、或等价含全四层）→ `"完整流程 (L1→L4)"`
  - `[1]` → `"仅 Layer 1 (问题分析)"`
  - 其他 → `"L1→L2 (问题分析→建模)"` 形式（层号 + 层名简述用 `→` 连接）

层名简述映射：`1=问题分析, 2=建模, 3=实现, 4=论文`。L5 已退役，直接忽略（`selected_layers` 里的 5 不参与标签）。

用纯函数替代 main.py 264-268 行的内联 if/else，使逻辑可独立验收（`from main import _mode_label`）。

## Change List

### `main.py`

1. 在 `build_config_from_args` 函数之后、`main()` 内部 banner 逻辑之前，新增模块级纯函数：

```python
_LAYER_SHORT = {1: "问题分析", 2: "建模", 3: "实现", 4: "论文"}

def _mode_label(config: dict) -> str:
    """根据实际执行的 selected_layers 生成 banner 的 Mode 标签。

    explain_mode（--from-layer1）优先；否则按 selected_layers 映射，
    全层显示「完整流程」，单层/部分层用「L1→L2 (层名→层名)」形式。
    """
    if config.get("explain_mode"):
        return "恢复 (L1 已有 → L2·L3 → 解释)"
    selected = [l for l in (config.get("selected_layers") or []) if l in _LAYER_SHORT]
    if not selected or set(selected) == set(_LAYER_SHORT):
        return "完整流程 (L1→L4)"
    nums = "→".join(f"L{l}" for l in sorted(selected))
    names = "→".join(_LAYER_SHORT[l] for l in sorted(selected))
    if len(selected) == 1:
        return f"仅 Layer {selected[0]} ({_LAYER_SHORT[selected[0]]})"
    return f"{nums} ({names})"
```

2. 删除原 264-268 行的内联 if/else，改为 `mode_label = _mode_label(config)`。

> 提示：原内联逻辑位于 `resolve_sensitivity_mode(config)` 之后、`_layer_roles` 定义之前（当前约 264-268 行）。

## Acceptance Criteria（验收方会独立重跑）

```bash
cd /f/code/projects/MathModelingAgents && export PYTHONPATH=
.venv/Scripts/python.exe -c "from main import _mode_label as f; \
print(repr(f({'explain_mode': True}))); \
print(repr(f({'selected_layers':[1,2,3,4]}))); \
print(repr(f({'selected_layers':[1]}))); \
print(repr(f({'selected_layers':[1,2]})))"
```

预期输出（顺序一致）：

```
'恢复 (L1 已有 → L2·L3 → 解释)'
'完整流程 (L1→L4)'
'仅 Layer 1 (问题分析)'
'L1→L2 (问题分析→建模)'
```

## Explicitly Do NOT Do

- 不修改 `_layer_roles` 硬编码列表（逐层展示模型映射是注释里明确「保守全部列出」的有意设计）。
- 不修改 explain_mode 分支的语义。
- 不新增第三方依赖。
- 不触碰 `main.py` 以外的任何文件。
- 不运行真实 LLM 端到端测试（本次只改展示字符串，验收用纯函数单测即可）。
