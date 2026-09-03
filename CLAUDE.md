# MathModelingAgents Project

Multi-agent mathematical modeling framework using LangGraph. 5-layer architecture with agentic tool calling.

## Architecture
- Layer 1: Problem Analysis (Decomposer → DataAnalyst → ConstraintAnalyst → ProblemManager, 4 agents)
- Layer 2: Mathematical Modeling (ModelerA → ModelerB → ModelerC → ModelingManager, debate loop, 4 agents)
- Layer 3: Code Implementation (SolverAgent → ImplManager → VizAgent, retry loop + visualization, 3 agents)
- Layer 4: Paper Writing (PaperAgent → PaperManager, agentic section-by-section loop, 2 agents)
- Layer 5: Sensitivity Analysis (conditional pre-paper layer, 3 agents)
- Layer 5 enablement: runtime decision by Layer 1 (ProblemManager CONCLUDE → structured `sensitivity_enabled` call), governed by `sensitivity_mode` (auto/always/never). When enabled runs L3 → L5 → L4 so sensitivity results reach the paper (ADR-0001/0002). Requires Layer 3 outputs; skipped when Layer 3 is skipped.

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
- `mathmodelingagents/tools/web_search.py` — real web search (tavily/ddgs), Layer 1 pre-search injection + Layer 3 solver tool
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
- Sensitivity mode: `sensitivity_mode` = auto (default, governed by Layer 1) / always / never; legacy `enable_sensitivity` boolean and `selected_layers` containing 5 map to always with a deprecation warning (see `resolve_sensitivity_mode`)
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

## Agent skills

### Issue tracker
Issues are tracked as GitHub Issues in this repo (`https://github.com/J0Jng/MathModelingAgents`). See `docs/agents/issue-tracker.md` for the `gh` CLI conventions and wayfinding operations.

### Triage labels
This repo uses the five canonical triage labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md` for the mapping.

### Domain model
Core domain terminology lives in `CONTEXT.md` (the project glossary — Layer, Verdict, Sensitivity Decision, ...); the engineering skills' doc-consumption rules live in `docs/agents/domain.md`. Consult `CONTEXT.md` when:
- You need the definition of a domain term, or the mapping between layer names, agent names, and roles for config changes
- You're writing code that interacts with `AgentState`, `DebateState`, or the sandbox

Key reference: `CONTEXT.md` — glossary of domain terms; `docs/agents/domain.md` — how skills read `CONTEXT.md`/ADRs.

### Using skills
When working on this project, prefer these skill patterns (all under `.claude/skills/engineering/`):
- **Routing to the right skill**: `/ask-matt`
- **Planning a big chunk of work**: `/wayfinder` — a shared map of decision tickets, resolved one at a time
- **Turning a conversation into a spec / tickets**: `/to-spec` then `/to-tickets` — tracer-bullet tickets with blocking edges
- **Implementing features or fixing bugs**: `/implement` (drives `/tdd` at seams, closes with `/code-review`)
- **Test-driven development**: `/tdd` — red-green-refactor, write a failing test first
- **Diagnosing hard bugs**: `/diagnosing-bugs` — reproduce → minimise → hypothesise → instrument → fix → regression-test
- **Code review before committing**: `/code-review` — Standards + Spec axes, run as parallel sub-agents
- **Researching a topic**: `/research` — high-trust primary sources, cited Markdown file
- **Sharpening the domain model**: `/domain-modeling` (or `/grill-with-docs`) — challenge terms, update `CONTEXT.md` + ADRs
- **Designing deep modules**: `/codebase-design`
- **Triaging issues**: `/triage`
- **Resolving merge conflicts**: `/resolving-merge-conflicts`

### Architecture decisions
For significant design decisions affecting the 5-layer architecture or LangGraph topology:
- Document the decision in the domain model (`docs/agents/domain.md`)
- Use `/domain-modeling` if the change introduces new concepts or redefines existing ones
- Use `/codebase-design` when designing new module interfaces or deepening existing ones
