# 任务书：修复 flat-layout 多顶层包导致 editable install 失败

## 背景

新设备（setuptools >= 76）上 `pip install -e .` 报错：

```
error: Multiple top-level packages discovered in a flat-layout: ['cli', 'mathmodelingagents'].
```

原因：项目根目录有两个带 `__init__.py` 的顶层包（`mathmodelingagents/` 主包、`cli/` 交互菜单包），
但 `pyproject.toml` 没有 `[tool.setuptools]` 段，完全依赖 setuptools 自动发现。新版 setuptools
把「flat-layout 下多顶层包」从警告升级为硬错误，直接拒绝构建。

关键事实（已核实）：
- `main.py` 在项目根目录（非包内），`[project.scripts]` 声明 `mathmodeling = "main:main"`，
  故 `main` 必须显式声明为模块 py-modules。
- `cli/`（含 `model_picker.py`、`model_catalog.py`）与 `mathmodelingagents/` 是仅有的两个顶层包。
- `code/`、`scripts/`、`docs/`、`results/` 等目录无 `__init__.py`，不会被当作包。

## 改动清单（仅一处）

`pyproject.toml` 文件末尾（`[project.scripts]` 段之后）追加以下段，其余内容一字节不改：

```toml
[tool.setuptools]
py-modules = ["main"]

[tool.setuptools.packages.find]
include = ["mathmodelingagents*", "cli*"]
```

## 验收标准（用系统 python，`python --version` = 3.11.15，其 setuptools = 79.0.1）

逐条执行并报告**字面输出**（不许编造）：

1. TOML 语法合法：
   `python -c "import tomllib; tomllib.load(open('pyproject.toml','rb')); print('TOML OK')"`

2. 报错点验证（`get_requires_for_build_editable` 是本次报错发生处），执行后 returncode 必须为 0、
   stderr 不再含 `Multiple top-level packages`：
   `python -c "import subprocess,sys; r=subprocess.run([sys.executable,'-c','import setuptools.build_meta as bm; print(bm.get_requires_for_build_editable())'],cwd='.',capture_output=True,text=True); print('rc=',r.returncode); print('out=',r.stdout); print('err=',r.stderr)"`

3. 包发现只含两个顶层包及其子包（不得含 code/scripts/docs/results）：
   `python -c "from setuptools import find_packages; print(sorted(find_packages(include=['mathmodelingagents*','cli*'])))"`

## Do NOT 清单（严格）

- **只改 `pyproject.toml` 这一个文件**，其余任何文件（包括 `main.py`、`cli/*`、
  `mathmodelingagents/*`、`.env`、`README.md`）一律不动。
- **不得**运行 `pip install -e .` 或 `pip install` 任何包，**不得**创建/修改 `.venv` 或任何
  venv，**不得**修改 `uv.lock`/`poetry.lock`/`requirements*.txt`（本仓库也无这些文件）。
- **不得**写任何新的 brief/文档/脚本文件（本任务不需要输出新文件）。
- **不得**删改 `[project]`、`[project.scripts]`、`[build-system]` 等已有段的任何内容。
- 若验收命令因环境问题跑不起来，**报告失败原因即可，不得自行尝试装包或改环境来绕过**。