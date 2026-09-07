# Implementation Brief: 辩论历史滚动窗口（3 轮）

> 由 Hermes 设计，Claude Code 执行，Hermes 验收复核。
> 前置：附件剥离 + 降级链止血已入库（`docs/input-overflow-and-fallback-hardening-impl-brief.md`）。

## Background

L2 辩论的 `a_history` / `b_history` / `c_history` 三个字段随轮次**无脑累积**（`_make_modeler_node` 里 `debate[history_key] = existing + f"\n\n--- 第 {round_count} 轮 ---\n{result}"`），而 `_build_context`（`agents/__init__.py:740-745`）把这三人历史**全量**塞给每个建模师和 manager。第 N 轮上下文是首轮的 ~N 倍，是排除数据堆之后的刚性膨胀源。

本轮引入**滚动窗口**：喂给 LLM 的历史只保留最近 K 轮（K=3）。

## Design

**核心原则：落盘全量、喂 prompt 精简。** history 字段继续全量累积（第 10 轮仍是 10 段，报告/审计/`layer_outputs` 依赖全量），仅在 `_build_context` 组装 prompt 时裁剪。轮数配置、累积逻辑、`current_*` 注入逻辑**一律不动**。

新增 helper（放 `agents/__init__.py`，与 `_record`/`_parse_sensitivity_json` 并列的模块级函数）：

```python
def _take_last_rounds(history: str, k: int) -> str:
    """截取辩论历史中最近 k 轮发言。

    轮次分隔符为独占一行的 `--- 第 N 轮 ---`（见 _make_modeler_node 的累积格式）。
    用 ^--- 第 \d+ 轮 ---\s*$（MULTILINE）匹配，避免误伤发言正文里内联的「第 N 轮」字样。
    轮段数 <= k 时原样返回（不裁剪）；history 为空返回空串。
    """
    import re
    markers = list(re.finditer(r'^--- 第 \d+ 轮 ---\s*$', history, flags=re.MULTILINE))
    if len(markers) <= k:
        return history
    return history[markers[-k].start():]
```

窗口值从 config 读取，默认 3：`config.get("debate_history_window", 3)`。

## Change List

### 1. `mathmodelingagents/agents/__init__.py`
- 新增模块级函数 `_take_last_rounds`（放在 `_build_context` 之前，约 692 行附近），docstring 与上面一致。
- `_build_context` 的 modeling 分支（现 740-745 行），三个 history 注入改为应用窗口：

```python
        # ── 滚动窗口：只喂最近 K 轮历史，字段本身仍全量累积（报告依赖全量）──
        window = config.get("debate_history_window", 3)
        if debate.get("a_history"):
            parts.append(f"## 建模师 A 历史发言\n\n{_take_last_rounds(debate['a_history'], window)}")
        if debate.get("b_history"):
            parts.append(f"## 建模师 B 历史发言\n\n{_take_last_rounds(debate['b_history'], window)}")
        if debate.get("c_history"):
            parts.append(f"## 建模师 C 历史发言\n\n{_take_last_rounds(debate['c_history'], window)}")
```

   标题文案保持原样「## 建模师 A 历史发言」（不做「最近 N 轮」字样，避免轮段数 ≤ K 时误导；窗口是内部机制）。

### 2. `mathmodelingagents/default_config.py`
在「辩论配置」区（约 153-159 行，`max_debate_rounds` 附近）新增：

```python
    # 辩论历史滚动窗口：喂给 LLM 的历史发言只保留最近 N 轮（history 字段仍全量累积）。
    # 减少第 N 轮上下文随轮次线性膨胀；不影响轮数与辩论时长。
    "debate_history_window": int(_env("debate_history_window", "3")),
```

### 3. `tests/test_debate_history_window.py`（新增，TDD）
- `_take_last_rounds` 单元测试：
  - 5 轮历史、k=3 → 只含最后 3 轮（断言含第 4/5 轮标记、不含第 1/2 轮标记）
  - 2 轮历史、k=3 → 原样返回
  - 空串 → 空串
  - 无分隔符文本 → 原样返回
  - 内联「第 N 轮」字样（非独占行）不被误切
- `_build_context` 建模层集成测试：
  - 构造 `model_debate_state` 含 5 轮 a_history 的 state，调 `_build_context(state, "modeling", "modeler_b", config)`，断言输出含第 5 轮标记、不含第 1 轮标记
  - 轮段数 ≤ 3 时 context 含全部轮标记

## Acceptance Criteria（全部用项目虚拟环境，先 `export PYTHONPATH=`）

```bash
export PYTHONPATH=
cd /f/code/projects/MathModelingAgents

# 1. 全量单测
.venv/Scripts/python.exe -m pytest tests/ -v

# 2. import smoke（含新函数可导入）
.venv/Scripts/python.exe -c "from mathmodelingagents.agents import _take_last_rounds; print('ok')"
```

## Explicitly Do NOT Do

- **不得改任何轮数配置**（`max_debate_rounds` / `max_modeling_rounds` / `max_revision_rounds`），不删 DEPRECATED 标注、不改 default_config 里它们的值与注释。
- **不得改 `_make_modeler_node` 的 history 累积逻辑**（`debate[history_key] = existing + ...` 保持全量追加）。
- **不得改 `current_a/b/c_response` 的注入逻辑**（它在窗口 history 最后一段的少量重叠是既有冗余，本次不处理）。
- **不得改 `.env` / `pyproject.toml` / lock 文件**，不新增依赖（只用标准库 `re`）。
- **不得动附件剥离 / 候选池 / fail-fast / 敏感性决策**等上一轮改动。
- manager 与建模师共用同一个 `_build_context`，滚动窗口对两者同时生效即可，不要为 manager 另写一套。
- 先写失败测试（red）再最小实现（green），改完 `git diff --stat` 自核越界。