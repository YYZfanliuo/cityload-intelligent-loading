# -*- coding: utf-8 -*-
"""T50 交互界面（Streamlit）AppTest 冒烟测试（窗口E）。

默认跳过（避免拖慢常规全量测试）；显式运行：

    CITYLOAD_APP_TEST=1 pytest tests/test_app_t50.py -v

覆盖：初始渲染 → 组件齐备 → 点击「运行智能配载」→ 指标卡/对比表/图件渲染 → 无异常。
注意：本测试会真实执行完整管线（每个用例约 20～30 秒）。
"""

import os
from pathlib import Path

import pytest

if os.environ.get("CITYLOAD_APP_TEST", "").strip().lower() not in {"1", "true", "yes", "on"}:
    pytest.skip("设置 CITYLOAD_APP_TEST=1 后执行交互界面冒烟", allow_module_level=True)

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def _run_flow(seed_label: str | None = None):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    assert at.selectbox[0].label == "选择数据集"
    assert at.button[0].label == "运行智能配载"

    if seed_label:
        at.selectbox[0].select(seed_label)
        at.run()

    at.button[0].click()
    at.run()
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    return at


def test_app_seed42_flow():
    at = _run_flow()
    assert len(at.metric) == 5                       # 指标卡：车辆/里程/运输费/装下率/时效
    assert len(at.image) >= 7                        # 路线图 + 各车厢 3D 图
    assert any("对比" in s.value for s in at.subheader)  # 对比表区块
    assert any("路线图" in s.value for s in at.subheader)


def test_app_seed7_flow():
    at = _run_flow(seed_label="seed7（第二组验证 · 30 门店）")
    assert len(at.metric) == 5
    assert len(at.image) >= 6                        # 路线图 + 各车厢 3D 图
