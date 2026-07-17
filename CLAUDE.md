# MathModelingAgents Project

Multi-agent mathematical modeling framework using LangGraph. 5-layer architecture.

## Architecture
- Layer 1: Problem Analysis (Decomposer → DataAnalyst → ConstraintAnalyst → ProblemManager)
- Layer 2: Mathematical Modeling (ModelerA → ModelerB → ModelerC → ModelingManager, debate loop)
- Layer 3: Code Implementation (AlgorithmDesigner → Coder → Visualizer → ImplManager, retry loop)
- Layer 4: Paper Writing (PaperArchitect → SectionWriter → ChartDesigner → PaperManager, revise loop)
- Layer 5: Sensitivity Analysis (optional)

## Key Files
- `mathmodelingagents/default_config.py` — model routing, max_tokens, temperature config
- `mathmodelingagents/agents/utils/prompt_templates.py` — all agent system prompts
- `mathmodelingagents/llm_clients/__init__.py` — LLM client factory (OpenCode Go / DeepSeek)
- `mathmodelingagents/graph/setup.py` — StateGraph construction
- `mathmodelingagents/agents/__init__.py` — agent node factory functions

## Known Model Issues
- kimi-k2.7-code: returns empty on long Chinese math prompts → REMOVED from config (2026-07-17)
- qwen3.7-max: returns empty on long Chinese math prompts → DO NOT USE
- glm-5.2/glm-5.1: returns empty on long Chinese math prompts → DO NOT USE
- deepseek-v4-flash: works but lower quality for complex reasoning
- deepseek-v4-pro: works well, deep reasoning, best for manager/agent/coder roles

## Config Rules
- Max_tokens_overrides: agent_name priority > role > default (1024)
- Temperature_overrides: role > default (0.0); all kimi models removed, no overrides needed
- All layers timeout: 10800s (3 hours)
- **Unified fallback chain** (`invoke_with_fallback` in `llm_clients/__init__.py`):
  1. Primary provider + role model (e.g. opencode + deepseek-v4-pro)
  2. Fallback provider (deepseek official API) + same model name
  3. Primary provider + flash model (deepseek-v4-flash)
  4. Fallback provider + flash model
  Each step: 3 retries with exponential backoff (2s → 4s → 8s) for transient errors
- Empty-content detection: `_invoke_with_retry` treats responses < 10 chars as model failure → triggers retry/fallback

## Standard
- Python with type hints
- pytest for tests
