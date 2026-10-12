# -*- coding: utf-8 -*-
"""T23 单测 —— report/render.py（窗口E）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mock_data import mini_bundle  # noqa: E402
from report.render import render_figures  # noqa: E402

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _assert_png(path: str) -> None:
    assert os.path.isfile(path), f"图件不存在：{path}"
    assert os.path.getsize(path) > 1000, f"图件过小（疑似损坏）：{path}"
    with open(path, "rb") as fh:
        assert fh.read(8) == PNG_MAGIC, f"非 PNG 文件：{path}"


def test_render_figures_contract(tmp_path):
    """契约 §2.8：返回路径列表；命名与数量符合约定。"""
    bundle = mini_bundle()
    out = render_figures(bundle["routes"], bundle["packing"], bundle["loading_plan"],
                         bundle["network"], str(tmp_path / "figures"))
    names = [os.path.basename(p) for p in out]
    assert "route_map.png" in names
    assert "packing_V01.png" in names and "packing_V02.png" in names
    assert "step_V01_1.png" in names and "step_V02_1.png" in names
    for p in out:
        _assert_png(p)


def test_step_shot_count_bounded(tmp_path):
    """每车分步图不超过 8 张（T50h 上限），且与实际装载步骤数一致（mock：R01 两步 / R02 一步）。"""
    bundle = mini_bundle()
    out = render_figures(bundle["routes"], bundle["packing"], bundle["loading_plan"],
                         bundle["network"], str(tmp_path))
    names = [os.path.basename(p) for p in out]
    v01 = [n for n in names if n.startswith("step_V01_")]
    v02 = [n for n in names if n.startswith("step_V02_")]
    assert len(v01) == 2 and len(v02) == 1
    assert len(v01) <= 8 and len(v02) <= 8


def test_step_index_sampling_capped_at_8():
    """T50h：步骤数多于 8 时分步图恰为 8 张（首尾步均含、采样无重复单调）。"""
    from report.render import _MAX_STEP_SHOTS, _select_step_indices

    assert _MAX_STEP_SHOTS == 8
    idx = _select_step_indices(20, _MAX_STEP_SHOTS)
    assert len(idx) == 8
    assert idx[0] == 0 and idx[-1] == 19
    assert idx == sorted(set(idx))
    assert len(_select_step_indices(12, _MAX_STEP_SHOTS)) == 8
    assert _select_step_indices(3, _MAX_STEP_SHOTS) == [0, 1, 2]  # 不足 8 步全出


def test_render_empty_inputs_do_not_crash(tmp_path):
    """空数据容错：不抛异常，至少仍生成 route_map。"""
    out = render_figures({}, {}, {}, {"nodes": [], "node_order": [],
                                      "distance_matrix_km": [], "duration_min": []},
                         str(tmp_path))
    names = [os.path.basename(p) for p in out]
    assert "route_map.png" in names
    for p in out:
        _assert_png(p)


def test_render_is_deterministic(tmp_path):
    """同一输入两次渲染，文件集合一致（可复现）。"""
    bundle = mini_bundle()
    a = render_figures(bundle["routes"], bundle["packing"], bundle["loading_plan"],
                       bundle["network"], str(tmp_path / "a"))
    b = render_figures(bundle["routes"], bundle["packing"], bundle["loading_plan"],
                       bundle["network"], str(tmp_path / "b"))
    assert sorted(os.path.basename(p) for p in a) == sorted(os.path.basename(p) for p in b)


def test_packing_stats_lines_reports_unplaced():
    """契约 v1.3：unplaced 非空时统计行包含“未装载 N 件”；为空时自动隐藏。"""
    from report.render import _packing_stats_lines

    ok = _packing_stats_lines({"volume_utilization": 0.5, "weight_utilization": 0.6,
                               "unplaced": []})
    assert ok == ["体积利用率 50% · 载重利用率 60%"]
    warn = _packing_stats_lines({"volume_utilization": 0.5, "weight_utilization": 0.6,
                                 "unplaced": [{"sku": "A"}, {"sku": "B"}]})
    assert warn[-1] == "未装载 2 件"
    assert len(warn) == 2


def test_render_packing_with_unplaced_ok(tmp_path):
    """带未装载货物的 packing 数据仍能正常渲染（标注不破坏出图）。"""
    bundle = mini_bundle()
    bundle["packing"]["vehicles"][0]["unplaced"] = [{"sku": "SKU0999", "qty": 1}]
    out = render_figures(bundle["routes"], bundle["packing"], bundle["loading_plan"],
                         bundle["network"], str(tmp_path))
    names = [os.path.basename(p) for p in out]
    assert "packing_V01.png" in names
    for p in out:
        _assert_png(p)
