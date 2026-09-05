# 修复：空 LLM 响应不重试，导致 openCode 网关偶发空包即跳降级

## 背景

2026-09-03 实测，`opencode/deepseek-v4-flash`（推理模型）响应极慢（单次 24–80s）且偶发「HTTP 200 但正文为空（0 字符）」的瞬态故障。

框架现行为：`_invoke_with_retry` 检测到空/极短内容时 `raise ValueError("模型返回空/极短内容 …")`，该异常不含任何可重试子串，`is_retryable_error()` 返回 `False` → **空包只试 1 次**就跳到 next provider 降级链。对如此不稳定的网关，空包应该像 503 一样先在本地指数退避重试，再换 provider。

同时日志只打印 `⚠️ <provider>/<model> unavailable → trying next fallback`，无法分辨是 503 还是空响应，排障困难。

## 改动范围

只改 **1 个文件**：`mathmodelingagents/llm_clients/__init__.py`

## 具体改动

### 1. 新增异常类型 `EmptyLLMResponseError(ValueError)`

放在 `_MIN_CONTENT_CHARS` 常量附近。

```python
class EmptyLLMResponseError(ValueError):
    """LLM 返回空/极短内容（HTTP 200 但正文无有效输出），视为可重试的瞬态模型故障。"""
```

### 2. `is_retryable_error()` 识别该异常

在函数开头加：

```python
if isinstance(error, EmptyLLMResponseError):
    return True
```

位置放在取 `str(error)` 之前即可（空响应必可重试）。

### 3. `_invoke_with_retry()` 空内容判定改用新异常

`raise ValueError(...)` → `raise EmptyLLMResponseError(...)`。

这样空响应进入「可重试」分支（`is_retryable_error` = True），走 `2s→4s→8s` 指数退避重试 3 次。若 3 次仍空，才抛给上层 `invoke_with_fallback` 进入下一 provider。

### 4. 日志区分：空响应 vs 网络错误

`_invoke_with_retry` 的 except 分支里，区分两种可重试场景，打印不同文案：

```python
if isinstance(e, EmptyLLMResponseError):
    print(f"  [{layer}] {agent_name} ⚠️ 空响应 ({len(str(e))}?) retry {attempt}/{_MAX_RETRIES} ({delay:.0f}s backoff)", flush=True)
    logger.warning(f"[{layer}] {agent_name} 空响应，第 {attempt}/{_MAX_RETRIES} 次重试，{delay:.0f}s 后重试: {e}")
elif attempt < _MAX_RETRIES and is_retryable_error(e):
    ...  # 原有逻辑
```

注意：`len(str(e))` 是错误消息长度，不是响应字符数。若要精确显示字符数，可给 `EmptyLLMResponseError` 加一个 `char_count` 属性，在 raise 处传入。**建议实现该属性**，日志打印 `空响应 (0 字符) retry 1/3 (4s backoff)`，可读性最好。

建议：
```python
class EmptyLLMResponseError(ValueError):
    def __init__(self, char_count: int):
        self.char_count = char_count
        super().__init__(f"模型返回空/极短内容 ({char_count} 字符)，视为模型故障")
```
raise: `raise EmptyLLMResponseError(len(result))`

日志: `f"  [{layer}] {agent_name} ⚠️ 空响应 ({e.char_count} 字符) retry {attempt}/..."`

## 不改的东西（Do NOT）

- 不改 `invoke_with_fallback()` 的 4 步降级链结构。
- 不改 `_RETRYABLE_CODES` / `_RETRYABLE_SUBSTRINGS`。
- 不改任何其他文件、graph 拓扑、AgentState。
- 不引入新依赖。

## 验收标准

新增验证脚本 `scripts/verify_empty_response_retry.py`（相当于 ad-hoc 验证，见下），或 pytest 单测。标准：

1. `is_retryable_error(EmptyLLMResponseError(0))` 返回 `True`。
2. `is_retryable_error(ValueError("其它错误"))` 返回 `False`（不误伤）。
3. `_invoke_with_retry` stub 掉 `llm.invoke` 首回掷出 `EmptyLLMResponseError`、第二次返回正常内容 → 返回该内容（证明空包被重试后成功）。
4. `_invoke_with_retry` 每次都掷空 → 抛 `EmptyLLMResponseError`（3 次耗尽）。
5. 既有行为不回归：普通网络异常（如 `ValueError("Error code: 503")`）仍按原路径重试。

### 验证脚本（`scripts/verify_empty_response_retry.py`）

用 `unittest`/`pytest` 风格，`_MAX_RETRIES` 较大时用 `monkeypatch` 或临时换小值避免等 2+4+8s。可用 `unittest.mock.patch` 把 `_BACKOFF_BASE` 或把 `_time.sleep` 替换为 no-op 来加速 2s/4s/8s 等待。脚本跑完打印 `✅ 全部通过` 或清晰断言失败。**脚本属 ad-hoc 验证，允许放在 repo，不强制入库测试套件**（项目惯例：`_verify_*` 脚本不入 pytest）。

## 完成定义（Done）

- `mathmodelingagents/llm_clients/__init__.py` 改动生效，`py_compile` 通过。
- 运行 `python scripts/verify_empty_response_retry.py` 全部断言通过。
- 运行既有相关测试确认无回归：`python -m pytest tests/ -q -x`（或至少 `tests/test_model_picker.py`、顺手覆盖导入）。
- `git diff --stat` 确认只动目标文件 + 新增验证脚本。