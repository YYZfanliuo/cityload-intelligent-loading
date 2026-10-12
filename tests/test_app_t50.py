# -*- coding: utf-8 -*-
"""T50/T50b 交互界面（Streamlit）AppTest 冒烟测试（窗口E）。

默认跳过（避免拖慢常规全量测试）；显式运行：

    CITYLOAD_APP_TEST=1 pytest tests/test_app_t50.py -v

覆盖：初始渲染（默认中文）→ 组件齐备 → 点击运行 → 指标卡/对比表/图件渲染；
T50b 双语：侧边栏切换 English → 骨架文案为英文 → 点击运行仍正常；
T50c LLM 面板（无 Key 红线 / Key 注入与清理）；T50d/T50e/T50f NL 串联流程；
T50g NL 结果区补图（3D 装载图 + 分步图，目标车辆优先）；
T50h 多行上限 50 / 分步图 8 张 / 按车分组展示 / ④LLM 面板（测试连接 + 获取模型列表）。
注意：本测试会真实执行完整管线（各用例数十秒量级）。
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
    inputs["接口地址（Base URL）"].set_value("http://127.0.0.1:9")
    at.run()
    assert any("已启用 LLM" in s.value for s in at.success)

    [b for b in at.button if b.key == "nl_example_0"][0].click()
    at.run()
    assert not at.exception and not at.error
    assert any("已自动降级" in i.value or "解析来源：规则引擎" in i.value for i in at.info)
    for var in ("CITYLOAD_LLM_API_KEY", "CITYLOAD_LLM_BASE_URL", "CITYLOAD_LLM_MODEL"):
        assert not os.environ.get(var), f"注入的环境变量 {var} 未被清理"


def test_app_t50d_merge_happy_path(monkeypatch):
    """T50d：一句话 → 解析 → 并入场景 → 重跑配载 → 该单分到车次 + 新指标 + 路线图。"""
    _clear_llm_env(monkeypatch)
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    # 示例 1（连锁药店07）→ 解析
    [b for b in at.button if b.key == "nl_example_0"][0].click()
    at.run()
    assert not at.exception and not at.error
    # 串联按钮可用 → 点击 → 重跑配载
    merge_btns = [b for b in at.button if b.key == "merge_run"]
    assert merge_btns and not merge_btns[0].disabled, "解析成功后串联按钮应可用"
    merge_btns[0].click()
    at.run()
    assert not at.exception and not at.error
    # 车次分配 + 新方案指标 + 路线图
    success_text = " ".join(s.value for s in at.success)
    assert "并入" in success_text and "R0" in success_text, f"应展示车次分配：{success_text[:120]}"
    metric_labels = [m.label for m in at.metric]
    assert "车辆数" in metric_labels and "总里程" in metric_labels
    assert len(at.image) >= 1, "应展示合并后路线图"


def test_app_t50d_store_not_found(monkeypatch):
    """T50d 边界：门店不在当前配送网络 → 明确提示，不崩溃、不编造坐标。"""
    _clear_llm_env(monkeypatch)
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    at.get("text_area")[0].set_value("给幸福大药房送 2 箱板蓝根")
    at.run()
    [b for b in at.button if b.key == "nl_parse"][0].click()
    at.run()
    merge_btns = [b for b in at.button if b.key == "merge_run"]
    assert merge_btns and not merge_btns[0].disabled
    merge_btns[0].click()
    at.run()
    assert not at.exception and not at.error
    assert any("不在当前配送网络" in w.value for w in at.warning), "应提示门店不在网络内"


def test_app_t50d_parse_failed_no_merge_button(monkeypatch):
    """T50d 边界：解析失败 → 显示错误提示，且不出现可点击的串联按钮。"""
    _clear_llm_env(monkeypatch)
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    at.get("text_area")[0].set_value("你好")
    at.run()
    [b for b in at.button if b.key == "nl_parse"][0].click()
    at.run()
    assert not at.exception, at.exception
    assert [e for e in at.error], "解析失败应显示错误提示"
    merge_btns = [b for b in at.button if b.key == "merge_run"]
    assert (not merge_btns) or merge_btns[0].disabled, "解析失败时串联按钮应不可用"


@pytest.fixture()
def clean_catalog_ext():
    """备份并恢复扩展档案目录（保证 T50e 测试的档案状态隔离）。"""
    p = Path(APP).parent / "outputs" / "app-tmp" / "catalog-ext"
    backup = {f.name: f.read_text(encoding="utf-8") for f in p.glob("*.json")} if p.exists() else {}
    yield
    if p.exists():
        for f in p.glob("*.json"):
            f.unlink()
        for name, text in backup.items():
            (p / name).write_text(text, encoding="utf-8")


def test_app_t50e_archive_to_order_flow(monkeypatch, clean_catalog_ext):
    """T50e 全链路：新建门店档案 → 一句话下单 → 匹配扩展档案 → 并入场景 → 车次分配。"""
    _clear_llm_env(monkeypatch)
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=300)
    at.run()

    # 1) 新增门店档案（默认经纬度即可）
    name_input = [x for x in at.get("text_input") if x.key == "cat_store_name"][0]
    name_input.set_value("新康药房")
    at.run()
    [b for b in at.button if b.key == "cat_store_save"][0].click()
    at.run()
    assert not at.exception and not at.error
    assert any("已保存" in s.value and "新康药房" in s.value for s in at.success), \
        [s.value for s in at.success]
    # 档案文件已写入（C01）
    stores_file = Path(APP).parent / "outputs" / "app-tmp" / "catalog-ext" / "stores.json"
    assert stores_file.exists()
    assert "新康药房" in stores_file.read_text(encoding="utf-8")

    # 2) 用新门店一句话下单 → 解析
    at.get("text_area")[0].set_value("明天上午给新康药房送 4 箱感冒灵，12 点前到")
    at.run()
    [b for b in at.button if b.key == "nl_parse"][0].click()
    at.run()
    assert not at.exception and not at.error

    # 3) 并入场景 → 重跑配载 → 车次分配（扩展门店含坐标，可参与配载）
    merge_btns = [b for b in at.button if b.key == "merge_run"]
    assert merge_btns and not merge_btns[0].disabled
    merge_btns[0].click()
    at.run()
    assert not at.exception and not at.error
    success_text = " ".join(s.value for s in at.success)
    assert "新康药房" in success_text and "C01" in success_text, success_text[:160]
    assert "R0" in success_text, "应展示车次分配"
    assert len(at.image) >= 1


def test_app_t50f_multi_scene_flow(monkeypatch):
    """T50f：多单独立场景 —— 填入内置示例（6 单）→ 构建 → 方案 + 车次分配 + 路线图。"""
    _clear_llm_env(monkeypatch)
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    [b for b in at.button if b.key == "multi_fill"][0].click()
    at.run()
    assert not at.exception
    multi_area = [t for t in at.get("text_area") if t.key == "multi_text"][0]
    assert len((multi_area.value or "").splitlines()) == 6, "内置示例应为 6 行"

    [b for b in at.button if b.key == "multi_build"][0].click()
    at.run()
    assert not at.exception and not at.error
    assert any("独立场景已生成" in s.value for s in at.success), [s.value for s in at.success]
    assert len(at.image) >= 1, "应展示独立场景路线图"


def test_app_t50f_merge_note_and_compare(monkeypatch):
    """T50f：插单说明行 + 方案对比行（数据来自重跑前后两个 metrics，不硬编码）。"""
    _clear_llm_env(monkeypatch)
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    [b for b in at.button if b.key == "nl_example_0"][0].click()
    at.run()
    # 说明行（解析成功后按钮下方可见）
    captions = [c.value for c in at.caption]
    assert any("并入当前数据集的 30 家门店场景" in c for c in captions), "应显示插单模式说明行"
    # 执行合并 → 对比行
    [b for b in at.button if b.key == "merge_run"][0].click()
    at.run()
    assert not at.exception and not at.error
    infos = [i.value for i in at.info]
    assert any("方案对比" in v and "原方案" in v for v in infos), infos


def test_app_t50g_merge_shows_cargo_figures(monkeypatch):
    """T50g：插单结果区补展示 3D 装载图 + 分步图，且目标车辆图优先排列。"""
    import re

    _clear_llm_env(monkeypatch)
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    [b for b in at.button if b.key == "nl_example_0"][0].click()
    at.run()
    [b for b in at.button if b.key == "merge_run"][0].click()
    at.run()
    assert not at.exception and not at.error

    captions = [c for img in at.image for c in (img.captions or [])]
    pack_caps = [c for c in captions if "车厢 3D 装载图" in c]
    step_caps = [c for c in captions if "分步示意" in c]
    assert pack_caps, "插单结果区应展示车厢 3D 装载图"
    assert step_caps, "插单结果区应展示分步装载图"

    # 目标车辆（success 消息中的车次号）的装载图应排在最前
    success_text = " ".join(s.value for s in at.success)
    route_ids = re.findall(r"R\d{2}", success_text)
    assert route_ids, f"success 消息应含车次号：{success_text[:120]}"
    assert pack_caps[0].startswith(route_ids[0]), \
        f"目标车辆 {route_ids[0]} 的装载图应排最前：{pack_caps[:3]}"


def test_app_t50g_multi_scene_shows_cargo_figures(monkeypatch):
    """T50g：多单独立场景结果区补展示 3D 装载图 + 分步图。"""
    _clear_llm_env(monkeypatch)
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    [b for b in at.button if b.key == "multi_fill"][0].click()
    at.run()
    [b for b in at.button if b.key == "multi_build"][0].click()
    at.run()
    assert not at.exception and not at.error

    captions = [c for img in at.image for c in (img.captions or [])]
    assert [c for c in captions if "车厢 3D 装载图" in c], "多单场景应展示车厢 3D 装载图"
    assert [c for c in captions if "分步示意" in c], "多单场景应展示分步装载图"


def test_app_t50h_multi_line_limit(monkeypatch):
    """T50h：多行上限 12→50——常量=50，51 行被拒绝（快速路径，不触发管线）。"""
    _clear_llm_env(monkeypatch)
    import app

    assert app.MAX_MULTI_LINES == 50
    lines = "\n".join(["明天上午给连锁药店03送 4 箱感冒灵"] * 51)
    r = app._build_independent_scene(lines, "seed42")
    assert r["error"] == "too_many" and r["count"] == 51


def test_app_t50h_multi_13_lines_flow(monkeypatch):
    """T50h：13 行（旧上限 12 的溢出样例）现在可正常构建独立场景。"""
    _clear_llm_env(monkeypatch)
    import app

    lines = "\n".join(f"明天上午给连锁药店{6 + i % 6:02d}送 1 箱感冒灵" for i in range(13))
    r = app._build_independent_scene(lines, "seed42")
    assert r["error"] is None, r
    assert r["orders"], "应至少解析出一单"


def test_app_t50h_vehicle_grouping(monkeypatch):
    """T50h：装载图按车分组——主流程分组标题形如“车 1 · R01-1（V01）”，组内含 3D 总图 + 分步图。"""
    _clear_llm_env(monkeypatch)
    at = _run_flow()
    _click_and_check(at, "车辆数")
    labels = [e.label for e in at.get("expander")]
    assert any(l.startswith("车 1 · R01-1") for l in labels), labels[:10]
    captions = [c for img in at.image for c in (img.captions or [])]
    assert [c for c in captions if "车厢 3D 装载图" in c], "组内应含 3D 总图"
    assert [c for c in captions if "分步示意" in c], "组内应含分步图"


# ---------------------------------------------------------------- T50h ④ LLM 面板


def _start_models_mock(mode: dict | None = None):
    """启动本地 mock /models 服务（校验 Bearer 头），返回 (base_url, stop_fn)。"""
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    state = mode if mode is not None else {"m": "ok"}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if not self.headers.get("Authorization", "").startswith("Bearer "):
                self.send_response(401)
                self.end_headers()
                return
            if state["m"] == "404":
                self.send_response(404)
                self.end_headers()
                return
            body = _json.dumps({"data": [{"id": "deepseek-chat"},
                                         {"id": "deepseek-reasoner"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # 静音
            pass

    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def _stop():
        srv.shutdown()
        srv.server_close()

    return f"http://127.0.0.1:{srv.server_port}", _stop


def test_app_t50h4_fetch_models_branches(monkeypatch):
    """T50h④：_fetch_models 单元分支——无 Key / 成功（含 Bearer 校验）/ 404 不支持。"""
    _clear_llm_env(monkeypatch)
    import app

    assert app._fetch_models("http://127.0.0.1:9", "")["reason"] == "no_key"
    mode = {"m": "ok"}
    base, stop = _start_models_mock(mode)
    try:
        ok = app._fetch_models(base, "sk-mock-unit")
        assert ok["ok"] and ok["models"] == ["deepseek-chat", "deepseek-reasoner"]
        mode["m"] = "404"
        r404 = app._fetch_models(base, "sk-mock-unit")
        assert r404["reason"] == "unsupported"
    finally:
        stop()


def test_app_t50h4_llm_panel_test_and_fetch(monkeypatch):
    """T50h④：面板单层三字段两按钮 → 无 Key 提示 → mock 连接成功 + 模型下拉 + 回填。"""
    _clear_llm_env(monkeypatch)
    from streamlit.testing.v1 import AppTest

    base, stop = _start_models_mock()
    try:
        at = AppTest.from_file(APP, default_timeout=120)
        at.run()
        assert not at.exception, at.exception

        labels = [x.label for x in at.get("text_input")]
        for expect in ("接口地址（Base URL）", "API Key", "模型名称"):
            assert expect in labels, f"缺少字段 {expect}"
        keys = {b.key for b in at.button}
        assert {"llm_test_btn", "llm_fetch_btn"} <= keys, "缺少测试连接/获取模型列表按钮"
        assert not any("高级选项" in e.label for e in at.get("expander")), "已合并为单层组织"

        # 无 Key 点击「测试连接」→ 明确提示（不崩溃）
        [b for b in at.button if b.key == "llm_test_btn"][0].click()
        at.run()
        assert any("请先填写 API Key" in w.value for w in at.warning), \
            [w.value for w in at.warning]

        # 填入 mock 配置 → 获取模型列表
        inputs = {x.label: x for x in at.get("text_input")}
        inputs["接口地址（Base URL）"].set_value(base)
        inputs["API Key"].set_value("sk-mock-ui")
        at.run()
        [b for b in at.button if b.key == "llm_fetch_btn"][0].click()
        at.run()
        assert any("连接成功" in s.value and "2 个模型" in s.value for s in at.success), \
            [s.value for s in at.success]
        sel = [s for s in at.selectbox if s.label.startswith("可用模型")]
        assert sel and list(sel[0].options) == ["deepseek-chat", "deepseek-reasoner"]

        # 选择模型 → 自动回填到「模型名称」输入框
        sel[0].select("deepseek-reasoner")
        at.run()
        assert any(x.label == "模型名称" and x.value == "deepseek-reasoner"
                   for x in at.get("text_input")), "选择后应回填模型名称"
    finally:
        stop()
