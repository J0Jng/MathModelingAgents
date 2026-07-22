"""
Comprehensive tests for Layer 3 (SolverAgent + VizAgent) and Layer 4 (PaperAgent) redesign.
Tests mock LLM calls to verify tool-calling loop logic, graph topology, and prompt quality.
"""
import json
from unittest.mock import MagicMock

from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage, AIMessage

from mathmodelingagents.agents import create_solver_agent, create_viz_agent, create_impl_manager
from mathmodelingagents.agents import create_paper_agent, create_paper_manager
from mathmodelingagents.agents.utils.prompt_templates import (
    get_solver_agent_prompt, get_viz_agent_prompt, get_impl_manager_prompt,
    get_paper_agent_prompt, get_paper_manager_prompt,
)
from mathmodelingagents.tools import create_coding_agent_tools, create_paper_agent_tools
from mathmodelingagents.graph.setup import GraphSetup
from mathmodelingagents.default_config import DEFAULT_CONFIG


def test_solver_agent_factory():
    """Test SolverAgent factory creates the right structure."""
    config = DEFAULT_CONFIG.copy()
    config['output_dir'] = '/tmp/test_mma_output'
    config['layer_timeouts'] = {'implementation': 10800}

    node = create_solver_agent(config)
    assert node.__name__ == 'solver_agent'

    tools = create_coding_agent_tools(config['output_dir'])
    names = [t.name for t in tools]
    assert 'run_code_tool' in names
    assert 'read_file_tool' in names
    assert 'write_file_tool' in names
    assert 'list_dir_tool' in names


def test_viz_agent_factory():
    """Test VizAgent factory creates the right structure."""
    config = DEFAULT_CONFIG.copy()
    config['output_dir'] = '/tmp/test_mma_output'
    config['layer_timeouts'] = {'implementation': 10800}

    node = create_viz_agent(config)
    assert node.__name__ == 'viz_agent'


def test_paper_agent_factory():
    """Test PaperAgent factory — no run_code."""
    config = DEFAULT_CONFIG.copy()
    config['output_dir'] = '/tmp/test_mma_output'

    node = create_paper_agent(config)
    assert node.__name__ == 'paper_agent'

    tools = create_paper_agent_tools(config['output_dir'])
    names = [t.name for t in tools]
    assert 'run_code_tool' not in names, "PaperAgent should NOT have run_code!"
    assert 'read_file_tool' in names
    assert 'list_dir_tool' in names
    assert 'write_file_tool' in names


def test_tool_calling_loop():
    """Mock the tool-calling loop: tool calls → text → SELF_CHECK_PASSED."""
    mock_llm = MagicMock()
    call_sequence = [
        AIMessage(content='', tool_calls=[{
            'name': 'read_file_tool', 'args': {'path': '/tmp/test'}, 'id': 'call_1',
        }]),
        AIMessage(content='', tool_calls=[{
            'name': 'list_dir_tool', 'args': {'path': '../results'}, 'id': 'call_2',
        }]),
        AIMessage(content='', tool_calls=[{
            'name': 'write_file_tool', 'args': {'content': 'draft', 'path': 'p.md'}, 'id': 'call_3',
        }]),
        AIMessage(content='## SELF_CHECK_PASSED\n\n## Final Output\n...', tool_calls=[]),
    ]
    mock_llm.invoke.side_effect = call_sequence

    messages = [SystemMessage(content='test'), HumanMessage(content='test')]
    consecutive_no_tool = 0

    for i in range(30):
        response = mock_llm.invoke(messages)
        messages.append(response)

        if response.tool_calls:
            consecutive_no_tool = 0
            for tc in response.tool_calls:
                result = f'[tool result for {tc["name"]}]'
                messages.append(ToolMessage(content=result, tool_call_id=tc['id']))
        else:
            consecutive_no_tool += 1
            content = response.content or ''
            if 'SELF_CHECK_PASSED' in content:
                break

    # 2 initial + 4 AI + 3 tool = 9 messages
    assert len(messages) == 9, f"Expected 9 messages, got {len(messages)}"
    assert 'SELF_CHECK_PASSED' in messages[-1].content


def test_safety_break():
    """3 consecutive no-tool calls → forced break."""
    mock_llm = MagicMock()
    stuck = [AIMessage(content=f'Thinking {i}...', tool_calls=[]) for i in range(5)]
    mock_llm.invoke.side_effect = stuck

    messages = [SystemMessage(content='test'), HumanMessage(content='test')]
    consecutive = 0

    for i in range(30):
        response = mock_llm.invoke(messages)
        messages.append(response)
        if response.tool_calls:
            consecutive = 0
        else:
            consecutive += 1
            if consecutive >= 3:
                break

    assert consecutive == 3, f"Expected 3 consecutive, got {consecutive}"


def test_graph_topology():
    """Verify new nodes present and old nodes removed from graph."""
    gs = GraphSetup(DEFAULT_CONFIG)
    g = gs.setup_graph().compile()
    mermaid = g.get_graph().draw_mermaid()

    # New nodes present
    for node in ['solver_agent', 'viz_agent', 'impl_manager', 'paper_agent', 'paper_manager']:
        assert node in mermaid, f"Missing node: {node}"

    # Old nodes removed
    old_nodes = ['coding_agent', 'algorithm_designer', 'coder', 'visualizer',
                 'paper_architect', 'section_writer', 'chart_designer']
    for old in old_nodes:
        assert old not in mermaid, f"Stale node: {old}"


def test_prompts():
    """Verify prompt content quality."""
    sa = get_solver_agent_prompt()
    va = get_viz_agent_prompt()
    im = get_impl_manager_prompt()
    pa = get_paper_agent_prompt()
    pm = get_paper_manager_prompt()

    # SolverAgent
    assert 'run_code' in sa
    assert 'SELF_CHECK_PASSED' in sa
    assert 'results.json' in sa
    # 不再包含图表生成相关内容
    assert 'plt.savefig' not in sa, "SolverAgent should NOT include chart generation"

    # VizAgent
    assert 'run_code' in va
    assert 'SELF_CHECK_PASSED' in va
    assert 'plt.savefig' in va
    assert 'results.json' in va
    assert '15' in va  # max_iterations for VizAgent

    # ImplManager
    assert 'CONCLUDE' in im
    assert 'RETRY' in im
    assert ('SolverAgent' in im) or ('CodingAgent' in im)

    # PaperAgent
    assert ('分节' in pa) or ('逐节' in pa), "Missing section-by-section"
    assert 'read_file' in pa
    assert 'SELF_CHECK_PASSED' in pa

    # PaperManager
    assert '无工具' in pm or '纯审查' in pm, "Should be no-tool"
    assert 'REVISE' in pm
    assert 'PaperAgent' in pm


def test_impl_manager_factory():
    """Test ImplManager factory still works."""
    config = DEFAULT_CONFIG.copy()
    node = create_impl_manager(config)
    assert node.__name__ == 'impl_manager'


def test_paper_manager_factory():
    """Test PaperManager factory still works."""
    config = DEFAULT_CONFIG.copy()
    node = create_paper_manager(config)
    assert node.__name__ == 'paper_manager'


def test_feedback_channel():
    """Test that paper_feedback state field exists."""
    from mathmodelingagents.agents.utils.agent_states import AgentState
    assert 'paper_feedback' in AgentState.__annotations__


def test_graph_entry_points():
    """Test --start-layer entry points are correct."""
    gs = GraphSetup(DEFAULT_CONFIG)
    assert gs.selected_layers == [1, 2, 3, 4]


if __name__ == '__main__':
    tests = [
        test_solver_agent_factory,
        test_viz_agent_factory,
        test_paper_agent_factory,
        test_tool_calling_loop,
        test_safety_break,
        test_graph_topology,
        test_prompts,
        test_impl_manager_factory,
        test_paper_manager_factory,
        test_feedback_channel,
        test_graph_entry_points,
    ]
    passed = 0
    for test in tests:
        try:
            test()
            print(f"  ✓ {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {test.__name__}: {e}")

    print(f"\n{passed}/{len(tests)} tests passed")
