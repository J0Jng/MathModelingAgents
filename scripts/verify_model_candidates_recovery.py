# -*- coding: utf-8 -*-
"""验收脚本：modeler 创新强化 + 候选池落盘/回填（impl-brief Acceptance Criteria 1-5）。

用法（仓库根目录）:
    export PYTHONPATH=
    .venv/Scripts/python.exe scripts/verify_model_candidates_recovery.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

PASS = "✅ PASS"
FAIL = "❌ FAIL"
results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    results.append((ok, label))
    print(f"{'PASS' if ok else 'FAIL'}  {label}")


# ── 1. 语法检查：三个被改文件 py_compile ──
files = [
    REPO_ROOT / "mathmodelingagents/agents/utils/prompt_templates.py",
    REPO_ROOT / "mathmodelingagents/agents/__init__.py",
    REPO_ROOT / "mathmodelingagents/graph/recovery.py",
]
compile_ok = True
for f in files:
    r = subprocess.run(
        [sys.executable, "-m", "py_compile", str(f)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        compile_ok = False
        print(r.stderr)
check(compile_ok, "1. py_compile 三个被改文件无错误")

# ── 2. import 冒烟 ──
r = subprocess.run(
    [sys.executable, "-c",
     "import mathmodelingagents.agents, mathmodelingagents.graph.recovery"],
    capture_output=True, text=True, cwd=str(REPO_ROOT),
)
check(r.returncode == 0, f"2. import 冒烟成功{'' if r.returncode == 0 else ': ' + r.stderr}")

# ── 3. prompt 授权验证 ──
from mathmodelingagents.agents.utils.prompt_templates import (
    get_modeler_a_prompt, get_modeler_b_prompt, get_modeler_c_prompt,
)

pa, pb, pc = get_modeler_a_prompt(), get_modeler_b_prompt(), get_modeler_c_prompt()
check(all("model_search" in p for p in (pa, pb, pc)),
      "3a. modeler_a/b/c 工具权限行均含 model_search")
check("前沿方向" in pa and "model_search_tool 检索候选模型知识库" in pa,
      "3b. modeler_a prompt 含「前沿方向」与「model_search_tool 检索候选模型知识库」")

# ── 4. 落盘验证 ──
from mathmodelingagents.agents import CandidatePoolResult, _persist_model_candidates

tmp = Path(tempfile.mkdtemp(prefix="verify_mc_"))
try:
    pool = CandidatePoolResult(source="rag", text="## 候选模型池 Top-5\n- 模型X", query="优化 预测")
    _persist_model_candidates({"output_dir": str(tmp)}, pool)
    out = tmp / "model_candidates.json"
    ok4 = out.exists()
    if ok4:
        data = json.loads(out.read_text(encoding="utf-8"))
        ok4 = all(k in data for k in ("text", "source", "query"))
        ok4 = ok4 and data["text"] == pool.text and data["source"] == "rag" and data["query"] == pool.query
    check(ok4, "4. _persist_model_candidates 落盘且含 text/source/query 三键")

    # 4b. 空 text 不落盘（source="empty" 语义）
    tmp2 = Path(tempfile.mkdtemp(prefix="verify_mc_empty_"))
    try:
        _persist_model_candidates({"output_dir": str(tmp2)},
                                  CandidatePoolResult(source="empty", text="", query=""))
        check(not (tmp2 / "model_candidates.json").exists(), "4b. 空 text（source=empty）不落盘")
    finally:
        import shutil
        shutil.rmtree(tmp2, ignore_errors=True)

    # ── 5. 回填验证 ──
    from mathmodelingagents.graph.recovery import load_layer1_state

    layer1 = tmp / "Layer1_问题分析.md"
    layer1.write_text(
        "# Layer 1 输出\n\n### 问题分析经理 [裁决]\n综合问题分析正文（裁决内容）。\n",
        encoding="utf-8",
    )
    state = load_layer1_state(str(tmp))
    check(bool(state.get("model_candidates")) and state["model_candidates"] == pool.text,
          "5a. load_layer1_state 回填 model_candidates 且等于 json 里的 text")

    (tmp / "model_candidates.json").unlink()
    state2 = load_layer1_state(str(tmp))
    check("model_candidates" not in state2, "5b. 删除 json 后回填不含 model_candidates 键")
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

# ── 汇总 ──
failed = [label for ok, label in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} 项通过")
if failed:
    sys.exit(1)
print("ALL CHECKS PASSED")
