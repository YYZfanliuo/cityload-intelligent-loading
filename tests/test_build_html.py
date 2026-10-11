# -*- coding: utf-8 -*-
"""T24 单测 —— report/build_html.py（窗口E）。"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mock_data import mini_bundle  # noqa: E402
from report.build_html import build_html  # noqa: E402
from report.render import render_figures  # noqa: E402


def _build(tmp_path, metrics=None):
    bundle = mini_bundle()
    figs = render_figures(bundle["routes"], bundle["packing"], bundle["loading_plan"],
                          bundle["network"], str(tmp_path / "figures"))
    out = tmp_path / "report.html"
    ret = build_html(bundle["routes"], metrics or bundle["metrics"],
                     bundle["guide_text"], figs, str(out))
    return ret, out.read_text(encoding="utf-8")


def test_build_html_contract(tmp_path):
    """契约 §2.9：返回 HTML 路径；页面包含关键区块与相对路径图片。"""
    ret, text = _build(tmp_path)
    assert os.path.isfile(ret) and ret.endswith("report.html")
    assert "智载 CityLoad" in text
    assert "场景说明" in text and "核心指标" in text and "分步装卸指引" in text
    # 图片相对路径引用（相对 outpath 目录）
    assert "figures/route_map.png" in text
    assert "figures/packing_V01.png" in text
    assert "figures/step_V01_1.png" in text
    # 指标卡数字来自 metrics
    assert "60.5" in text  # total_distance_km
    assert "100.0%" in text or "100%" in text  # on_time_rate


def test_build_html_no_external_deps_and_no_placeholder_left(tmp_path):
    """离线约束：无 CDN/脚本；模板占位符全部替换。"""
    _, text = _build(tmp_path)
    assert "<script" not in text.lower()
    assert "cdn." not in text.lower()
    assert "http://" not in text and "https://" not in text
    assert not re.search(r"\$[a-z_]+", text), "存在未替换的模板占位符"


def test_build_html_without_comparison_has_no_section(tmp_path):
    """无 comparison 数据时，不渲染对比节，说明节编号为 6。"""
    _, text = _build(tmp_path)
    assert "优化 vs 基准" not in text
    assert "6 · 数据来源与说明" in text


def test_build_html_with_comparison(tmp_path):
    """metrics["comparison"] 存在时渲染对比表，说明节编号顺延为 7。"""
    bundle = mini_bundle()
    metrics = dict(bundle["metrics"])
    metrics["comparison"] = {
        "baseline": {"vehicle_count": 3, "total_distance_km": 80.0, "total_cost": 500.0,
                     "avg_weight_load_rate": 0.22, "on_time_rate": 0.85,
                     "unassigned_order_count": 0},
        "optimized": bundle["metrics"]["metrics"],
    }
    _, text = _build(tmp_path, metrics)
    assert "优化 vs 基准" in text
    assert "7 · 数据来源与说明" in text
    assert "better" in text  # 至少一个指标有改善标记


def test_build_html_missing_figures_graceful(tmp_path):
    """无图件时的降级：给出占位提示而非崩溃。"""
    bundle = mini_bundle()
    ret = build_html(bundle["routes"], bundle["metrics"], bundle["guide_text"],
                     [], str(tmp_path / "r" / "report.html"))
    text = open(ret, encoding="utf-8").read()
    assert "未生成" in text  # 路线图/3D 图占位提示


def test_build_html_v13_item_counts(tmp_path):
    """契约 v1.3：渲染装下率与未装载警示；unplaced=0 时警示自动隐藏。"""
    bundle = mini_bundle()
    base = bundle["metrics"]
    metrics = {**base, "metrics": {**base["metrics"],
                                   "total_item_count": 100, "placed_item_count": 92,
                                   "unplaced_item_count": 8}}
    _, text = _build(tmp_path, metrics)
    assert "货物装载 92/100 件" in text
    assert "装下率 92.0%" in text
    assert "8 件货物因装载约束未能装入" in text

    metrics0 = {**base, "metrics": {**base["metrics"],
                                    "total_item_count": 100, "placed_item_count": 100,
                                    "unplaced_item_count": 0}}
    _, text0 = _build(tmp_path, metrics0)
    assert "货物装载 100/100 件" in text0
    assert "装下率 100.0%" in text0
    assert "未能装入" not in text0
