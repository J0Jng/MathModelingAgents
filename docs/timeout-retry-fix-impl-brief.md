# 修复任务书：volcengine coding 端点超时挂起 + timeout 异常不可重试

## 背景

在 `F:\code\projects\MathModelingAgents` 上跑 RAG 端到端测试（provider=volcengine，全角色 deepseek-v4-flash）时，L2 数学建模层卡死 78 分钟无任何输出。经系统排查，定位到**两个叠加的 bug**：

### 根因 1（主 bug）：超时 3 小时太长，挂起请求无法快速失败

- `volcengine`（Coding Plan，端点 `https://ark.cn-beijing.volces.com/api/coding/v3`）的 `deepseek-v4-flash` 对**复杂/较长 prompt 会间歇性挂起**：请求既不返回也不抛异常，短 prompt（"1+1"）1.8s 正常，复杂 modeler prompt 可挂 20s~3h 不等。
- 而代码里的超时统一配成 **`10800s`（3 小时）**（注释「不限时间，确保推理模型完整跑完」）。结果是挂起请求要等满 3 小时才超时，整个 graph 卡死在单次 `llm.invoke()` 上。
- **已实测验证**：`request_timeout=20s` 时，挂起请求会在 20.2s 抛出 `OpenAITimeoutError`，说明降低超时能让挂起请求快速失败。

### 根因 2（次 bug）：`OpenAITimeoutError` 不被识别为可重试

- `OpenAITimeoutError` 的消息文本是 `"Request timed out."`（**"timed out" 中间有空格**）。
- `is_retryable_error()`（`llm_clients/__init__.py:53-71`）匹配的子串是 `"timeout"`（**无空格**），导致 `"Request timed out."` 这个异常**永远无法被识别为可重试**。
- 后果：`_invoke_with_retry` 的指数退避重试（`llm_clients/__init__.py:74-118`）、`_run_tool_loop` 的 invoke 重试（`agents/__init__.py:366-379`）、`invoke_with_fallback` 的降级链（`llm_clients/__init__.py:293-365`）全部失效——超时异常直接向上抛，走不到重试和降级。

## 设计决策

修复必须让「挂起请求快速失败 + 异常被识别为可重试 + 走现有重试/降级链」，从而把「无限挂死」变成「最多几十秒后自动重试或降级」。

### 决策 1：降低默认超时（从 10800s → 180s）

- `DEFAULT_TIMEOUT`（`llm_clients/__init__.py:23`）和 `default_config.py` 里 `layer_timeouts` 的默认值（130-134 行）从 `10800` 降到 **`180`**（3 分钟）。
- 理由：单次 LLM 调用正常应在几十秒内返回；180s 既给推理模型充足余量（复杂数学推导），又不会让挂起请求卡死整个流程。挂起会在 180s 抛 `OpenAITimeoutError`，随后被重试链捕获。

### 决策 2：让 `is_retryable_error` 识别 timeout 异常（补上 "timed out" 与 `APITimeoutError`）

- 在 `is_retryable_error()` 里补两个判定，二者任一命中即返回 True：
  1. `"timed out" in msg`（覆盖 "Request timed out." / "OpenAI API request timed out" 等带空格写法）；
  2. 直接用类型判定：`isinstance(error, APITimeoutError)` 或 `isinstance(error, APIConnectionError)`（覆盖异常消息不含 "timeout" 字样的边界情况）。
- 推荐同时补 `"timed out"` 子串（最小改动、不引入 openai import 依赖）**和** `APITimeoutError`/`APIConnectionError` 类型判定（更稳，因为 openai 是既有依赖）。二选一也可，但类型判定更可靠。

### 决策 3：`invoke_with_fallback` 里 provider 超时参数对齐

- `invoke_with_fallback`（`llm_clients/__init__.py:328`）读 `layer_timeouts` 得到 timeout，和 `create_layer_llm` 一致，无需改逻辑，只需确认它走的是 `DEFAULT_TIMEOUT`（改后 180s）。

## 改动清单（含文件路径、行号提示）

### 1. `mathmodelingagents/llm_clients/__init__.py`

- **第 23 行**：`DEFAULT_TIMEOUT = 10800` → `DEFAULT_TIMEOUT = 180`
- **第 29-40 行**：`_RETRYABLE_SUBSTRINGS` 元组里追加 `"timed out"`（可加在 `"timeout"` 之后）
- **第 53-71 行** `is_retryable_error()`：函数体开头补类型判定：
  ```python
  from openai import APITimeoutError, APIConnectionError
  if isinstance(error, (APITimeoutError, APIConnectionError)):
      return True
  ```
  （import 放函数内或文件顶部均可，函数内更内聚；注意 openai 已是项目依赖）

### 2. `mathmodelingagents/default_config.py`

- **第 130-134 行**：`layer_timeouts` 各层默认值 `"10800"` → `"180"`（共 5 处：problem/modeling/implementation/paper/sensitivity）

### 3. 注释同步（非必须但建议）

- `llm_clients/__init__.py` 第 11-13 行模块 docstring、第 148 行「从 config.layer_timeouts 读取」相关注释，把「统一 10800s = 3h」「不限时间」改为「默认 180s，超时走重试/降级」。

## 可运行验收标准

修复后，在项目根目录跑以下命令，全部通过才算完成：

```bash
cd "/f/code/projects/MathModelingAgents" && export PYTHONPATH=
# 1. 单元/回归测试不破
.venv/Scripts/python.exe -m pytest tests/ -q 2>&1 | tail -20
# 2. 验证 is_retryable_error 能识别 timeout（用探针脚本即可，或临时 python -c）
.venv/Scripts/python.exe -c "
from mathmodelingagents.llm_clients import is_retryable_error
class FakeTimeout(Exception): pass
print('timed out 可重试:', is_retryable_error(FakeTimeout('Request timed out.')))
print('timeout 可重试:', is_retryable_error(FakeTimeout('timeout')))
try:
    from openai import APITimeoutError
    print('APITimeoutError 可重试:', is_retryable_error(APITimeoutError('x')))
except Exception as e:
    print('import err:', e)
"
# 3. 验证默认超时已改（读 config 值，不跑真实 LLM）
.venv/Scripts/python.exe -c "
from mathmodelingagents.default_config import DEFAULT_CONFIG
from mathmodelingagents.llm_clients import DEFAULT_TIMEOUT
print('DEFAULT_TIMEOUT=', DEFAULT_TIMEOUT)
print('layer_timeouts=', DEFAULT_CONFIG['layer_timeouts'])
"
```

预期：
1. `pytest` 全绿（或与改动无关的既有失败，需说明）。
2. 三个 `is_retryable_error` 判定都输出 `True`。
3. `DEFAULT_TIMEOUT=180`，且 `layer_timeouts` 各层都是 `180`。

## Do NOT 清单

- ❌ 不要改 `_invoke_with_retry` 的重试次数（3 次）、退避基数（2.0）或 `EmptyLLMResponseError` 判定阈值（`_MIN_CONTENT_CHARS=10`）——那些与本次 bug 无关。
- ❌ 不要动 `cli/model_picker.py`、`cli/model_catalog.py` 的模型选择逻辑。
- ❌ 不要改 `temperature_overrides`、`default_max_tokens`、`max_tokens_overrides` 等生成参数。
- ❌ 不要新增 provider 或改 provider 端点 URL（`base_url`）。
- ❌ 不要改 `default_config.py` 里除 `layer_timeouts` 默认值外的任何内容。
- ❌ 不要删除/重写 `is_retryable_error` 里现有的 HTTP 状态码（429/500/502/503/504）判定——只做增量补充。
- ❌ 不要动 `mathmodelingagents/agents/__init__.py` 的 `_run_tool_loop` 重试逻辑（它调用 `is_retryable_error`，修好后者即可，前者无需改）。
- ❌ 不要跑真实 LLM 端到端流程来「验收」（费钱）；用上述离线命令验收即可。

## 交付

- 修改上述 2 个源码文件（+ 可选注释）。
- 跑完验收命令，把 3 条命令的真实输出贴回。
- 说明 pytest 结果（通过数 / 失败项及原因）。
