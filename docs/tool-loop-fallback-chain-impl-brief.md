# Implementation Brief: tool-calling 循环接入 provider/model 降级链

## Background

Layer2/3/4 的 tool-calling 节点（modeler_a/b/c、SolverAgent、VizAgent、PaperAgent）共用 `_run_tool_loop`
骨架。该骨架的 LLM 调用目前是**单一 provider + 单一模型 + 内联重试**（`agents/__init__.py:364-384`），
没有降级链。实测（2026-09-05）确认：`volcengine`（火山 Coding Plan）+ `deepseek-v4-pro` + 长中文
数学上下文下，模型推理爆量导致 invoke 持续超时（543s `OpenAITimeoutError`）或返回 0 字符。旧的非工具
路径 `invoke_with_fallback`（`llm_clients/__init__.py:297`）有 4 步 provider/model 降级兜底，
但工具路径没有——一次空响应/超时就直接掀翻整个节点。

本 brief 把"降级链"能力引入 tool-calling 路径：主 provider/模型失败后，依次降级到
fallback provider / flash 模型，覆盖全部 4 个 tool-calling 节点（一次修复，全层受益）。

## Design

### 核心思路：`invoke_fn` 回调注入，降级逻辑内聚在 llm_clients

`_run_tool_loop` 不直接依赖 config，而是新增一个可选回调 `invoke_fn(messages) -> response`，
由**调用点**注入"带降级的工具调用"。降级链的实现放在 `llm_clients`（与 `invoke_with_fallback`
同层），复用同一个 steps 构造逻辑。

关键区别 vs `invoke_with_fallback`：
- `invoke_with_fallback` 返回 `response.content` 字符串（无工具场景）
- 新函数返回**完整 response 对象**（含 `tool_calls`），因为工具循环需要结构化 tool_calls

### 降级成功不修改 response 内容

`invoke_with_fallback` 降级时给 content 加 `[降级 prov/model]` 前缀；但工具场景 response 是
AIMessage 对象，给 content 加前缀会污染 tool_calls 语义。因此**降级成功只写日志，不修改返回对象**：
`logger.info(f"[{layer}] {agent_name} 降级 {prov}/{model} 成功")`。

---

## Change List

### 1. `mathmodelingagents/llm_clients/__init__.py`

#### 1a. 提取私有 helper `_build_fallback_steps`

在 `invoke_with_fallback`（第 297 行）之前新增纯函数，把 4 步降级链构造抽出来：

```python
def _build_fallback_steps(config: dict, layer: str, role: str) -> list[tuple[str, str, str | None]]:
    """构造 4 步降级链，供 invoke_with_fallback / invoke_with_tools_with_fallback 共用。"""
    provider = config.get("llm_provider", "opencode")
    fallback_provider = config.get("fallback_provider", "deepseek")
    primary_model = get_layer_model(config, layer, role)
    flash_model = config.get("quick_think_llm", "deepseek-v4-flash")
    fallback_base_url = config.get("fallback_base_url")
    return [
        (provider, primary_model, None),
        (fallback_provider, primary_model, fallback_base_url),
        (provider, flash_model, None),
        (fallback_provider, flash_model, fallback_base_url),
    ]
```

然后把 `invoke_with_fallback` 里第 344-349 行的 steps 构造替换为 `steps = _build_fallback_steps(config, layer, role)`。
`provider`/`fallback_provider`/`primary_model`/`flash_model`/`fallback_base_url` 这些局部变量若不再被
其它地方用到可删除，但**必须保持函数其余行为与日志文案完全不变**（`invoke_with_fallback` 已有测试依赖）。

> 注意：`_build_fallback_steps` 依赖 `get_layer_model`，它在同文件第 217 行定义，晚于 helper 定义点
> （若 helper 放在 `invoke_with_fallback` 之前）。Python 模块级函数在定义时是延迟绑定（调用时才解析），
> 只要 helper 在**被调用时** `get_layer_model` 已定义即可。为稳妥，建议把 helper 放在 `get_layer_model`
> 定义（217 行）之后、`invoke_with_fallback`（297 行）之前。

#### 1b. 新增私有 `_invoke_tools_with_retry`

仿照 `_invoke_with_retry`（第 78-122 行），但返回完整 response 对象、软失败检测针对 tool_calls：

```python
def _invoke_tools_with_retry(
    llm_with_tools,
    messages: list,
    agent_name: str,
    layer: str,
) -> Any:
    """带重试的工具调用 invoke，返回 AIMessage（含 tool_calls）。

    软失败判定：无 tool_calls 且 content.strip() < _MIN_CONTENT_CHARS（10）→ 视为
    EmptyLLMResponseError，走指数退避重试（2s→4s→8s）。与 _run_tool_loop 现有软失败
    检测对齐（agents/__init__.py:369-373），语义一致。
    """
    last_error = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = llm_with_tools.invoke(messages)
            content = getattr(response, "content", "") or ""
            if not getattr(response, "tool_calls", None) and len(content.strip()) < _MIN_CONTENT_CHARS:
                raise EmptyLLMResponseError(len(content))
            if attempt > 1:
                logger.info(f"[{layer}] {agent_name} 第 {attempt} 次尝试成功（工具调用）")
            return response
        except Exception as e:
            last_error = e
            if isinstance(e, EmptyLLMResponseError):
                if attempt < _MAX_RETRIES:
                    delay = _BACKOFF_BASE ** attempt
                    logger.warning(
                        f"[{layer}] {agent_name} 空响应，第 {attempt}/{_MAX_RETRIES} 次重试，"
                        f"{delay:.0f}s 后重试: {e}"
                    )
                    _time.sleep(delay)
                else:
                    break
            elif attempt < _MAX_RETRIES and is_retryable_error(e):
                delay = _BACKOFF_BASE ** attempt
                logger.warning(
                    f"[{layer}] {agent_name} 工具调用第 {attempt}/{_MAX_RETRIES} 次失败（可重试），"
                    f"{delay:.0f}s 后重试: {e}"
                )
                _time.sleep(delay)
            else:
                break
    raise last_error  # type: ignore[misc]
```

#### 1c. 新增公开 `invoke_with_tools_with_fallback`

```python
def invoke_with_tools_with_fallback(
    config: dict,
    layer: str,
    role: str,
    tools: list,
    messages: list,
    agent_name: str,
    *,
    max_tokens: int | None = None,
) -> Any:
    """带多级 provider/model 降级的 tool-calling invoke。

    降级链与 invoke_with_fallback 完全一致（主 provider+角色模型 → fallback provider
    +同模型 → 主 provider+flash → fallback provider+flash），每步内 3 次指数退避重试。

    Returns:
        第一个成功的 AIMessage（含 tool_calls）。降级成功只写日志、不修改 response。
    Raises:
        RuntimeError: 4 步全部失败。
    """
    timeout = config.get("layer_timeouts", {}).get(layer, DEFAULT_TIMEOUT)
    if max_tokens is None:
        max_tokens = resolve_max_tokens(config, role)
    temperature = config.get("temperature_overrides", {}).get(
        role, config.get("default_temperature", 0.0)
    )
    steps = _build_fallback_steps(config, layer, role)

    last_error = None
    for step_num, (prov, model, base_url) in enumerate(steps, 1):
        try:
            llm = create_llm_client(
                provider=prov, model=model, base_url=base_url,
                temperature=temperature, max_tokens=max_tokens,
                request_timeout=timeout,
            )
            llm_with_tools = llm.bind_tools(tools)
            response = _invoke_tools_with_retry(llm_with_tools, messages, agent_name, layer)
            if step_num > 1:
                logger.info(f"[{layer}] {agent_name} 降级 {prov}/{model} 成功")
            else:
                logger.info(f"[{layer}] {agent_name} step1 ({prov}/{model}) 成功")
            return response
        except Exception as e:
            last_error = e
            print(f"  [{layer}] {agent_name} ⚠️ {prov}/{model} unavailable → trying next fallback", flush=True)
            logger.warning(f"[{layer}] {agent_name} step{step_num} ({prov}/{model}) 不可用: {e}")

    raise RuntimeError(f"[{layer}] {agent_name} 降级链全部失败: {last_error}")
```

#### 1d. 导出更新

在 `llm_clients/__init__.py` 末尾如有 `__all__` 列表则补 `invoke_with_tools_with_fallback`。
若用星号导出（`from ... import *`），`agents/__init__.py:16` 已显式列名导入，需在该 import 行补入
`invoke_with_tools_with_fallback`（见 Change 2）。

### 2. `mathmodelingagents/agents/__init__.py`

#### 2a. 更新 import（第 16 行）

```python
from mathmodelingagents.llm_clients import (
    invoke_with_fallback, resolve_max_tokens, is_retryable_error,
    create_layer_llm, EmptyLLMResponseError, invoke_with_tools_with_fallback,
)
```

#### 2b. `_run_tool_loop` 签名与 invoke 段改造（第 322-384 行）

签名新增一个可选参数 `invoke_fn`（放在 `on_selfcheck` 之后），并把 `llm`/`llm_with_tools` 改为可选
（`llm: Any = None, llm_with_tools: Any = None`），因为 `invoke_fn` 路径下不再需要 `llm_with_tools`，
`llm` 仅在 Solver 的 `on_summary_after_exhaust` 仍被用到：

```python
def _run_tool_loop(
    *,
    llm: Any = None,
    llm_with_tools: Any = None,
    tools: list,
    layer_tag: str,
    agent_tag: str,
    max_iterations: int,
    initial_messages: list,
    max_retries: int = 3,
    consecutive_no_tool_limit: int = 3,
    sanitize: Callable[[list], list] | None = None,
    on_summary_after_exhaust: Callable[[Any, list], str | None] | None = None,
    on_selfcheck: Callable[[str, list], str] | None = None,
    invoke_fn: Callable[[list], Any] | None = None,
) -> tuple[list, str]:
```

invoke 段（原 364-384 行）改为：**若 `invoke_fn` 非 None，直接 `response = invoke_fn(messages)`
（其内部已含重试+降级）；否则走原内联重试逻辑（保持不变）**：

```python
        # ── Invoke LLM：invoke_fn 存在时走降级链回调；否则内联重试 ──
        response = None
        if invoke_fn is not None:
            response = invoke_fn(messages)
        else:
            for attempt in range(1, max_retries + 1):
                try:
                    response = llm_with_tools.invoke(messages)
                    _content = getattr(response, "content", "") or ""
                    if not getattr(response, "tool_calls", None) and len(_content.strip()) < 10:
                        raise EmptyLLMResponseError(len(_content))
                    break
                except Exception as e:
                    if attempt < max_retries and is_retryable_error(e):
                        delay = 2 ** attempt
                        logger.warning(
                            f"[{layer_tag}] {agent_tag} LLM 调用重试 {attempt}/{max_retries}, "
                            f"{delay}s: {e}"
                        )
                        _time.sleep(delay)
                    else:
                        raise
```

> **日志文案逐字保留**：`LLM 调用重试 {attempt}/{max_retries}, {delay}s: {e}` 这条非降级路径的
> 日志文案一个字都不能改（有测试/运维依赖）。降级路径的日志是 `invoke_with_tools_with_fallback`
> 里新增的，不冲突。

其余段（软失败检测注释、tool 派发、consecutive_no_tool、SELF_CHECK、兜底回调）**均不修改**。

#### 2c. 四个调用点改为注入 `invoke_fn`

**modeler 节点（约 1103-1118 行）**：删掉 `llm = create_layer_llm(...)` 与 `llm.bind_tools(...)`
（modeler 无 summary 兜底），改为：

```python
        wanted = {"model_search_tool", "web_search_tool", "run_code_tool"}
        tools = [t for t in create_langchain_tools() if t.name in wanted]

        def _invoke_fn(msgs):
            return invoke_with_tools_with_fallback(
                config, "modeling", "agent", tools, msgs, agent_name,
            )

        try:
            messages, result = _run_tool_loop(
                llm=None,
                llm_with_tools=None,
                tools=tools,
                layer_tag="Layer2",
                agent_tag=agent_name,
                max_iterations=10,
                initial_messages=messages,
                invoke_fn=_invoke_fn,
            )
        except Exception as e:
            logger.error(f"[Layer2] {agent_name} tool loop 失败: {e}")
            result = f"LLM 调用失败（全部降级耗尽）: {e}"
```

**solver 节点（约 1217-1233 行）**：保留 `llm = create_layer_llm(config, "implementation", "coder")`
（`_solver_summary` 兜底仍需裸 llm），删掉 `llm.bind_tools`，注入：

```python
        tools = create_coding_agent_tools(output_dir)
        llm = create_layer_llm(config, "implementation", "coder")  # 仅 summary 兜底用

        def _invoke_fn(msgs):
            return invoke_with_tools_with_fallback(
                config, "implementation", "coder", tools, msgs, "SolverAgent",
            )

        messages, final_output = _run_tool_loop(
            llm=llm,
            llm_with_tools=None,
            tools=tools,
            layer_tag="Layer3",
            agent_tag="SolverAgent",
            max_iterations=30,
            initial_messages=messages,
            sanitize=_sanitize_tool_pairing,
            on_summary_after_exhaust=_solver_summary,
            invoke_fn=_invoke_fn,
        )
```

**viz 节点（约 1297-1312 行）**：同 modeler，无 summary，删 `create_layer_llm`/`bind_tools`：

```python
        tools = create_coding_agent_tools(output_dir)

        def _invoke_fn(msgs):
            return invoke_with_tools_with_fallback(
                config, "implementation", "coder", tools, msgs, "VizAgent",
            )

        messages, final_output = _run_tool_loop(
            llm=None,
            llm_with_tools=None,
            tools=tools,
            layer_tag="Layer3",
            agent_tag="VizAgent",
            max_iterations=10,
            initial_messages=messages,
            sanitize=_sanitize_tool_pairing,
            invoke_fn=_invoke_fn,
        )
```

**paper 节点（约 1492-1513 行）**：`on_selfcheck` 用的是 `_paper_read_disk(output_dir, ...)`，不依赖
裸 llm，故 llm 可传 None；删 `create_layer_llm`/`bind_tools`：

```python
        tools = create_paper_agent_tools(output_dir)

        def _invoke_fn(msgs):
            return invoke_with_tools_with_fallback(
                config, "paper", "writer", tools, msgs, "PaperAgent",
            )

        def selfcheck(final_output: str, messages: list) -> str:
            return _paper_read_disk(output_dir, final_output, messages)

        messages, final_output = _run_tool_loop(
            llm=None,
            llm_with_tools=None,
            tools=tools,
            layer_tag="Layer4",
            agent_tag="PaperAgent",
            max_iterations=30,
            initial_messages=messages,
            sanitize=_sanitize_tool_pairing,
            on_selfcheck=selfcheck,
            invoke_fn=_invoke_fn,
        )
```

> 各节点的 `role` 参数必须与原来 `create_layer_llm(config, layer, role)` 的 role 完全一致
> （modeling/agent、implementation/coder×2、paper/writer），否则模型路由会变。

### 3. 测试

#### 3a. `tests/test_tool_loop.py` 新增（invoke_fn 注入路径）

复用现有 `FakeToolLLM` / `scripted_responses` / `AIMessage` 写法，新增 2 个用例：

- `test_invoke_fn_is_used_when_provided`：传一个 fake `invoke_fn`（按 `scripted_responses` 返回
  AIMessage：2 次空 tool_calls + 1 次 SELF_CHECK_PASSED），断言 `_run_tool_loop` 调用的是 `invoke_fn`
  而非 `llm_with_tools`（可断言 fake 的 `invoke_calls` 为空、且 `invoke_fn` 被调 3 次），最终输出含
  `SELF_CHECK_PASSED`。
- `test_invoke_fn_exhausted_propagates`：`invoke_fn` 直接 `raise RuntimeError("降级链全部失败...")`，
  断言 `_run_tool_loop` 向上抛该异常（而非静默返回空）。

#### 3b. 新增 `tests/test_tool_fallback.py`（llm_clients 降级链）

用 `monkeypatch` 替换 `mathmodelingagents.llm_clients.create_llm_client` 返回 fake 客户端，验证
`invoke_with_tools_with_fallback` 的降级行为（**不发起真实网络**）：

- `test_fallback_succeeds_on_step2`：fake `create_llm_client` 在第 1 步 raise
  `EmptyLLMResponseError(0)`、第 2 步返回一个 `AIMessage(tool_calls=[...])`，断言最终返回该 response、
  且 `create_llm_client` 被调 2 次、日志含"降级"。
- `test_fallback_all_steps_fail_raises_runtime_error`：4 步全 raise，断言抛 `RuntimeError` 且
  被调 4 次。

fake 客户端需实现 `bind_tools(tools) -> self` 与 `invoke(messages) -> AIMessage`（或 raise）。

---

## Acceptance Criteria

（所有命令在项目根、使用项目解释器、先清 PYTHONPATH）

```bash
export PYTHONPATH=
.venv/Scripts/python.exe -m pytest tests/test_tool_loop.py tests/test_tool_fallback.py -v   # 全绿，含新增用例
.venv/Scripts/python.exe -m pytest tests/ -q                                                    # 全量不回归
.venv/Scripts/python.exe -c "import mathmodelingagents.llm_clients, mathmodelingagents.agents" # import smoke
.venv/Scripts/python.exe -m py_compile mathmodelingagents/llm_clients/__init__.py mathmodelingagents/agents/__init__.py
```

- `invoke_with_fallback`（无工具路径）行为不变：既有它的测试（若有）仍绿。
- `git diff --stat` 只应改动 4 个文件：`llm_clients/__init__.py`、`agents/__init__.py`、
  `tests/test_tool_loop.py`、`tests/test_tool_fallback.py`（新增）。

---

## Explicitly Do NOT Do

- ❌ **不要**给 `_run_tool_loop` 直接加 config/layer/role 参数（降级链通过 `invoke_fn` 注入，保持骨架不依赖 config）。
- ❌ **不要**修改 `invoke_with_fallback` 的日志文案、返回格式（`[降级 ...]` 前缀）或重试退避参数。
- ❌ **不要**修改 `_run_tool_loop` 内**非降级路径**的日志文案（尤其 `LLM 调用重试 ...` 那条逐字保留）。
- ❌ **不要**修改 `_invoke_with_retry`（无工具路径现有函数）的语义。
- ❌ **不要**动 `_solver_summary` / `_paper_read_disk` 的降级行为（summary 兜底仍用裸 `llm`，本次不接入降级）。
- ❌ **不要**实现"跨 iteration 记住降级 provider"的优化（每次 iteration 重新从 step1 开始降级即可；该优化留作独立 ticket）。
- ❌ **不要**改变任何节点的 `layer`/`role` 路由映射；不要新增第三方法依赖（不碰 pyproject.toml / lock / 依赖）。
- ❌ **不要**顺手改其它未列出的代码（graph 拓扑、AgentState、prompt、config 默认值等一概不动）。

---

## Future consideration（本次不做，仅记录）

- 跨 iteration 缓存降级结果（一旦某 provider 持续超时，后续 iteration 直接从上次成功的 step 起），
  可把"每次 iteration 都重新等 543s 超时"的浪费降下来。
- `_solver_summary` 裸 llm 兜底同样可接入 `invoke_with_fallback`（无工具，直接复用现有函数）。