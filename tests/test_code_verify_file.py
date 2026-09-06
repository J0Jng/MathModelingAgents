"""_verify_layer3_code 路径 A 落盘执行回归测试。

Bug 背景：路径 A 曾用 `python -c` 执行 code/ 下的脚本，而 `-c` 模式下 Python
不注入内置变量 `__file__`，脚本里合法的
`os.path.dirname(os.path.abspath(__file__))` 必然抛 NameError，导致假报警失败。
修复：改为把 build_preamble()+源码落盘到 code_dir 内的临时 .py 再执行。
"""

from main import _verify_layer3_code


def test_verify_layer3_code_supports_dunder_file(tmp_path):
    """脚本使用 __file__ 解析目录时，验证应通过而非报 NameError。"""
    code_dir = tmp_path / "code"
    code_dir.mkdir()
    (code_dir / "solver.py").write_text(
        "import os\n"
        "base = os.path.dirname(os.path.abspath(__file__))\n"
        "print('base is', base)\n",
        encoding="utf-8",
    )

    report = _verify_layer3_code(str(tmp_path))

    assert "solver.py: ✅ 通过" in report
    assert "solver.py: ❌" not in report
    assert "NameError" not in report
    assert "__file__" not in report


def test_verify_layer3_code_tmpfile_cleaned_up(tmp_path):
    """验证用的 _verify_ 临时文件执行后应被删除，不残留到 code/ 目录。"""
    code_dir = tmp_path / "code"
    code_dir.mkdir()
    (code_dir / "solver.py").write_text("print('ok')\n", encoding="utf-8")

    _verify_layer3_code(str(tmp_path))

    leftovers = [p.name for p in code_dir.iterdir() if p.name != "solver.py"]
    assert leftovers == []


def test_verify_layer3_code_reports_real_failure(tmp_path):
    """真正有错的脚本仍应被标记为失败（修复不能吞掉真实失败）。"""
    code_dir = tmp_path / "code"
    code_dir.mkdir()
    (code_dir / "solver.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")

    report = _verify_layer3_code(str(tmp_path))

    assert "solver.py: ❌ 失败" in report
