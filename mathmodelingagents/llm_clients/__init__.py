"""LLM Client Factory — 支持 OpenCode Go 和 DeepSeek。

使用 langchain-openai 的 ChatOpenAI，避免直接 import openai。

Timeout 策略（从 config.layer_timeouts 读取，统一 10800s = 3h）：
  - 不限时间，确保推理模型能完整跑完
"""

import logging
import os
import time as _time
from typing import Any

logger = logging.getLogger(__name__)

# 默认超时（秒）— 当 config 中未指定时使用，统一 3 小时
DEFAULT_TIMEOUT = 10800

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
    "failover_exhausted",
    "temporarily unavailable",
    "server error",
)
_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0  # 2s → 4s → 8s
_MIN_CONTENT_CHARS = 10  # 低于此长度的输出视为模型故障


def _is_retryable(error: Exception) -> bool:
    """判断异常是否可重试（瞬态故障）。"""
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


def _invoke_with_retry(
    llm,
    messages: list,
    agent_name: str,
    layer: str,
) -> str:
    """带重试的 LLM 调用，指数退避 2s → 4s → 8s。"""
    last_error = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = llm.invoke(messages)
            result = response.content
            if not result or len(result.strip()) < _MIN_CONTENT_CHARS:
                raise ValueError(
                    f"模型返回空/极短内容 ({len(result)} 字符)，视为模型故障"
                )
            if attempt > 1:
                logger.info(f"[{layer}] {agent_name} 第 {attempt} 次尝试成功")
            return result
        except Exception as e:
            last_error = e
            if attempt < _MAX_RETRIES and _is_retryable(e):
                delay = _BACKOFF_BASE ** attempt
                logger.warning(
                    f"[{layer}] {agent_name} 第 {attempt}/{_MAX_RETRIES} 次失败（可重试），"
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
        provider: "opencode" 或 "deepseek"
        model: 模型名称
        base_url: 自定义 API 地址
        temperature: 温度参数
        max_tokens: 最大输出 token
        request_timeout: HTTP 请求超时（秒），从 config.layer_timeouts 读取

    Returns:
        langchain_openai.ChatOpenAI 实例
    """
    from langchain_openai import ChatOpenAI

    if provider == "opencode":
        api_key = os.getenv("OPENCODE_GO_API_KEY", "")
        if not api_key:
            # fallback: try loading from ~/.hermes/.env
            from dotenv import load_dotenv as _ld
            _ld(os.path.expanduser("~/.hermes/.env"))
            api_key = os.getenv("OPENCODE_GO_API_KEY", "")
        if not api_key:
            raise ValueError("OPENCODE_GO_API_KEY 环境变量未设置，且 ~/.hermes/.env 中也未找到")
        url = base_url or "https://opencode.ai/zen/go/v1"
    elif provider == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY 环境变量未设置")
        url = base_url or "https://api.deepseek.com/v1"
    else:
        raise ValueError(f"不支持的 LLM provider: {provider}")

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
    )


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
            return config["deep_think_llm"]
        return config["quick_think_llm"]

    # OpenCode 模式：查 layer_model_overrides
    overrides = config.get("layer_model_overrides", {})
    layer_config = overrides.get(layer, {})

    if role in layer_config:
        return layer_config[role]
    if "agent" in layer_config:
        return layer_config["agent"]
    return config["quick_think_llm"]


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
    provider = config.get("llm_provider", "opencode")
    fallback_provider = config.get("fallback_provider", "deepseek")
    timeout = config.get("layer_timeouts", {}).get(layer, DEFAULT_TIMEOUT)

    primary_model = get_layer_model(config, layer, role)
    if max_tokens is None:
        max_tokens = resolve_max_tokens(config, role)

    temp_overrides = config.get("temperature_overrides", {})
    temperature = temp_overrides.get(role, config.get("default_temperature", 0.0))

    fallback_base_url = config.get("fallback_base_url")
    flash_model = config.get("quick_think_llm", "deepseek-v4-flash")

    steps = [
        (provider, primary_model, None),
        (fallback_provider, primary_model, fallback_base_url),
        (provider, flash_model, None),
        (fallback_provider, flash_model, fallback_base_url),
    ]

    last_error = None
    for step_num, (prov, model, base_url) in enumerate(steps, 1):
        try:
            llm = create_llm_client(
                provider=prov, model=model, base_url=base_url,
                temperature=temperature, max_tokens=max_tokens,
                request_timeout=timeout,
            )
            result = _invoke_with_retry(llm, messages, agent_name, layer)
            if step_num > 1:
                result = f"[降级 {prov}/{model}]\n\n{result}"
            logger.info(f"[{layer}] {agent_name} step{step_num} ({prov}/{model}) 成功")
            return result
        except Exception as e:
            last_error = e
            logger.warning(f"[{layer}] {agent_name} step{step_num} ({prov}/{model}) 不可用: {e}")

    raise RuntimeError(f"[{layer}] {agent_name} 降级链全部失败: {last_error}")
