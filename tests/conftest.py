# -*- coding: utf-8 -*-
"""tests/conftest.py —— 把项目根加入 sys.path，保证 `import eval.xxx` 可用。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
