# -*- coding: utf-8 -*-
"""T50/T50b 交互界面（Streamlit）AppTest 冒烟测试（窗口E）。

默认跳过（避免拖慢常规全量测试）；显式运行：

    CITYLOAD_APP_TEST=1 pytest tests/test_app_t50.py -v

覆盖：初始渲染（默认中文）→ 组件齐备 → 点击运行 → 指标卡/对比表/图件渲染；
以及 T50b 双语：侧边栏切换 English → 骨架文案为英文 → 点击运行仍正常。
注意：本测试会真实执行完整管线（中文/英文各一次完整点击，约 40～60 秒）。
"""

import os
from pathlib import Path

import pytest

if os.environ.get("CITYLOAD_APP_TEST", "").strip().lower() not in {"1", "true", "yes", "on"}:
    pytest.skip("设置 CITYLOAD_APP_TEST=1 后执行交互界面冒烟", allow_module_level=True)

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def _run_flow(seed_key: str | None = None, lang: str | None = None):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception, at.exception

    if lang == "en":
        at.sidebar.radio[0].set_value("English")
        at.run()
        assert not at.exception, at.exception

    if seed_key:
        at.selectbox[0].select(seed_key)
        at.run()

    return at


def _click_and_check(at, expect_metric_label: str):
    at.button[0].click()
    at.run()
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    assert len(at.metric) == 5
    assert expect_metric_label in [m.label for m in at.metric]
    return at


def test_app_seed42_flow_zh():
    at = _run_flow()
    assert at.selectbox[0].label == "选择数据集"
    assert at.button[0].label == "运行智能配载"
    _click_and_check(at, "车辆数")
    assert len(at.image) >= 7                         # 路线图 + 各车厢 3D 图
    assert any("对比" in s.value for s in at.subheader)
    assert any("路线图" in s.value for s in at.subheader)


def test_app_seed7_flow_zh():
    at = _run_flow(seed_key="seed7")
    _click_and_check(at, "货物装下率")
    assert len(at.image) >= 6


def test_app_english_mode():
    """T50b：切换 English 后骨架为英文，运行流程与此前一致（复用缓存时亦不报错）。"""
    at = _run_flow(lang="en")
    assert at.button[0].label == "Run Loading Optimization"
    assert at.selectbox[0].label == "Select dataset"
    _click_and_check(at, "Vehicles")
    assert len(at.image) >= 7
    assert any("Baseline" in s.value or "vs" in s.value for s in at.subheader)
    assert any("Route" in s.value for s in at.subheader)


def _clear_llm_env(monkeypatch):
    for var in ("CITYLOAD_LLM_API_KEY", "CITYLOAD_LLM_BASE_URL", "CITYLOAD_LLM_MODEL",
                "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_app_llm_panel_no_key_flow(monkeypatch):
    """T50c 红线：无 Key 时全流程正常——面板可见（Key 为 password 输入框）、
    自然语言解析走规则引擎、界面不报错。"""
    _clear_llm_env(monkeypatch)
    at = _run_flow()

    inputs = list(at.get("text_input"))
    key_inputs = [x for x in inputs if x.label == "API Key"]
    assert key_inputs, "侧边栏缺少 API Key 输入框"
    assert key_inputs[0].proto.type == 1, "API Key 输入框应为 password 打码类型"
    assert any("未启用 LLM" in i.value for i in at.info), "无 Key 时应提示未启用"

    example_btn = [b for b in at.button if b.key == "nl_example_0"][0]
    example_btn.click()
    at.run()
    assert not at.exception and not at.error
    assert any("解析来源：规则引擎" in i.value for i in at.info), "无 Key 应走规则引擎来源"
    assert len(at.dataframe) >= 2, "应展示结构化结果（概览 + items）"


def test_app_llm_key_injection_cleanup_and_fallback(monkeypatch):
    """T50c：填 Key（指向不可达地址）→ 优雅降级到规则引擎；运行时注入的环境变量已清理。"""
    _clear_llm_env(monkeypatch)
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    inputs = {x.label: x for x in at.get("text_input")}
    inputs["API Key"].set_value("sk-dummy-test-000")
    inputs["Base URL"].set_value("http://127.0.0.1:9")
    at.run()
    assert any("已启用 LLM" in s.value for s in at.success)

    [b for b in at.button if b.key == "nl_example_0"][0].click()
    at.run()
    assert not at.exception and not at.error
    assert any("已自动降级" in i.value or "解析来源：规则引擎" in i.value for i in at.info)
    for var in ("CITYLOAD_LLM_API_KEY", "CITYLOAD_LLM_BASE_URL", "CITYLOAD_LLM_MODEL"):
        assert not os.environ.get(var), f"注入的环境变量 {var} 未被清理"
