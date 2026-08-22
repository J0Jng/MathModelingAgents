# 敏感性分析启用决策权从静态配置移交 Layer 1

数模竞赛论文通常需要灵敏度分析章节，但是否值得花三小时取决于题目本身（是否存在值得扰动的关键参数/不确定性）。我们决定：启用与否由 Layer 1 在分析题目后动态判断（「敏感性决策」），用户配置降级为三档兜底 `sensitivity_mode`（`auto` 默认尊重决策 / `always` / `never`），取代旧的布尔 `enable_sensitivity` 与 `selected_layers` 含 5 的写法（旧写法静默映射：`true`/含 5 -> `always`，`false`/缺省 -> `auto`，附弃用警告）。

决策通道采用 ProblemManager CONCLUDE 后追加的一次结构化 LLM 调用（function calling，schema `{enabled, reason}`），而非散文标记解析或给 Manager 绑工具--前者受 LLM 格式漂移影响（Layer 3 已吃过静默通过的亏），后者打破「Manager 无工具、只做外部评审」的架构不变量。auto 模式下拿不到决策时 fail-open 默认执行：拿不到决策意味着没有反对执行的理由，而漏掉敏感性分析对竞赛评分的伤害大于多跑一层的时间成本。

## Consequences

- 敏感性决策 + 理由注入 Layer 2/3 上下文：建模与求解需为扰动预留显式参数面，否则 Layer 5 无从下手。
- 图构建层必须无条件构建 Layer 5 节点，启用与否推迟到运行时路由决定。
- `selected_layers` 不再是 Layer 5 的硬门，但对 Layer 1-4 语义不变。
