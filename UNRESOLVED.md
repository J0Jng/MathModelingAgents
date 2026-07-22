# 未解决问题 & 进度衔接

> 最后更新: 2026-07-22
> 当前分支: master
> 最后提交: d5dffc7 (v0.2.0: Agentic Tool Calling for Layer 3 & 4)

---

## 代码改动状态

全部改动已提交推送至 `master` 分支。下次启动时项目即处于当前最新状态。

## 已验证通过的改动 ✅

| 改动 | 验证方式 |
|---|---|
| SolverAgent 消息持久化（Plan A） | 第2次 SolverAgent 只输出 1,240 字符 vs 首次 322K，确认继承历史后快速修复 |
| ImplManager CONCLUDE 清空 impl_messages | 代码逻辑检查通过 |
| 11 个单元测试 | `pytest tests/test_layer3_layer4.py` 全部通过 |

## 尚未验证的改动 ⚠️

### 1. VizAgent 图表生成 — 两次运行均失败

**第一次失败原因**：上下文注入 50KB 题目描述 + 80KB Layer 2 辩论 → VizAgent 被淹没，未调用 run_code
**第二次失败原因**：同上——当时只修了 `problem_description` 跳过，但 `layer_summary` 仍被注入（80KB Layer 2 完整辩论）

**已做的修复（未验证）：**
- `_build_context`: VizAgent 跳过 `problem_description` ✅
- `_build_context`: VizAgent 跳过 `layer_summary` ✅（**刚刚加的，最关键**）
- `_build_context`: VizAgent implementation 分支只给文件路径（~300 字符）
- VizAgent prompt: 第一个动作强制 run_code，去掉"设计方案"阶段
- `run_layer3_resume.py`: `layer_summary` 从 80KB 精简为提取最后一轮 + CONCLUDE 部分（~8KB）

**验证方法：**
```bash
# 先修好 run_layer3_resume.py 的 import re 问题，然后：
python run_layer3_resume.py
# 检查 results/ 下是否有 PNG 文件
```

### 2. run_layer3_resume.py 的 import re 缺失

上次运行时脚本报错 `NameError: name 're' is not defined`。需要在该文件顶部添加 `import re`。

### 3. PaperAgent 消息持久化 — 未在真实运行中验证

代码已完成：
- `create_paper_agent`: REVISE 时从 `paper_messages` 继承消息历史
- `create_paper_manager`: CONCLUDE 时清空 `paper_messages`

需要一次 REVISE 场景才能验证（PaperManager 说 REVISE → PaperAgent 第二次进入 → 检查 messages 数量 > 2）。

### 4. PaperAgent + VizAgent 对联 — 端到端未验证

完整链路：
```
SolverAgent → ImplManager → VizAgent → PaperAgent → PaperManager
```
只在第一次运行中走通了 SolverAgent（当时还叫 CodingAgent），VizAgent 和 PaperAgent 都在上下文污染下运行。**三个修复叠加后的完整流程从未跑通过。**

## 下次启动时应该做什么

### 第一步：修 run_layer3_resume.py
在文件顶部 `import sys` 后面加 `import re`

### 第二步：跑完整流程
```bash
python run_layer3_resume.py
```

### 第三步：检查关键产物
```bash
# VizAgent 产出
ls results/*.png

# PaperAgent 产出  
cat paper/PaperAgent_paper.md | grep "!\["  # 是否有图片嵌入

# 论文数值是否与 results.json 一致
cat results/results.json
```

### 第四步（如果失败）：看 VizAgent 到底做了什么
```bash
grep "run_code\|plt.savefig\|SELF_CHECK_PASSED" Layer3_代码实现.md
```

## 已知的架构局限（不阻塞当前进度）

| 局限 | 说明 |
|---|---|
| VizAgent 无外部 manager | 图表失败不会触发重试，只能手动补 |
| VizAgent 图表质量不可控 | LLM 自由发挥，配色/布局不稳定 |
| PaperManager 的 REVISE 用 `model_debate_state.round_count` 计数 | 与 Layer 3 的 `impl_retry_count` 不是同一个计数器，可能造成轮次混淆 |
| `max_debate_rounds` 同时控制 Layer 2 辩论和 Layer 4 论文修改 | 语义不清晰，建议后续拆分为独立配置项 |

## 本次验证中"手动补"的内容

> 以下是人工介入完成的部分，下次如果自动流程未产出，可参考

- 手动生成 4 张 PNG 图表：`code/generate_charts.py`（脚本已保存在输出目录）
- 手动更新论文数值并嵌入图表引用
- 论文最终版本：`C:\Users\joeji\Desktop\绿色物流配送_L3重跑\paper\PaperAgent_paper.md`
