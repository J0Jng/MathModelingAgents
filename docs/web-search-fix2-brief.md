# 修复任务书：verify_web_search.py 运行路径问题（第 3 轮）

## 问题

`python scripts/verify_web_search.py` 直接运行时报 `ModuleNotFoundError: No module named 'mathmodelingagents'`。
原因：直接运行脚本时 `sys.path[0]` 是 `scripts/` 目录，不包含项目根。

## 修复（唯一改动）

在 `scripts/verify_web_search.py` 中，`from mathmodelingagents.tools.web_search import ...`（第 12 行）**之前**，插入：

```python
# 确保项目根在 sys.path 中（直接运行 scripts/*.py 时）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

（`os` 和 `sys` 已在第 9-10 行导入，无需新增 import。）

## 验收

```bash
python scripts/verify_web_search.py
```

- 必须正常输出"生效 provider: ddgs" + 真实搜索结果
- 退出码 0

只改这一处，不要动其他任何文件。
