"""CityLoad 一键运行管线。"""
from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.explainer import gen_guide_text
from eval.metrics import compute_metrics
from report.build_html import build_html
from report.render import render_figures
from solver.baseline import solve_baseline
from solver.packing import solve_packing
from solver.routing import solve_routing
from solver.sequencing import sequence_loading


def _read(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _item_counts(orders: dict, packing: dict, routes: dict) -> tuple[int, int, int]:
    """按 qty 统计所有订单，未装载包含未分配订单，并检查求解结果守恒。"""
    total = sum(int(item.get("qty", 0)) for order in orders.get("orders", []) for item in order.get("items", []))
    placed = sum(len(vehicle.get("placed") or []) for vehicle in packing.get("vehicles", []))
    unplaced = sum(int(item.get("qty", 1)) for vehicle in packing.get("vehicles", []) for item in vehicle.get("unplaced", []))
    unassigned_ids = routes.get("unassigned_orders", [])
    unassigned = set(unassigned_ids)
    known_ids = {order.get("order_id") for order in orders.get("orders", []) if order.get("order_id") is not None}
    if len(unassigned_ids) != len(unassigned) or not unassigned <= known_ids:
        raise ValueError("未派订单包含重复或未知 order_id")
    unplaced += sum(int(item.get("qty", 0)) for order in orders.get("orders", [])
                    if order.get("order_id") in unassigned for item in order.get("items", []))
    if total != placed + unplaced:
        raise ValueError(f"件数不守恒：总计 {total}，已装 {placed}，未装 {unplaced}")
    return total, placed, unplaced


def _solve(orders: dict, vehicles: dict, network: dict, rules: dict, baseline: bool) -> dict:
    """执行指定方案，基线布局和装载计划保持原样。"""
    if baseline:
        return solve_baseline(orders, vehicles, network, rules)
    routes = solve_routing(orders, vehicles, network, rules)
    packing = solve_packing(routes, orders, vehicles, rules)
    # sequencing 会就地调整 packing 的坐标；必须在序列化前完成。
    loading_plan = sequence_loading(packing, routes, rules)
    return {"routes": routes, "packing": packing, "loading_plan": loading_plan,
            "solve_time_sec": routes.get("solve_time_sec", 0.0),
            "peak_memory_mb": routes.get("peak_memory_mb", 0.0)}


def _evaluate(result: dict, orders: dict, vehicles: dict, network: dict) -> dict:
    """展开路线容器，保留求解器性能，并验证件级计数。"""
    routes, packing = result["routes"], result["packing"]
    total, placed, unplaced = _item_counts(orders, packing, routes)
    metrics = compute_metrics({"routes": routes.get("routes", []), "packing": packing,
                               "unassigned_orders": routes.get("unassigned_orders", []),
                               "solve_time_sec": result.get("solve_time_sec", routes.get("solve_time_sec", 0.0)),
                               "peak_memory_mb": result.get("peak_memory_mb", routes.get("peak_memory_mb", 0.0))},
                              orders, vehicles, network)
    metrics.setdefault("metrics", {}).update({"total_item_count": total, "placed_item_count": placed,
                                              "unplaced_item_count": unplaced})
    if unplaced:
        metrics.setdefault("warnings", []).append(f"未装载 {unplaced} 件；请查看 packing.json、routes.json 与装载指引中的提醒")
    return metrics


def run_pipeline(data_dir: str | Path, out_dir: str | Path, baseline: bool = False,
                 comparison: bool = False) -> dict:
    """执行指定方案并生成报告；对比模式独立求解两种方案。"""
    if baseline and comparison:
        raise ValueError("--baseline 与 --comparison 不能同时使用")
    data = Path(data_dir)
    out = Path(out_dir)
    inputs = tuple(_read(data / name) for name in ("orders.json", "vehicles.json", "network.json", "rules.json"))
    orders, vehicles, network, rules = inputs
    result = _solve(*deepcopy(inputs), baseline=baseline)
    routes, packing, loading_plan = result["routes"], result["packing"], result["loading_plan"]
    metrics = _evaluate(result, orders, vehicles, network)
    if comparison:
        baseline_result = _solve(*deepcopy(inputs), baseline=True)
        baseline_metrics = _evaluate(baseline_result, orders, vehicles, network)
        metrics["comparison"] = {"baseline": dict(baseline_metrics["metrics"]),
                                 "optimized": dict(metrics["metrics"])}
    guide_text = gen_guide_text(loading_plan, routes)
    # 全车未装下时 explainer 没有步骤文本；仍保留车辆顺序与真实未装提醒。
    guide_by_vehicle = {g["vehicle_instance"]: g for g in guide_text.get("guides", [])}
    plans = {p["vehicle_instance"]: p for p in loading_plan.get("plans", [])}
    ordered_guides = []
    for vehicle in packing.get("vehicles", []):
        instance = vehicle["vehicle_instance"]
        guide = guide_by_vehicle.get(instance, {"vehicle_instance": instance, "text": "没有已装载货物。"})
        count = sum(int(item.get("qty", 1)) for item in vehicle.get("unplaced", []))
        if count:
            guide["text"] += f"\n未装载 {count} 件，不包含在上述装载步骤中，需另行安排配送。"
        if instance not in guide_by_vehicle:
            for warning in plans.get(instance, {}).get("blocks_warning", []):
                guide["text"] += f"\n装载提醒：{warning}"
        ordered_guides.append(guide)
    guide_text["guides"] = ordered_guides

    _write(out / "routes.json", routes)
    _write(out / "packing.json", packing)
    _write(out / "loading_plan.json", loading_plan)
    _write(out / "metrics.json", metrics)
    _write(out / "guide_text.json", guide_text)
    figures = render_figures(routes, packing, loading_plan, network, str(out / "figures"))
    report = build_html(routes, metrics, guide_text, figures, str(out / "report.html"))
    return {"routes": routes, "packing": packing, "loading_plan": loading_plan,
            "metrics": metrics, "guide_text": guide_text, "figures": figures, "report": report}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行 CityLoad 完整管线")
    parser.add_argument("--data", default="data/", help="输入数据目录")
    parser.add_argument("--out", default="output/", help="输出目录")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--baseline", action="store_true", help="运行贪心基线方案")
    mode.add_argument("--comparison", action="store_true", help="独立运行基线与优化方案，生成真实指标对比")
    args = parser.parse_args(argv)
    try:
        result = run_pipeline(args.data, args.out, baseline=args.baseline, comparison=args.comparison)
    except Exception as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 1
    m = result["metrics"].get("metrics", {})
    print(f"[完成] 输出: {Path(args.out).resolve()} | 车辆 {m.get('vehicle_count', 0)} | "
          f"件数 {m.get('placed_item_count', 0)}/{m.get('total_item_count', 0)} | "
          f"图件 {len(result['figures'])} | 耗时 {m.get('solve_time_sec', 0):.3f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
