"""LLM Client Factory — 支持 OpenCode Go、DeepSeek 和火山方舟两种订阅套餐。

使用 langchain-openai 的 ChatOpenAI，避免直接 import openai。

支持的 provider：
  - "opencode"           OpenCode Go 网关
  - "deepseek"            DeepSeek 官方 API
  - "volcengine"          火山方舟 **Coding Plan**（`api/coding/v3`，`VOLCENGINE_API_KEY`）
  - "volcengine-plan"     火山方舟 **Agent Plan**（`api/plan/v3`，`VOLCENGINE_PLAN_API_KEY`）

Timeout 策略（从 config.layer_timeouts 读取，默认 90s）：
  - 默认 90s。流式调用（streaming=True）下 read timeout 语义变为「两个流块之间的
    最大间隔」：正常推理 token 持续回流不会超时，90s 无任何字节才抛
    OpenAITimeoutError 走重试/降级链；同一通道连续超时 5 次即熔断跳下一步。
"""

import logging
import os
import time as _time
from typing import Any

logger = logging.getLogger(__name__)

# 默认超时（秒）— 当 config 中未指定时使用
DEFAULT_TIMEOUT = 90

# ═══════════════════════════════════════════════
# LLM 调用重试（从 agents/__init__.py 迁移）
# ═══════════════════════════════════════════════

_RETRYABLE_CODES = {429, 500, 502, 503, 504}
_RETRYABLE_SUBSTRINGS = (
    "upstream request failed",
    "inference is temporarily unavailable",
    "rate limit",
    "connection",
    "timeout",
    "timed out",
    "failover_exhausted",
    "temporarily unavailable",
    "server error",
)
_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0  # 2s → 4s → 8s
_MIN_CONTENT_CHARS = 10  # 低于此长度的输出视为模型故障


class EmptyLLMResponseError(ValueError):
    """LLM 返回空/极短内容（HTTP 200 但正文无有效输出），视为可重试的瞬态模型故障。"""

    def __init__(self, char_count: int):
        self.char_count = char_count
        super().__init__(f"模型返回空/极短内容 ({char_count} 字符)，视为模型故障")


def is_retryable_error(error: Exception) -> bool:
    """判断异常是否可重试（瞬态故障）。"""
    from openai import APITimeoutError, APIConnectionError
    if isinstance(error, (APITimeoutError, APIConnectionError)):
        return True
    if isinstance(error, EmptyLLMResponseError):
        return True
    msg = str(error).lower()
    for code in _RETRYABLE_CODES:
        code_str = str(code)
        if f"error code: {code_str}" in msg:
            return True
        if f"http error {code_str}" in msg:
            return True
        if f"status code {code_str}" in msg:
            return True
        if f" {code_str} " in msg or msg.startswith(f"{code_str} "):
            return True
    for ss in _RETRYABLE_SUBSTRINGS:
        if ss in msg:
            return True
    return False


# 「输入超长」确定性错误的特征子串（不可重试、换 provider 无效——是输入太大，不是通道故障）
_INPUT_TOO_LONG_SUBSTRINGS = (
    "input length", "maximum length", "maximum context",
    "context length", "invalidparameter", "too many tokens",
    "context window", "exceeds the maximum",
)


def is_input_too_long_error(error: Exception) -> bool:
    """判断是否为「输入超长」确定性错误（不可重试、换 provider 无效）。"""
    msg = str(error).lower()
    return any(ss in msg for ss in _INPUT_TOO_LONG_SUBSTRINGS)


def _invoke_with_retry(
    llm,
    messages: list,
    agent_name: str,
    layer: str,
    *,
    step_label: str = "",
    consecutive_timeout_limit: int = 5,
) -> str:
    """带重试的 LLM 调用，指数退避 2s → 4s → 8s。

    同一步（同一 provider+model）内连续 APITimeoutError 达到 consecutive_timeout_limit
    次即熔断（提前 break，不再烧满剩余重试），由降级循环跳至下一步；换一步重新计数。
    """
    from openai import APITimeoutError

    start_time = _time.perf_counter()
    step_prefix = f"{step_label} " if step_label else ""
    last_error = None
    timeout_streak = 0
    for attempt in range(1, max(_MAX_RETRIES, consecutive_timeout_limit) + 1):
        try:
            response = llm.invoke(messages)
            result = response.content
            if not result or len(result.strip()) < _MIN_CONTENT_CHARS:
                raise EmptyLLMResponseError(len(result))
            if attempt > 1:
                print(f"  [{layer}] {agent_name} ✅ retry succeeded on attempt {attempt}", flush=True)
                logger.info(f"[{layer}] {agent_name} 第 {attempt} 次尝试成功")
            return result
        except Exception as e:
            elapsed = _time.perf_counter() - start_time
            last_error = e
            if isinstance(e, EmptyLLMResponseError):
                timeout_streak = 0
                if attempt < _MAX_RETRIES:
                    delay = _BACKOFF_BASE ** attempt
                    print(f"  [{layer}] {agent_name} ⚠️ 空响应 ({e.char_count} 字符) retry {attempt}/{_MAX_RETRIES} ({delay:.0f}s backoff)", flush=True)
                    logger.warning(f"[{layer}] {agent_name} 空响应，第 {attempt}/{_MAX_RETRIES} 次重试，{delay:.0f}s 后重试: {e}")
                    _time.sleep(delay)
                else:
                    break
            elif is_retryable_error(e):
                if isinstance(e, APITimeoutError):
                    timeout_streak += 1
                else:
                    timeout_streak = 0
                if timeout_streak >= consecutive_timeout_limit:
                    print(f"  [{layer}] {agent_name} 🔥 {step_prefix}连续超时 ×{timeout_streak}，熔断该通道，跳至下一步", flush=True)
                    logger.warning(f"[{layer}] {agent_name} {step_label} 连续超时 ×{timeout_streak}，熔断")
                    break
                # 非 timeout 错误（或曾被非 timeout 打断的 streak）仍受 _MAX_RETRIES 上限约束；
                # 只有「从第 1 次起连续超时」的纯超时场景才可重试至 consecutive_timeout_limit 次。
                if attempt >= _MAX_RETRIES and timeout_streak < attempt:
                    break
                delay = _BACKOFF_BASE ** attempt
                max_attempts = max(_MAX_RETRIES, consecutive_timeout_limit)
                print(f"  [{layer}] {agent_name} 🔄 {step_prefix}{elapsed:.0f}s 后失败 → retry {attempt}/{max_attempts} ({delay:.0f}s backoff)", flush=True)
                logger.warning(
                    f"[{layer}] {agent_name} 第 {attempt}/{max_attempts} 次失败（可重试），"
                    f"{delay:.0f}s 后重试: {e}"
                )
                _time.sleep(delay)
            else:
                break
    raise last_error  # type: ignore[misc]


def resolve_max_tokens(config: dict, role: str, agent_name: str = "") -> int:
    """多级 max_tokens 解析：agent_name > role > default_max_tokens > 1024。"""
    overrides = config.get("max_tokens_overrides", {})
    if agent_name and agent_name in overrides:
        return overrides[agent_name]
    if role in overrides:
        return overrides[role]
    return config.get("default_max_tokens", 1024)


def create_llm_client(
    provider: str,
    model: str,
    base_url: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    request_timeout: int = DEFAULT_TIMEOUT,
) -> Any:
    """创建 LLM 客户端。

    Args:
        provider: "opencode" / "deepseek"
                  "volcengine"（火山方舟 Coding Plan）/"volcengine-plan"（火山方舟 Agent Plan）
        model: 模型名称
        base_url: 自定义 API 地址
        temperature: 温度参数
        max_tokens: 最大输出 token
        request_timeout: HTTP 请求超时（秒），从 config.layer_timeouts 读取。
            流式调用（streaming=True）下，此为「两个流块之间的最大间隔」而非整次响应上限。

    Returns:
        langchain_openai.ChatOpenAI 实例（streaming=True，分片自动聚合，对上层透明）
    """
    from langchain_openai import ChatOpenAI

    if provider == "opencode":
        api_key = os.getenv("OPENCODE_GO_API_KEY", "")
        if not api_key:
            raise ValueError(
                "OPENCODE_GO_API_KEY 环境变量未设置，请在项目 .env 文件中配置"
            )
        url = base_url or "https://opencode.ai/zen/go/v1"
    elif provider == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY 环境变量未设置")
        url = base_url or "https://api.deepseek.com/v1"
    elif provider == "volcengine":
        # 火山方舟 Coding Plan（普通方舟 Key）
        api_key = os.getenv("VOLCENGINE_API_KEY", "")
        if not api_key:
            raise ValueError("VOLCENGINE_API_KEY 环境变量未设置")
        url = base_url or "https://ark.cn-beijing.volces.com/api/coding/v3"
    elif provider == "volcengine-plan":
        # 火山方舟 Agent Plan（订阅后从「开通管理」换取专属 Key）
        api_key = os.getenv("VOLCENGINE_PLAN_API_KEY") or os.getenv("VOLCENGINE_API_KEY", "")
        if not api_key:
            raise ValueError("VOLCENGINE_PLAN_API_KEY / VOLCENGINE_API_KEY 环境变量未设置")
        url = base_url or "https://ark.cn-beijing.volces.com/api/plan/v3"
    else:
        raise ValueError(f"不支持的 LLM provider: {provider}")

    # volcengine-plan（Agent Plan）端点上 max_completion_tokens 是推理+正文总预算，
    # 推理模型会吃满预算导致正文返空（langchain 强制的参数名语义）。
    # 显式指定 reasoning_effort 压制推理暴走（已实测验证）。
    # 注意：langchain-openai 1.4.x 将 reasoning_effort 作为显式参数，
    # 传入 model_kwargs 会被提升为显式字段并触发 UserWarning，故直接传显式参数。
    reasoning_effort = "medium" if provider == "volcengine-plan" else None

    logger.info(
        f"创建 LLM: provider={provider}, model={model}, "
        f"temp={temperature}, max_tokens={max_tokens}, timeout={request_timeout}s"
    )

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=url,
        temperature=temperature,
        max_tokens=max_tokens,
        request_timeout=request_timeout,
        max_retries=2,
        streaming=True,
        reasoning_effort=reasoning_effort,
    )


def _apply_model_alias(config: dict, provider: str, model: str) -> str:
    """应用 provider 级模型别名映射（如 volcengine 下 qwen3.7-max → deepseek-v4-pro）。"""
    aliases = config.get("provider_model_aliases", {})
    provider_aliases = aliases.get(provider, {})
    return provider_aliases.get(model, model)


def get_layer_model(
    config: dict,
    layer: str,
    role: str,
) -> str:
    """根据 layer 和 role 获取对应的模型名称。

    Args:
        config: 全局配置
        layer: 层名 (problem/modeling/implementation/paper/sensitivity)
        role: 角色 (agent/manager/algorithm/coder/visualizer)

    Returns:
        模型名称字符串
    """
    provider = config["llm_provider"]

    if provider == "deepseek":
        if role == "manager":
            model = config["deep_think_llm"]
        else:
            model = config["quick_think_llm"]
    else:
        # OpenCode / volcengine / volcengine-plan 模式：查 layer_model_overrides
        overrides = config.get("layer_model_overrides", {})
        # 1) provider 级 layer 覆盖优先（合并：全局为底，provider 级覆盖同名角色）
        pv_overrides = config.get("provider_layer_model_overrides", {}).get(provider, {})
        layer_config = {**overrides.get(layer, {}), **pv_overrides.get(layer, {})}

        if role in layer_config:
            model = layer_config[role]
        elif "agent" in layer_config:
            model = layer_config["agent"]
        else:
            model = config["quick_think_llm"]

    return _apply_model_alias(config, provider, model)


def create_layer_llm(
    config: dict,
    layer: str,
    role: str,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> Any:
    """为特定层的特定角色创建 LLM 客户端。

    自动从 config.layer_timeouts 读取该层的超时设置。
    max_tokens 优先级：显式传入 > config.max_tokens_overrides[role] > config.default_max_tokens > 1024。
    """
    model = get_layer_model(config, layer, role)
    timeout = config.get("layer_timeouts", {}).get(layer, DEFAULT_TIMEOUT)

    # 解析 max_tokens
    if max_tokens is None:
        overrides = config.get("max_tokens_overrides", {})
        if role in overrides:
            max_tokens = overrides[role]
        else:
            max_tokens = config.get("default_max_tokens", 1024)

    # 解析 temperature
    if temperature is None:
        temp_overrides = config.get("temperature_overrides", {})
        if role in temp_overrides:
            temperature = temp_overrides[role]
        else:
            temperature = config.get("default_temperature", 0.0)

    return create_llm_client(
        provider=config["llm_provider"],
        model=model,
        base_url=config.get("backend_url"),
        temperature=temperature,
        max_tokens=max_tokens,
        request_timeout=timeout,
    )


def _build_fallback_steps(config: dict, layer: str, role: str) -> list[tuple[str, str, str | None]]:
    """构造 4 步降级链，供 invoke_with_fallback / invoke_with_tools_with_fallback 共用。"""
    provider = config.get("llm_provider", "opencode")
    fallback_provider = config.get("fallback_provider", "deepseek")
    primary_model = get_layer_model(config, layer, role)
    flash_model = config.get("quick_think_llm", "deepseek-v4-flash")
    fallback_base_url = config.get("fallback_base_url")
    steps = [
        (provider, primary_model, None),
        (fallback_provider, primary_model, fallback_base_url),
        (provider, flash_model, None),
        (fallback_provider, flash_model, fallback_base_url),
    ]
    # 按 (provider, model) 去重（保留首次出现）：primary==flash 时 4 步缩为 2 步，避免白跑两遍
    deduped: list[tuple[str, str, str | None]] = []
    seen: set[tuple[str, str]] = set()
    for step in steps:
        key = (step[0], step[1])
        if key not in seen:
            seen.add(key)
            deduped.append(step)
    return deduped


def invoke_with_fallback(
    config: dict,
    layer: str,
    role: str,
    messages: list,
    agent_name: str,
    *,
    max_tokens: int | None = None,
) -> str:
    """统一 LLM 调用链，带多级 provider/模型降级。

    降级链:
      1) 主 provider + 角色模型          (opencode + deepseek-v4-pro)
      2) fallback provider + 同模型名    (deepseek官方 + deepseek-v4-pro)
      3) 主 provider + flash 模型        (opencode + deepseek-v4-flash)
      4) fallback provider + flash 模型  (deepseek官方 + deepseek-v4-flash)

    每步内部有 3 次指数退避重试（503/超时等瞬态故障）。

    Args:
        config: 全局配置字典
        layer: 层名 (problem/modeling/implementation/paper/sensitivity)
        role: 角色名 (agent/manager/algorithm/coder/visualizer/architect/writer)
        messages: 已构建的 SystemMessage + HumanMessage 列表
        agent_name: Agent 名称（用于日志）
        max_tokens: 覆盖 max_tokens（默认从 config 解析）

    Returns:
        响应文本。若使用了降级路径，开头附加 [降级 provider/model] 标记。

    Raises:
        RuntimeError: 4 步全部失败。
    """
    timeout = config.get("layer_timeouts", {}).get(layer, DEFAULT_TIMEOUT)

    if max_tokens is None:
        max_tokens = resolve_max_tokens(config, role)

    temp_overrides = config.get("temperature_overrides", {})
    temperature = temp_overrides.get(role, config.get("default_temperature", 0.0))

    steps = _build_fallback_steps(config, layer, role)

    last_error = None
    for step_num, (prov, model, base_url) in enumerate(steps, 1):
        step_label = f"{prov}/{model} (step {step_num}/{len(steps)})"
        try:
            print(f"  [{layer}] {agent_name} ⏳ calling {prov}/{model} (step {step_num}/{len(steps)}, timeout={timeout}s)...", flush=True)
            llm = create_llm_client(
                provider=prov, model=model, base_url=base_url,
                temperature=temperature, max_tokens=max_tokens,
                request_timeout=timeout,
            )
            start_time = _time.perf_counter()
            result = _invoke_with_retry(llm, messages, agent_name, layer, step_label=step_label)
            elapsed = _time.perf_counter() - start_time
            print(f"  [{layer}] {agent_name} ✅ {elapsed:.1f}s, {len(result)} chars", flush=True)
            if step_num > 1:
                result = f"[降级 {prov}/{model}]\n\n{result}"
            logger.info(f"[{layer}] {agent_name} step{step_num} ({prov}/{model}) 成功")
            return result
        except Exception as e:
            last_error = e
            # 输入超长 = 确定性错误，fail-fast，绝不再烧剩余降级调用
            if is_input_too_long_error(e):
                raise RuntimeError(f"[{layer}] {agent_name} 输入超长（超过模型上下文上限），请剥离附件数据/精简输入后重试: {e}") from e
            print(f"  [{layer}] {agent_name} ⚠️ {prov}/{model} unavailable → trying next fallback", flush=True)
            logger.warning(f"[{layer}] {agent_name} step{step_num} ({prov}/{model}) 不可用: {e}")

    raise RuntimeError(f"[{layer}] {agent_name} 降级链全部失败: {last_error}")


def _invoke_tools_with_retry(
    llm_with_tools,
    messages: list,
    agent_name: str,
    layer: str,
    *,
    step_label: str = "",
    consecutive_timeout_limit: int = 5,
) -> Any:
    """带重试的工具调用 invoke，返回 AIMessage（含 tool_calls）。

    软失败判定：无 tool_calls 且 content.strip() < _MIN_CONTENT_CHARS（10）→ 视为
    EmptyLLMResponseError，走指数退避重试（2s→4s→8s）。与 _run_tool_loop 现有软失败
    检测对齐（agents/__init__.py），语义一致。

    同一步（同一 provider+model）内连续 APITimeoutError 达到 consecutive_timeout_limit
    次即熔断跳至下一步；换一步重新计数。
    """
    from openai import APITimeoutError

    start_time = _time.perf_counter()
    step_prefix = f"{step_label} " if step_label else ""
    last_error = None
    timeout_streak = 0
    for attempt in range(1, max(_MAX_RETRIES, consecutive_timeout_limit) + 1):
        try:
            response = llm_with_tools.invoke(messages)
            content = getattr(response, "content", "") or ""
            if not getattr(response, "tool_calls", None) and len(content.strip()) < _MIN_CONTENT_CHARS:
                raise EmptyLLMResponseError(len(content))
            if attempt > 1:
                logger.info(f"[{layer}] {agent_name} 第 {attempt} 次尝试成功（工具调用）")
            return response
        except Exception as e:
            elapsed = _time.perf_counter() - start_time
            last_error = e
            if isinstance(e, EmptyLLMResponseError):
                timeout_streak = 0
                if attempt < _MAX_RETRIES:
                    delay = _BACKOFF_BASE ** attempt
                    logger.warning(
                        f"[{layer}] {agent_name} 空响应，第 {attempt}/{_MAX_RETRIES} 次重试，"
                        f"{delay:.0f}s 后重试: {e}"
                    )
                    _time.sleep(delay)
                else:
                    break
            elif is_retryable_error(e):
                if isinstance(e, APITimeoutError):
                    timeout_streak += 1
                else:
                    timeout_streak = 0
                if timeout_streak >= consecutive_timeout_limit:
                    print(f"  [{layer}] {agent_name} 🔥 {step_prefix}连续超时 ×{timeout_streak}，熔断该通道，跳至下一步", flush=True)
                    logger.warning(f"[{layer}] {agent_name} {step_label} 连续超时 ×{timeout_streak}，熔断")
                    break
                # 非 timeout 错误（或曾被非 timeout 打断的 streak）仍受 _MAX_RETRIES 上限约束；
                # 只有「从第 1 次起连续超时」的纯超时场景才可重试至 consecutive_timeout_limit 次。
                if attempt >= _MAX_RETRIES and timeout_streak < attempt:
                    break
                delay = _BACKOFF_BASE ** attempt
                max_attempts = max(_MAX_RETRIES, consecutive_timeout_limit)
                print(f"  [{layer}] {agent_name} 🔄 {step_prefix}{elapsed:.0f}s 后失败 → retry {attempt}/{max_attempts} ({delay:.0f}s backoff)", flush=True)
                logger.warning(
                    f"[{layer}] {agent_name} 工具调用第 {attempt}/{max_attempts} 次失败（可重试），"
                    f"{delay:.0f}s 后重试: {e}"
                )
                _time.sleep(delay)
            else:
                break
    raise last_error  # type: ignore[misc]


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
        step_label = f"{prov}/{model} (step {step_num}/{len(steps)})"
        try:
            print(f"  [{layer}] {agent_name} ⏳ calling {prov}/{model} (step {step_num}/{len(steps)}, timeout={timeout}s)...", flush=True)
            llm = create_llm_client(
                provider=prov, model=model, base_url=base_url,
                temperature=temperature, max_tokens=max_tokens,
                request_timeout=timeout,
            )
            llm_with_tools = llm.bind_tools(tools)
            start_time = _time.perf_counter()
            response = _invoke_tools_with_retry(llm_with_tools, messages, agent_name, layer, step_label=step_label)
            elapsed = _time.perf_counter() - start_time
            n_tool_calls = len(getattr(response, "tool_calls", None) or [])
            print(f"  [{layer}] {agent_name} ✅ {elapsed:.1f}s, {n_tool_calls} tool_calls", flush=True)
            if step_num > 1:
                logger.info(f"[{layer}] {agent_name} 降级 {prov}/{model} 成功")
            else:
                logger.info(f"[{layer}] {agent_name} step1 ({prov}/{model}) 成功")
            return response
        except Exception as e:
            last_error = e
            # 输入超长 = 确定性错误，fail-fast，绝不再烧剩余降级调用
            if is_input_too_long_error(e):
                raise RuntimeError(f"[{layer}] {agent_name} 输入超长（超过模型上下文上限），请剥离附件数据/精简输入后重试: {e}") from e
            print(f"  [{layer}] {agent_name} ⚠️ {prov}/{model} unavailable → trying next fallback", flush=True)
            logger.warning(f"[{layer}] {agent_name} step{step_num} ({prov}/{model}) 不可用: {e}")

    raise RuntimeError(f"[{layer}] {agent_name} 降级链全部失败: {last_error}")
