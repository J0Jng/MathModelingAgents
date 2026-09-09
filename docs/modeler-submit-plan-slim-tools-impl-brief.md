# Modeler 工具瘦身 + submit_plan 显式终止（impl brief）

> 状态：待执行 | 方案已由用户拍板（B 去 run_code / 保留 web_search + D submit_plan 显式终止）
> 本文档是给 Claude Code 执行的任务书，附验收标准。

## 背景

Linear 2 建模师（`modeler_a/b/c`）当前绑定三个工具 `model_search + web_search + run_code`，
一轮发言的终止依赖「模型自觉输出纯文本」＋多套补丁级保底
（`_looks_like_complete_plan` 提前退出、`consecutive_tool_only` 提醒、双层 fallback 提取）。
实测常出现：调用轮次太多（发散）、工具消息配对 400、上下文超长被拒。

**根治方向（用户已确认）**：
- **B**：modeler 去掉 `run_code`，只保留 `model_search + web_search`（选型/检索用）；
  Layer 2 不再产出数值验证结果，数值验证整体下放 Layer 3。
- **D**：新增 `submit_plan` 工具，把「交卷」从隐式（自觉输出纯文本）变成显式动作，
  框架侧捕获到 `submit_plan` 即确定性结束本轮发言。

## 设计决策

1. **终止语义收敛为一条**：`_extract_plan_text(response)` 统一判断「是否交卷」，规则：
   - 优先：`response.tool_calls` 中有 `submit_plan_tool`，取 `args["plan"]`（非空）。
   - 退化：`response.tool_calls` 为空，取 `content`（非空）——模型不调任何工具直接成稿。
2. 删除 `_looks_like_complete_plan` 与 `_extract_last_substantial_text` 两个函数及其全部调用点，
   删除 `consecutive_tool_only` 计数与「停止提醒」注入逻辑。
3. 保留硬保险：`max_iterations=15`、`invoke_fn` 降级链、`_extract_final_output` 兜底。
4. 不改 graph 拓扑、不改 AgentState、不改模型路由、不改 `create_coding_agent_tools`。

## 改动清单

### 文件 1：`mathmodelingagents/tools/__init__.py`

- 在 `create_langchain_tools()`（约 351 行起，`model_search_tool` 定义之后）新增：

```python
@tool
def submit_plan_tool(plan: str) -> str:
    """提交完整的建模方案文本。调用此工具后本轮发言立即结束。

    参数 plan 必须是按输出模板写好的完整建模方案全文。
    """
    return f"方案已提交（{len(plan)} 字符）"
```

- 把 `submit_plan_tool` 加进 425–431 行的返回列表（`model_search_tool` 之后）。
- **不得**改动 `create_coding_agent_tools()`（约 434 行起）。

### 文件 2：`mathmodelingagents/agents/__init__.py`

1. **删除** `_looks_like_complete_plan`（约 287 行，函数体完整删除）。
2. **删除** `_extract_last_substantial_text`（约 301 行，函数体完整删除）。
3. 在 `_run_modeler_turn`（约 1198 行）**之前或之后**新增 module-level helper：

```python
def _extract_plan_text(response) -> str | None:
    """统一交卷提取：submit_plan 的 plan 参数优先，其次无工具调用时的纯文本。"""
    for tc in getattr(response, "tool_calls", None) or []:
        if tc.get("name") == "submit_plan_tool":
            plan = ((tc.get("args") or {}).get("plan") or "").strip()
            if plan:
                return plan
    if not getattr(response, "tool_calls", None):
        content = (getattr(response, "content", "") or "").strip()
        if content:
            return content
    return None
```

4. **重写** `_run_modeler_turn` 循环体为单终止判断版本：

   - 移除 `consecutive_tool_only` 计数、`consecutive_tool_only >= 8` 提醒注入。
   - 移除 `_looks_like_complete_plan` 调用块。
   - 移除 `has_tools and not content` 分支的计数逻辑。
   - 新流程：

```python
for iteration in range(max_iterations):
    logger.info(
        f"[{layer_tag}] {agent_tag} iteration {iteration + 1}/{max_iterations}"
    )
    response = invoke_fn(messages)
    messages.append(response)

    plan = _extract_plan_text(response)
    if plan:
        logger.info(f"[{layer_tag}] {agent_tag} 交卷（{len(plan)} 字符）")
        return messages, plan

    # 未交卷 → 执行它调用的工具并喂回结果
    for tc in getattr(response, "tool_calls", None) or []:
        tool_name = tc.get("name", "")
        tool_args = tc.get("args", {})
        tool_id = tc.get("id", "")
        tool_fn = next((t for t in tools if t.name == tool_name), None)
        if tool_fn is not None:
            try:
                result = tool_fn.invoke(tool_args)
            except Exception as e:
                result = f"[工具执行异常] {tool_name}: {e}"
                logger.error(f"[{layer_tag}] 工具 {tool_name} 执行失败: {e}")
        else:
            result = f"[未知工具] {tool_name}"
        result_str = (
            _json.dumps(result, ensure_ascii=False)
            if isinstance(result, dict) else str(result)
        )
        messages.append(ToolMessage(content=result_str, tool_call_id=tool_id))
        logger.info(f"[{layer_tag}] {agent_tag} 工具 {tool_name}: {result_str[:120]}...")

# 兜底：耗尽迭代仍无交卷
fallback = _extract_final_output(messages)
if not fallback.strip():
    fallback = (
        f"（发言生成失败：模型连续 {max_iterations} 次迭代均未提交方案。"
        "请检查模型通道。）"
    )
return messages, fallback
```

   - 保留文件顶部的 `import json as _json`、`from langchain_core.messages import ToolMessage`（原函数内已有）。
   - 注意：函数 docstring 里「不再有『连续 N 轮无工具调用 → 强制中断』」等过时描述一并更新为 submit_plan 语义。

5. `_make_modeler_node`（约 1298 行）里 1330 行工具白名单：

```python
wanted = {"model_search_tool", "web_search_tool", "run_code_tool"}   # 旧
wanted = {"model_search_tool", "web_search_tool", "submit_plan_tool"}  # 新
```

### 文件 3：`mathmodelingagents/agents/utils/prompt_templates.py`

三个 modeler（`get_modeler_a_prompt` 212 行 / `get_modeler_b_prompt` 262 行 / `get_modeler_c_prompt` 308 行）
同步修改。三处「工具调用收敛铁律」文案保持逐字一致。

1. **「工具权限」行**（A:242 / B:288 / C:335）：

   旧：`## 工具权限: run_code (sympy/numpy/scipy/sklearn), web_search, model_search（可先检索候选模型做选型参考）`

   新：`## 工具权限: model_search（候选模型选型）, web_search（检索近年新方法）, submit_plan（提交完整方案）`

2. **「工具调用收敛铁律」段** → 改名「方案提交铁律」，文案统一为：

```
## 方案提交铁律
- 先用 model_search 检索候选模型、web_search 检索近年新方法做选型依据（各检索 1–2 次即可，勿反复检索）。
- 选型确定后，必须调用 submit_plan 提交按输出模板写好的完整方案全文——这是本轮唯一正确的结束方式。
- 本层不做数值验证：方案中的数值结论一律标注为「待 Layer 3 数值验证」，不通过 run_code 验证。
```

   （三处逐字一致；原「禁止对同一份数据反复更换统计量/波段/算法做无限细化」等针对 run_code 的条款删除。）

3. **领地删除 run_code 相关条款**：
   - A：删「✅ 用 run_code 验证核心公式/算法的数值可行性」「✅ 给出小规模验证（toy example）的实际运行结果」「✅ 所有数值结论必须来自 run_code 实际输出，不准口算」「✅ 在第2轮及以后，用代码回应对你方案的质疑」（改为「方案中的数值结论标注为待 Layer 3 验证」）。
   - B：删「✅ 运行 A 的验证代码，检查是否可复现」「✅ 如果 A 的代码有 bug 或结果不成立，指出具体问题」「✅ 用自己的验证代码证明方案的可行性」「✅ 所有数值结论必须来自 run_code 实际输出，不准口算」；「❌ 不准说"A 方案不 work"但不给证据」保留但措辞去掉「代码」字眼。
   - C：删「✅ 运行 A 和 B 的验证代码，检查可复现性」「✅ 指出 A 和 B 方案中可以简化的地方」（「验证」改「论证」仍保留）、「✅ 所有数值结论必须来自 run_code 实际输出，不准口算」。
   - 各地「系统级约束」里的「计算铁律: ...代码失败 = 方案无效...」改为「计算铁律: 不准口算 / 不准画饼 / 方案中的数值结论一律标注待 Layer 3 验证 / 参考论文必须附链接」。

4. **「输出模板」里的「代码验证」项** → 改为「可行性论证（列出待 Layer 3 数值验证的关键点）」：
   - A 模板（约 257 行）「### 4. 代码验证（代码+实际运行结果）」 → 「### 4. 可行性论证（列出待 Layer 3 数值验证的关键点）」。
   - B 模板（约 303 行）「### 3. 代码验证（代码+实际运行结果）」 → 「### 3. 可行性论证（列出待 Layer 3 数值验证的关键点）」。
   - C 模板（约 348 行）「### 2. 简化论证（对比代码+结果矩阵: A vs B vs C 简化版）」 → 「### 2. 简化论证（对比矩阵: A vs B vs C 简化版，数值结论标注待验证）」。

5. **`get_modeling_manager_prompt`**（约 354 行）同步对齐口径（manager 无工具，本就不该要求跑代码）：
   - 系统级约束「特殊权力: 可要求运行代码来验证建模师声明」→「特殊权力: 可要求建模师补充论证；裁决是 Layer 2 最终输出，直接进入 Layer 3」。
   - 领地「✅ 亲自运行关键验证代码（你不信任任何人的声明）」→「✅ 亲自核验三位建模师的方案逻辑（你不信任任何人的声明）」。
   - 「✅ 评估每个方案的：理论正确性、代码可复现性、与数据/约束的匹配度」→「✅ 评估每个方案的：理论正确性、可行性、与数据/约束的匹配度」。
   - 「✅ 裁决必须引用具体数值」保留。
   - 禁区「❌ 不准凭直觉判断——必须引用具体的代码输出作为裁决依据」→「❌ 不准凭直觉判断——必须引用建模师的方案内容作为裁决依据」。
   - 裁决标准两处「代码验证结果一致」「验证代码结果矛盾」→「方案论证一致/矛盾」。
   - 综合权重「理论正确性 40% | 代码验证 35% | 可实现性 15% | 可解释性 10%」→「理论正确性 45% | 数学可行性 30% | 可实现性 15% | 可解释性 10%」。

### 文件 4（连带）：两个验收脚本

因为删除了 `_looks_like_complete_plan` / `_extract_last_substantial_text` 与 `consecutive_tool_only` 阈值，
以下脚本必须同步更新，否则会 import 报错 / 断言失败：

- `scripts/verify_modeler_c_tool_loop.py`：
  - 删第 68–70 行 import 里对 `_looks_like_complete_plan`、`_extract_last_substantial_text` 的引用。
  - 删 3a/3b/3c（`_looks_like_complete_plan` 相关）与 4a/4b（`_extract_last_substantial_text` 相关）断言。
  - 重写 5a/5b：改为用 `submit_plan_tool` 的 tool_calls 验证 `_run_modeler_turn` 能取到 `plan` 并返回。
  - 增加对 `_extract_plan_text` 的单元断言（submie_plan 优先 / 纯文本退化 / 两者皆无返回 None）。
- `scripts/verify_modeler_convergence.py`：
  - 第 24 行 `TIELU_MARKER = "## 工具调用收敛铁律"` → `"## 方案提交铁律"`。
  - 第 91 行 `"consecutive_tool_only >= 8"` 断言 → 改为 `"submit_plan_tool"` 相关存在性断言（如 `"_extract_plan_text" in src`、`"consecutive_tool_only" not in src`）。
  - 第 94–103 行的回归子调用回归 `verify_modeler_c_tool_loop.py` 保持不变（它已同步更新）。

## 验收标准

1. `py_compile` 三个改动文件全部无错误（`agents/__init__.py`、`agents/utils/prompt_templates.py`、`tools/__init__.py`）。
2. `import mathmodelingagents.agents` 冒烟成功。
3. `scripts/verify_modeler_c_tool_loop.py` 全部 PASS。
4. `scripts/verify_modeler_convergence.py` 全部 PASS。
5. `git diff --stat` 仅涉及以上 4 个文件（2 源码 + "prompt_templates" 实为同一文件的 prompts + 2 脚本），
   不得触及其他文件。
6. grep 确认源码中已不存在 `_looks_like_complete_plan`、`_extract_last_substantial_text`、`consecutive_tool_only`、
   `run_code_tool`（在 modeler 白名单/tools binding 上下文）保留的 `run_code` 仅属于 Layer 3 `create_coding_agent_tools`。

## Do NOT

- 不修改 `mathmodelingagents/graph/modeling_graph.py`（graph 拓扑）。
- 不修改 `AgentState` 定义（`agents/utils/agent_states.py`）。
- 不修改 `mathmodelingagents/llm_clients/__init__.py` 的降级链。
- 不修改 `default_config.py` 的模型路由 / max_tokens / timeout / temperature。
- 不修改 `create_coding_agent_tools()`（Layer 3 用，仍需 run_code 等全套工具）。
- 不新增/修改 `pyproject.toml`、`.env`、任何依赖。
- 不改 `main.py`、CLI 参数。
- 不写 `uv.lock` 外的新文件（除本 brief 之外）。