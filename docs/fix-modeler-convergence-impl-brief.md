# 任务书：Modeler 工具调用收敛机制（阈值强化 + 统一 prompt 收敛铁律）

## 背景

客观评测探针（`scripts/probe_modeler_tool_calls.py`，B 题真实数据，deepseek-v4-pro）实测
发现：ModelerC 在「纯自然停止」循环下发散——连续 14 轮纯工具调用（每轮 content 为空，
全程 run_code），从 FFT 一路深化到 OPD 峰值/自相关/零交叉/去趋势，从不停手写方案。

两个推论：
1. `max_iterations=10`（已调到 15）不够，且「一直调到自然停止」会无限烧 token，不可行。
2. `_run_modeler_turn` 里原有的 `consecutive_tool_only >= 5` 提醒是**必要的收敛保护**——
   铁证：B 题 Layer2 里建模师 A 的方案开头写「（已被要求停止调用工具，以下为基于已获得
   工具结果的文字方案）」，正是该提醒打断了 A 的无限验证。

设计取向（用户拍板）：**1+3 组合** —— 保留并强化提醒机制 + 统一改三个 modeler 的 prompt，
从源头治「无穷细化」的发散。不动已落地的 `max_iterations=15`、`_looks_like_complete_plan`
提前退出、`_extract_last_substantial_text` 兜底放宽（三者均已在共享层，保持原样）。

## 改动清单

### 1. `mathmodelingagents/agents/__init__.py` — `_run_modeler_turn` 提醒阈值与措辞

当前（约 L1237-1240 附近）：

```python
        if consecutive_tool_only >= 5:
            messages.append(HumanMessage(content=(
                "请停止调用工具，基于已获得的工具结果直接给出你的文字建模方案。"
            )))
            consecutive_tool_only = 0
            logger.warning(f"[{layer_tag}] {agent_tag} 连续 5 次仅工具调用，已注入纯文本提醒")
```

改为：

```python
        if consecutive_tool_only >= 8:
            messages.append(HumanMessage(content=(
                "验证已充分，请立即停止调用工具，基于已获得的工具结果直接输出完整的文字"
                "建模方案（按你的输出模板）。禁止继续调用工具做额外验证。"
            )))
            consecutive_tool_only = 0
            logger.warning(f"[{layer_tag}] {agent_tag} 连续 8 次仅工具调用，已注入停止提醒")
```

（阈值 5 → 8，措辞从「请停止」强化为「验证已充分，立即停止并输出方案，禁止继续验证」，
logger 文案同步更新 5→8。其余逻辑不动。）

### 2. `mathmodelingagents/agents/utils/prompt_templates.py` — 三个 modeler 统一加收敛铁律

在 `get_modeler_a_prompt` / `get_modeler_b_prompt` / `get_modeler_c_prompt` 三处，各自在
「## 工具权限: ...」那一行**之后**、`## 输出模板` 之前，插入**完全相同**的以下段落：

```
## 工具调用收敛铁律
- 工具调用只用于关键验证：复现 A/B 代码、检索候选模型、验证你的方案核心结论。
- 关键验证完成后，必须立即停止调用工具、输出完整的文字建模方案（按下方输出模板）。
- 禁止对同一份数据反复更换统计量/波段/算法做无限细化——验证以达到「能支撑方案结论」为限。
- 若已连续多次调用工具仍未写出方案，必须在最近一次有效结果的基础上给出文字方案，
  并明确标注尚待验证的部分。
```

三个 modeler 的铁律文案必须**逐字一致**（仅这三个函数，不动 manager 或其他 prompt）。

## Do NOT 清单

- **不得**引入任何 `agent_name == "modeler_c"` 特判，不新建独立 modeler 节点。铁律是
  三个 modeler 共享的同一段文案，阈值是 `_run_modeler_turn` 内对所有 modeler 统一生效。
- **不得**改动 `max_iterations=15`（`_make_modeler_node` 内，已落地）。
- **不得**改动 `_looks_like_complete_plan`、`_extract_last_substantial_text`、`_extract_final_output`、
  `_run_tool_loop`、`_sanitize_tool_pairing`。
- **不得**改动 `get_modeling_manager_prompt` 或其他层的 prompt。
- **不得**改动 graph 拓扑、AgentState、状态键。
- 既有日志/文案（除上文明确写出的两处）逐字保留。

## 可运行验收标准（AC）

新建 `scripts/verify_modeler_convergence.py`（仿现有 verify 脚本风格），至少覆盖：

1. `py_compile` 两个被改文件无错误。
2. `import mathmodelingagents.agents` 冒烟成功。
3. 三个 modeler prompt 均含「工具调用收敛铁律」段落，且三者的铁律文案逐字一致。
4. `get_modeler_c_prompt()` / `get_modeler_a_prompt()` / `get_modeler_b_prompt()` 中
   「工具调用收敛铁律」出现在「工具权限」与「输出模板」之间（顺序断言）。
5. 源码 `mathmodelingagents/agents/__init__.py` 含 `consecutive_tool_only >= 8` 且不再含
   `consecutive_tool_only >= 5`（阈值已更新）。
6. 回归：`scripts/verify_modeler_c_tool_loop.py` 仍 9/9 PASS（此前提前退出+兜底不被破坏）。

运行方式（仓库根目录）：

```bash
export PYTHONPATH=
.venv/Scripts/python.exe scripts/verify_modeler_convergence.py
.venv/Scripts/python.exe scripts/verify_modeler_c_tool_loop.py
```

全部 PASS 并打印 `ALL CHECKS PASSED` 才算通过。