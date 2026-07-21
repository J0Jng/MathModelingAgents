"""Test font auto-detection in sandbox via pytest."""
import pytest
from mathmodelingagents.tools import run_code


def test_font_detection_in_sandbox():
    """Verify sandbox auto-detects CJK fonts and reports status."""
    result = run_code("""
import matplotlib
import matplotlib.font_manager as fm

cjk = [f.name for f in fm.fontManager.ttflist
       if any(kw in f.name.lower() for kw in ["hei","song","ming","kai","cjk","noto","yahei","simsun","microsoft"])]
print(f"CJK_FONTS: {len(cjk)}")
""", timeout=20)

    assert result["success"], f"Sandbox failed: {result['stderr']}"
    stdout = result["stdout"]
    print("Sandbox stdout:", stdout[:500])

    # The preamble should have printed a font status line
    assert "[sandbox]" in stdout, "Preamble font detection message missing"
    assert ("中文字体已激活" in stdout or "未检测到中文字体" in stdout or "字体配置异常" in stdout), \
        f"Font status message not found in: {stdout[:300]}"
