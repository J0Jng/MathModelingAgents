# 修复任务书：结构化输出 max_tokens=1024 过小导致解析失败

## 背景

在 `F:\code\projects\MathModelingAgents` 上跑 RAG 端到端测试（provider=volcengine-plan，全角色 deepseek-v4-flash）时，Layer 1 的结构化输出调用失败，报错：

```
Could not parse response content as the length limit was reached -
CompletionUsage(completion_tokens=1024, prompt_tokens=2390, total_tokens=3414,
  completion_tokens_details=CompletionTokensDetails(..., reasoning_tokens=1024, ...))
```

### 根因

`deepseek-v4-flash` 是**推理模型**，它的 token 预算先分配给 `reasoning_tokens`（思考链），剩下的才给 `content`（正文）。

而 `mathmodelingagents/agents/__init__.py` 里两处结构化输出调用显式传了 `max_tokens=1024`：

- `_run_sensitivity_decision`（第 65 行）：`create_layer_llm(config, "problem", "manager", max_tokens=1024)`
- `_run_model_candidate_search`（第 170 行）：`create_layer_llm(config, "problem", "manager", max_tokens=1024)`

两处都接 `with_structured_output(...)`，需要输出结构化 JSON。但 `max_tokens=1024` 太小，**1024 个 token 全被推理占用**（usage 里 `reasoning_tokens=1024`），导致正文 JSON 输出 0 个 token → 截断 → 无法 parse → 抛异常走 fail-open 降级。

**为什么其他节点没事**：其他节点走 `resolve_max_tokens` 默认链拿到 `default_max_tokens=16384`（`.env` 里 `MATHMODELING_DEFAULT_MAX_TOKENS=16384`），只有这两个结构化调用点被显式 `max_tokens=1024` 压小。

## 设计决策

**删掉两处的显式 `max_tokens=1024`，让它们走默认解析链**（`create_layer_llm` 内 `max_tokens is None` 时 fallback 到 `default_max_tokens=16384`）。

- 理由：结构化输出需要「推理 token + JSON 正文 token」，16384 足够；显式传 1024 是当初写死的错误默认值。
- 这是最小改动：只删参数，不改 `create_layer_llm` 签名、不改默认值、不加 `reasoning_effort` 逻辑。

## 改动清单（精确到行）

### 文件：`mathmodelingagents/agents/__init__.py`

1. **第 65 行**：
   ```python
   # 改前
   llm = create_layer_llm(config, "problem", "manager", max_tokens=1024)
   # 改后
   llm = create_layer_llm(config, "problem", "manager")
   ```

2. **第 170 行**：
   ```python
   # 改前
   llm = create_layer_llm(config, "problem", "manager", max_tokens=1024)
   # 改后
   llm = create_layer_llm(config, "problem", "manager")
   ```

两处都是 `create_layer_llm(config, "problem", "manager", max_tokens=1024)` 这同一串，去掉 `, max_tokens=1024` 即可。

## 可运行验收标准

修复后，在项目根目录跑：

```bash
cd "/f/code/projects/MathModelingAgents" && export PYTHONPATH=
# 1. pytest 回归（不依赖真实 LLM，用 mock，应全绿）
.venv/Scripts/python.exe -m pytest tests/ -q 2>&1 | tail -15

# 2. 离线验证：确认删除 max_tokens=1024 后，create_layer_llm 走默认 16384
.venv/Scripts/python.exe -c "
from dotenv import load_dotenv; load_dotenv()
from mathmodelingagents.default_config import DEFAULT_CONFIG
from mathmodelingagents.llm_clients import create_layer_llm
# create_layer_llm 内部走 resolve_max_tokens 会拿到 default_max_tokens
from mathmodelingagents.llm_clients import resolve_max_tokens
print('resolve(problem, manager) =', resolve_max_tokens(DEFAULT_CONFIG, 'problem', 'manager'))
print('期望 =', 16384)
"

# 3. 源码核对：确认 agents/__init__.py 里再无 max_tokens=1024
grep -n "max_tokens=1024" mathmodelingagents/agents/__init__.py || echo "✅ 已无 max_tokens=1024"
```

预期：
1. `pytest` 全绿（尤其 `test_sensitivity_decision.py`、`test_knowledge_retrieval.py`，它们 monkeypatch 了 `create_layer_llm`，不受真实调用影响）。
2. `resolve(problem, manager) = 16384`。
3. grep 无结果（打印 ✅）。

## Do NOT 清单

- ❌ 不要改 `create_layer_llm` / `create_llm_client` / `resolve_max_tokens` 的函数签名或默认值。
- ❌ 不要动 `default_max_tokens`（16384）、`max_tokens_overrides`、`temperature_overrides`。
- ❌ 不要加 `reasoning_effort` 相关逻辑（那是另一个端点特性，本次不涉及）。
- ❌ 不要改 `_run_sensitivity_decision` / `_run_model_candidate_search` 里除这两行 `create_layer_llm(...)` 调用外的任何代码（schema、prompt、try/except 结构都不动）。
- ❌ 不要改 `with_structured_output` 的 schema 定义。
- ❌ 不要跑真实 LLM 端到端流程（费钱）；用上述离线命令验收。

## 交付

- 修改 `mathmodelingagents/agents/__init__.py`（仅两行）。
- 跑完验收命令，把 3 条命令的真实输出贴回。
- 说明 pytest 结果。
