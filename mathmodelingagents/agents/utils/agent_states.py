"""Agent 状态定义 — 贯穿所有层的全局状态。"""

from typing import TypedDict, NotRequired


class DebateState(TypedDict):
    """通用辩论状态，所有辩论层共用。"""
    round_count: int
    max_rounds: int
    a_history: str
    b_history: str
    c_history: str
    history: str
    latest_speaker: str
    current_a_response: str
    current_b_response: str
    current_c_response: str
    judge_decision: str  # CONTINUE or CONCLUDE


class AgentOutput(TypedDict):
    """单个 Agent 的输出记录。"""
    agent: str           # Agent 名称，如 "decomposer", "modeler_a"
    layer: str           # 层名，如 "problem", "modeling"
    role: str            # 角色，如 "agent", "manager", "algorithm", "coder"
    round_num: int       # 该层内的第几轮（从 1 开始，无循环的层固定 1）
    output: str          # LLM 完整输出文本


class AgentState(TypedDict, total=False):
    """全局 Agent 状态，贯穿 Layer 1-5。"""

    # ═══ 输入 ═══
    problem_path: str               # 题目 Markdown 文件路径
    problem_description: str        # 题目简称/标题
    output_name: str                # 输出文件夹名

    # ═══ 消息历史 ═══
    messages: list                  # LangGraph 消息列表

    # ═══ 全部 Agent 输出记录（新增，用于报告生成）═══
    layer_outputs: list[AgentOutput]

    # ═══ Layer 1 产出 ═══
    problem_report: str
    constraints: str
    assumptions: str
    data_insights: str
    background_research: str        # Layer 1 自动搜索的题目背景资料（原始文本，供 Layer 2 注入）
    problem_messages: list
    sensitivity_enabled: bool       # Layer 1 敏感性决策（ADR-0001）；缺省视为 True（fail-open）
    sensitivity_reason: str         # 敏感性决策理由（随上下文注入 Layer 2/3，为扰动预留参数面）

    # ═══ Layer 2 产出 ═══
    model_debate_state: DebateState
    model_spec: str
    model_validation: str
    modeling_messages: list

    # ═══ Layer 3 产出 ═══
    algorithm_spec: str
    code_results: str          # SolverAgent 最终输出（求解代码 + results.json）
    viz_results: str           # VizAgent 最终输出（图表生成 + PNG 清单）
    visualizations: list[str]
    error_analysis: str        # ImplManager RETRY 时的审查反馈
    impl_messages: list        # SolverAgent 完整消息历史（RETRY 时跨轮继承）
    impl_retry_count: int

    # ═══ Layer 4 产出 ═══
    paper_outline: str
    final_paper: str
    paper_feedback: str       # PaperManager REVISE 时的审查意见
    paper_messages: list

    # ═══ Layer 5 产出 ═══
    sensitivity_scan: str
    sensitivity_report: str
    sensitivity_messages: list

    # ═══ 元信息 ═══
    current_layer: str
    layer_results: dict
    layer_summary: str          # 跨层摘要（每层 Manager 产出，仅精华注入下层）
