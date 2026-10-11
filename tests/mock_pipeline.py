# -*- coding: utf-8 -*-
"""T23/T24 管线自测 —— 用 mock 假数据把「渲染 → HTML」全链路跑通。

用法（项目根目录运行）::

    python tests/mock_pipeline.py                 # 生成 output/figures/*.png + output/report.html
    python tests/mock_pipeline.py --comparison    # 额外演示“优化 vs 基准”对比表样式

产物:
    <out>/figures/*.png   与窗口C 联调时同样的图件命名（route_map / packing_V* / step_V*）
    <out>/report.html     单页报告，可直接浏览器打开（离线、无 CDN）

说明：数据来自 tests/mock_data.py::build_real_bundle（读 data/ 真实数据集组装，
metrics 由真实 eval/metrics.py 计算）。窗口C 的 T25 联调完成后，由 run.py 用真实
求解结果直接覆盖上述产物。
"""

from __future__ import annotations

import argparse
import copy
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from mock_data import build_real_bundle  # noqa: E402
from report.build_html import build_html  # noqa: E402
from report.render import render_figures  # noqa: E402


def _fake_comparison(metrics: dict) -> dict:
    """构造演示用对比数据（优化 vs 基准），仅用于预览对比表样式。"""
    opt = metrics.get("metrics") or {}
    baseline = copy.deepcopy(opt)
    for key, factor in (("total_distance_km", 1.18), ("total_cost", 1.25),
                        ("vehicle_count", 1.2), ("unassigned_order_count", 3)):
        if isinstance(baseline.get(key), (int, float)):
            baseline[key] = round(baseline[key] * factor, 2) if key != "unassigned_order_count" else 3
    for key, factor in (("avg_weight_load_rate", 0.82), ("avg_volume_utilization", 0.85),
                        ("on_time_rate", 0.94)):
        if isinstance(baseline.get(key), (int, float)):
            baseline[key] = round(baseline[key] * factor, 4)
    return {"baseline": baseline, "optimized": opt}


def main() -> int:
    parser = argparse.ArgumentParser(description="窗口E 管线自测（mock 数据）")
    parser.add_argument("--data", default=str(ROOT / "data"), help="输入数据目录")
    parser.add_argument("--out", default=str(ROOT / "output"), help="输出目录")
    parser.add_argument("--comparison", action="store_true", help="附加演示对比表（假 baseline 数字）")
    args = parser.parse_args()

    out_dir = Path(args.out)
    fig_dir = out_dir / "figures"

    t0 = time.perf_counter()
    bundle = build_real_bundle(args.data)
    m = bundle["metrics"]["metrics"]
    print(f"[mock ] 组装完成：{len(bundle['routes']['routes'])} 车次 / "
          f"{m.get('total_distance_km')} km / ¥{m.get('total_cost')} / "
          f"时效 {m.get('on_time_rate')}")

    t1 = time.perf_counter()
    figures = render_figures(bundle["routes"], bundle["packing"], bundle["loading_plan"],
                             bundle["network"], str(fig_dir))
    print(f"[图件 ] 生成 {len(figures)} 张，用时 {time.perf_counter() - t1:.1f}s")
    for f in figures:
        print(f"        {os.path.relpath(f, ROOT)}  ({os.path.getsize(f) / 1024:.0f} KB)")

    metrics = bundle["metrics"]
    if args.comparison:
        metrics = dict(metrics)
        metrics["comparison"] = _fake_comparison(metrics)
        print("[对比 ] 已附加演示用 comparison 数据（假 baseline）")

    t2 = time.perf_counter()
    report = build_html(bundle["routes"], metrics, bundle["guide_text"], figures,
                        str(out_dir / "report.html"))
    size_kb = os.path.getsize(report) / 1024
    print(f"[HTML ] {os.path.relpath(report, ROOT)}  ({size_kb:.0f} KB)，"
          f"用时 {time.perf_counter() - t2:.2f}s")

    print(f"[完成 ] 总用时 {time.perf_counter() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
