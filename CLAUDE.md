# MathModelingAgents Project

Multi-agent mathematical modeling framework using LangGraph. 5-layer architecture with agentic tool calling.

## Architecture
- Layer 1: Problem Analysis (Decomposer → DataAnalyst → ConstraintAnalyst → ProblemManager, 4 agents)
- Layer 2: Mathematical Modeling (ModelerA → ModelerB → ModelerC → ModelingManager, debate loop, 4 agents)
- Layer 3: Code Implementation (SolverAgent → ImplManager → VizAgent, retry loop + visualization, 3 agents)
- Layer 4: Paper Writing (PaperAgent → PaperManager, agentic section-by-section loop, 2 agents)
- Layer 5: Sensitivity Analysis (optional, 3 agents)

Total: 16 agents (down from 19 — Layers 3 and 4 merged from 3-agent chains; Layer 3 split into Solver+Viz)

## Layer 3 — SolverAgent + VizAgent + ImplManager
- **SolverAgent**: Single agent with real tools: run_code, read_file, write_file, list_dir
  - Internal loop (max 30 iterations): write code → execute → see output/errors → fix → re-execute
  - Focus: solving math problems, producing results.json
  - Self-check with SELF_CHECK_PASSED marker
  - **Message persistence**: on RETRY, inherits full tool-calling history from previous run (no cold start)
- **ImplManager**: External review only (no tools), checks solver output against Layer 2 model
  - Issues RETRY with specific instructions when problems found
  - On CONCLUDE: clears impl_messages to keep state clean for next layers
- **VizAgent**: Single agent with same tools, focused on chart generation
  - Internal loop (max 15 iterations): read results.json → generate PNG charts → verify → self-check
  - Handles Chinese font auto-detection for chart labels
  - Outputs to clear_impl → next layer

## Layer 4 — PaperAgent
- Single agent with read-only tools: read_file, list_dir, write_file (NO run_code)
- Section-by-section loop: write §N → read_file verify facts against source data → fix → lock → next
- Can go back to fix previous sections if inconsistencies discovered
- Self-check with SELF_CHECK_PASSED marker
- PaperManager does external review (no tools), issues REVISE with §-level specific feedback

## Prompt Caching
- All 16 system prompts are pure static strings (no f-string variable injection)
- Dynamic values (output_dir, round_count, retry_count, etc.) moved to user messages
- _build_context() now includes all runtime config in the "当前状态" section
- Enables LLM API prefix caching for 100% system prompt cache hit rate per agent

## Chinese Font Handling
- Sandbox preamble auto-detects CJK fonts (SimHei > Microsoft YaHei > STSong > ...)
- VizAgent prompt requires checking font availability before using Chinese labels
- Falls back to English labels if no CJK font detected (prevents tofu boxes)

## Key Files
- `mathmodelingagents/default_config.py` — model routing, max_tokens, temperature config
- `mathmodelingagents/agents/utils/prompt_templates.py` — all 15 static agent system prompts
- `mathmodelingagents/llm_clients/__init__.py` — LLM client factory (OpenCode Go / DeepSeek)
- `mathmodelingagents/agents/__init__.py` — agent node factory functions (incl. tool-calling loops)
- `mathmodelingagents/tools/__init__.py` — sandbox code execution + LangChain tool wrappers
- `mathmodelingagents/graph/setup.py` — StateGraph construction

## Known Model Issues
- kimi-k2.7-code: returns empty on long Chinese math prompts → REMOVED from config (2026-07-17)
- qwen3.7-max: returns empty on long Chinese math prompts (modeling scenarios) → OK for paper writing
- glm-5.2/glm-5.1: returns empty on long Chinese math prompts → DO NOT USE
- deepseek-v4-flash: works but lower quality for complex reasoning
- deepseek-v4-pro: works well, deep reasoning, best for manager/agent/coder roles

## Config Rules
- Max_tokens_overrides: agent_name priority > role > default (1024)
- Temperature_overrides: role > default (0.0)
- All layers timeout: 10800s (3 hours)
- **Unified fallback chain** (`invoke_with_fallback` in `llm_clients/__init__.py`):
  1. Primary provider + role model (e.g. opencode + deepseek-v4-pro)
  2. Fallback provider (deepseek official API) + same model name
  3. Primary provider + flash model (deepseek-v4-flash)
  4. Fallback provider + flash model
  Each step: 3 retries with exponential backoff (2s → 4s → 8s) for transient errors
- Empty-content detection: `_invoke_with_retry` treats responses < 10 chars as model failure → triggers retry/fallback

## Sandbox Security
- Blocked modules: socket, requests, urllib, http, ftplib, telnetlib, smtplib, poplib, imaplib, ctypes
- Subprocess, os, threading, shutil allowed (needed by matplotlib/numpy/scipy internals)
- Code executes as subprocess with temp directory isolation
- All network and native code loading vectors blocked

## Standard
- Python with type hints
- pytest for tests
