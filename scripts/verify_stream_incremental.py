#!/usr/bin/env python
"""验证流式执行和增量写盘模块的完整性。

检查:
1. reporting.py 中的核心函数可导入和调用
2. setup_incremental 能创建输出目录
3. append_agent_output 能写入文件
4. finalize_reports 能汇总
"""

import sys
import os
import tempfile
sys.path.insert(0, '.')

from mathmodelingagents.reporting import (
    setup_incremental,
    append_agent_output,
    finalize_reports,
)

print("=" * 60)
print("流式写盘增量验证")
print("=" * 60)

# 使用临时目录
tmpdir = tempfile.mkdtemp(prefix="mma_stream_test_")

try:
    # 1. setup_incremental
    print("\n1. 测试 setup_incremental...")
    out_dir = setup_incremental(tmpdir)
    assert os.path.isdir(out_dir), f"目录未创建: {out_dir}"
    results_dir = os.path.join(out_dir, "results")
    code_dir = os.path.join(out_dir, "code")
    assert os.path.isdir(results_dir), f"results 子目录未创建"
    assert os.path.isdir(code_dir), f"code 子目录未创建"
    print(f"   ✓ 输出目录已创建: {out_dir}")

    # 2. append_agent_output (record dict API)
    print("\n2. 测试 append_agent_output...")
    test_records = [
        {"layer": "implementation", "agent": "solver_agent", "round_num": 1,
         "output": "## SolverAgent 输出\n\n这是求解结果。"},
        {"layer": "implementation", "agent": "viz_agent", "round_num": 1,
         "output": "## VizAgent 输出\n\n图表已生成。"},
        {"layer": "paper", "agent": "paper_agent", "round_num": 1,
         "output": "## PaperAgent 输出\n\n论文初稿。"},
    ]

    for record in test_records:
        append_agent_output(out_dir, record)
        fname = {"implementation": "Layer3_代码实现.md", "paper": "Layer4_论文写作.md"}
        expected_file = os.path.join(out_dir, fname.get(record["layer"], f"{record['layer']}.md"))
        assert os.path.isfile(expected_file), f"文件未创建: {expected_file}"
        with open(expected_file, 'r', encoding='utf-8') as f:
            written = f.read()
        assert record["output"] in written, f"内容不匹配: {record['agent']}"
        print(f"   ✓ {record['agent']}: 文件已写入")

    # 3. 追加内容（同 agent 同层第二轮）
    print("\n3. 测试追加写入...")
    record2 = {"layer": "implementation", "agent": "solver_agent", "round_num": 2,
               "output": "追加内容第二段。"}
    append_agent_output(out_dir, record2)
    impl_file = os.path.join(out_dir, "Layer3_代码实现.md")
    with open(impl_file, 'r', encoding='utf-8') as f:
        solver_content = f.read()
    assert "求解结果" in solver_content, "原始内容丢失"
    assert "追加内容第二段" in solver_content, "追加内容未写入"
    assert "第 2 轮" in solver_content, "轮次标题缺失"
    print(f"   ✓ 追加写入成功 ({len(solver_content)} chars)")

    # 4. finalize_reports
    print("\n4. 测试 finalize_reports...")
    state = {
        "layer_outputs": [
            {"layer": "implementation", "agent": "solver_agent", "output": "结果A"},
            {"layer": "implementation", "agent": "viz_agent", "output": "结果B"},
            {"layer": "paper", "agent": "paper_agent", "output": "结果C"},
        ]
    }
    written_files = finalize_reports(out_dir, state, "绿色物流配送")
    assert len(written_files) >= 2, f"应该生成至少2个文件, got {len(written_files)}"
    for fp in written_files:
        assert os.path.isfile(fp), f"文件不存在: {fp}"
        print(f"   ✓ {os.path.basename(fp)}")
    print(f"   ✓ 汇总报告已生成")

    # 5. 验证 modeling_graph 中使用 stream 模式
    print("\n5. 验证建模图 stream 模式...")
    from mathmodelingagents.graph.modeling_graph import MathModelingGraph

    import inspect
    propagate_source = inspect.getsource(MathModelingGraph.propagate)
    assert "stream(" in propagate_source or "self.graph.stream" in propagate_source, \
        "MathModelingGraph.propagate 未使用 stream 模式"
    print("   ✓ MathModelingGraph.propagate 使用 stream 模式")

    assert "setup_incremental" in propagate_source, \
        "MathModelingGraph.propagate 未调用 setup_incremental"
    assert "append_agent_output" in propagate_source, \
        "MathModelingGraph.propagate 未调用 append_agent_output"
    print("   ✓ MathModelingGraph.propagate 使用增量写盘")

    print(f"\n✅ 流式增量写盘全部验证通过")

finally:
    # 清理
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)
