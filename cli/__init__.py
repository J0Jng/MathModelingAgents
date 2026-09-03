"""MathModelingAgents CLI 交互辅助包。

仅含纯数据与交互选择逻辑，由 main.py 在 propagate() 之前调用；
不 import 任何重型依赖的副作用（questionary 等在使用处局部 import）。
"""
