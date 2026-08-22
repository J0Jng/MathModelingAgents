# MathModelingAgents

多智能体数学建模框架：五层 LangGraph 流水线，从题目分析到最终论文。本文件是项目唯一术语表（glossary），只定义领域概念，不记录实现细节。

## Language

### 层与流转

**层 (Layer)**:
流程中的一个阶段单元，拥有自己的 Agent 组、内部循环与完成裁决。层间按序流转，可通过启动配置跳过。
_Avoid_: 阶段、步骤（指 Layer 时）

**裁决 (Verdict)**:
Manager Agent 对本层是否完成的判断（CONTINUE / CONCLUDE / REVISE / RETRY），以输出标记表达，由路由消费。
_Avoid_: 决定、结论（指 Verdict 时）

**跨层摘要 (Layer Summary)**:
每层 Manager 在 CONCLUDE 时产出的精炼总结，供后续各层快速定位上下文。与完整层产出相对。
_Avoid_: 报告、总结（指 Layer Summary 时）

### 敏感性分析

**敏感性决策 (Sensitivity Decision)**:
Layer 1 分析题目后做出的、针对本题的「是否需要敏感性分析」的判断，附理由。是敏感性分析层的唯一 affirmative 启用来源。
_Avoid_: 敏感性开关、开关、enable_sensitivity

**敏感性模式 (Sensitivity Mode)**:
用户对敏感性分析的三档兜底控制：`auto`（默认，尊重敏感性决策）、`always`（强制执行）、`never`（强制跳过）。取代已退役的布尔开关与 `selected_layers` 中的 5。
_Avoid_: enable_sensitivity、selected_layers 含 5 的写法

**敏感性分析层 (Layer 5)**:
启用时插在论文层之前执行的条件层，以求解层的结构化结果（results.json）为输入前提；扰动关键参数、检验结论稳健性，产出并入最终论文。求解层被跳过时本层不可用。
_Avoid_: 收尾附加层、后处理层（旧定位，已废弃）

**无决策默认 (Fail-open Default)**:
auto 模式下因任何原因拿不到敏感性决策（标记缺失、调用失败、Layer 1 被跳过）时，默认执行敏感性分析。
_Avoid_: fail-closed（已否决的反向选择）
