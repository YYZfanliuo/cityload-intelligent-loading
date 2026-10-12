# -*- coding: utf-8 -*-
"""T25 最终产物复核测试（窗口E）——outputs/t25-final 与 t25-baseline。

对已生成的双产物做结构与一致性校验；产物缺失时自动 skip（不阻塞其他测试）。
用途：C 的 T31b（复现性修复）结论后如需重生成产物，跑本文件一键复核：

    pytest tests/test_final_outputs.py -q

校验项：
1. t25-final 报告：章节齐全（含对比表）、无占位符/外链/脚本、图件零缺失、v1.3 装下率展示
2. t25-baseline 报告：章节齐全（不含对比表）、图件零缺失
3. 口径核验：final 的 comparison.baseline 与独立 baseline 产物的确定性指标逐项一致
4. 件级守恒：total_item_count == placed_item_count + unplaced_item_count
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FINAL = ROOT / "outputs" / "t25-final"
BASELINE = ROOT / "outputs" / "t25-baseline"

_SECTION_RE = re.compile(r"<h2>(\d) · ([^<]+)</h2>")
_IMG_RE = re.compile(r'src="([^"]+)"')

#: 不同进程运行间天然不同的字段，核验时排除
_NONDETERMINISTIC_KEYS = {"solve_time_sec", "peak_memory_mb"}


def _load_html(base: Path) -> str:
    path = base / "report.html"
    if not path.exists():
        pytest.skip(f"{base.name}/report.html 不存在（等待产物生成）")
    return path.read_text(encoding="utf-8")


def _missing_figures(base: Path, html: str) -> list:
    return [s for s in _IMG_RE.findall(html) if not (base / s).is_file()]


def test_final_report_structure():
    html = _load_html(FINAL)
    titles = [t for _, t in _SECTION_RE.findall(html)]
    for expected in ("场景说明", "核心指标", "配送路线图", "车厢 3D 装载方案",
                     "分步装卸指引", "优化 vs 基准对比", "数据来源与说明"):
        assert expected in titles, f"缺少章节：{expected}"
    assert not re.search(r"\$[a-z_]+", html), "存在未替换的模板占位符"
    assert "<script" not in html.lower(), "不应包含脚本"
    assert "http://" not in html and "https://" not in html, "不应包含外部链接"
    missing = _missing_figures(FINAL, html)
    assert not missing, f"缺失图件 {len(missing)} 张：{missing[:5]}"
    assert re.search(r"货物装载 \d+/\d+ 件（装下率 [\d.]+%）", html), "缺少 v1.3 装下率展示"


def test_baseline_report_structure():
    html = _load_html(BASELINE)
    titles = [t for _, t in _SECTION_RE.findall(html)]
    assert "场景说明" in titles and "分步装卸指引" in titles
    assert "优化 vs 基准对比" not in titles, "baseline 报告不应包含对比表"
    missing = _missing_figures(BASELINE, html)
    assert not missing, f"缺失图件 {len(missing)} 张：{missing[:5]}"


def test_final_comparison_matches_baseline_run():
    """口径核验：final 对比表的 baseline 列与独立 baseline 产物逐项一致（排除时间/内存）。"""
    fm, bm = FINAL / "metrics.json", BASELINE / "metrics.json"
    if not (fm.exists() and bm.exists()):
        pytest.skip("metrics.json 不存在")
    final_metrics = json.loads(fm.read_text(encoding="utf-8"))
    base_metrics = json.loads(bm.read_text(encoding="utf-8"))["metrics"]
    comp = (final_metrics.get("comparison") or {}).get("baseline")
    assert comp, "final metrics 缺 comparison.baseline"
    for key, value in comp.items():
        if key in _NONDETERMINISTIC_KEYS or key not in base_metrics:
            continue
        assert value == base_metrics[key], f"{key}: final={value} baseline={base_metrics[key]}"


def test_final_item_counts_consistent():
    """v1.3 件级守恒：total = placed + unplaced。"""
    fm = FINAL / "metrics.json"
    if not fm.exists():
        pytest.skip("metrics.json 不存在")
    m = json.loads(fm.read_text(encoding="utf-8"))["metrics"]
    for key in ("total_item_count", "placed_item_count", "unplaced_item_count"):
        assert isinstance(m.get(key), int), f"缺 v1.3 字段 {key}"
    assert m["total_item_count"] == m["placed_item_count"] + m["unplaced_item_count"]


def test_step_figures_capped_at_8():
    """T50h：每车分步图 ≤8 张，且与 loading_plan 步骤数精确一致（min(steps, 8)）。"""
    fm = FINAL / "packing.json"
    fp = FINAL / "loading_plan.json"
    if not (fm.exists() and fp.exists()):
        pytest.skip("packing.json / loading_plan.json 不存在")
    vehicles = json.loads(fm.read_text(encoding="utf-8")).get("vehicles") or []
    plans = json.loads(fp.read_text(encoding="utf-8")).get("plans") or []
    fig_dir = FINAL / "figures"
    for i, v in enumerate(vehicles, start=1):
        plan = next((p for p in plans
                     if p.get("vehicle_instance") == v.get("vehicle_instance")), None)
        steps = len((plan or {}).get("loading_steps") or [])
        placed = len(v.get("placed") or [])
        expected = min(steps, 8) if (steps and placed) else 0
        have = len(list(fig_dir.glob(f"step_V{i:02d}_*.png")))
        assert have == expected, (
            f"{v.get('vehicle_instance')}: steps={steps} placed={placed} "
            f"figures={have} expected={expected}")
