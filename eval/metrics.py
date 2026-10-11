# -*- coding: utf-8 -*-
"""智载 CityLoad —— 评估指标模块（T15 · 窗口B）

接口契约（docs/interfaces.md §2.5，签名冻结）：
    compute_metrics(result, orders, vehicles, network) -> dict

输入:
    result   —— routes.json 结构（或 baseline 输出 {routes, packing, loading_plan}）
    orders   —— data/orders.json 结构
    vehicles —— data/vehicles.json 结构
    network  —— data/network.json 结构
输出:
    metrics.json 结构（接口契约 §3.4）

指标：
    vehicle_count / total_distance_km / total_cost / avg_weight_load_rate
    / avg_volume_utilization / on_time_rate / unassigned_order_count
    / solve_time_sec / peak_memory_mb

关于 solve_time_sec 与 peak_memory_mb（接口契约 v1.1 · 3.1 节）:
    求解器须将两个字段输出在 result 顶层（summary/meta 之下亦可读取）：
        result["solve_time_sec"] = 12.3     # 求解耗时
        result["peak_memory_mb"] = 180.2    # 峰值内存
    peak_memory_mb 缺失时依次回退 tracemalloc 峰值（若调用方已 start()）→
    psutil 当前 RSS；
    两个字段最终仍无法获得时，按契约「缺失时按 0 计并在 metrics 中标注」处理：
    数值记 0.0，并在返回结构顶层附加 warnings 数组说明。
"""

from __future__ import annotations

import statistics
import tracemalloc


# ---------------------------------------------------------------- 内部工具

def _to_min(hhmm: str) -> int:
    """'HH:MM' → 当日分钟数。"""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _order_weight(order: dict) -> float:
    """订单总重量（kg）。"""
    return sum(it["qty"] * it["weight_kg"] for it in order.get("items") or [])


def _order_volume_m3(order: dict) -> float:
    """订单货物外廓总体积（m³）。"""
    total_cm3 = sum(
        it["qty"] * it["length_cm"] * it["width_cm"] * it["height_cm"]
        for it in order.get("items") or []
    )
    return total_cm3 / 1e6


def _route_store_ids(route: dict, warehouse_id: str) -> list[str]:
    """路径服务的门店列表（优先 stop_details，其次 sequence 去除仓库）。"""
    details = route.get("stop_details")
    if details:
        return [d.get("node") for d in details if d.get("node") and d.get("node") != warehouse_id]
    return [n for n in (route.get("sequence") or []) if n != warehouse_id]


def _route_distance_km(route: dict, network: dict) -> float:
    """路径里程：优先取字段，缺失时用 network 距离矩阵按 sequence 重算。"""
    if route.get("total_distance_km") is not None:
        return float(route["total_distance_km"])
    seq = route.get("sequence") or []
    if len(seq) < 2:
        return 0.0
    idx = {n: i for i, n in enumerate(network.get("node_order") or [])}
    dm = network.get("distance_matrix_km") or []
    total = 0.0
    for a, b in zip(seq, seq[1:]):
        if a in idx and b in idx and dm:
            total += dm[idx[a]][idx[b]]
    return round(total, 2)


def _read_opt_float(result: dict, key: str):
    """从 result 顶层 / summary / meta 中读取可选数值字段，找不到返回 None。"""
    for src in (result, result.get("summary"), result.get("meta")):
        if isinstance(src, dict) and src.get(key) is not None:
            try:
                return float(src[key])
            except (TypeError, ValueError):
                pass
    return None


def _fallback_peak_memory_mb():
    """峰值内存回退探测：tracemalloc（已 start 时）→ psutil 当前 RSS → None。"""
    if tracemalloc.is_tracing():
        _, peak = tracemalloc.get_traced_memory()
        return round(peak / 1024.0 / 1024.0, 1)
    try:
        import psutil  # type: ignore  # 可选依赖，未安装则跳过
        return round(psutil.Process().memory_info().rss / 1024.0 / 1024.0, 1)
    except Exception:
        return None


# ---------------------------------------------------------------- 主函数

def compute_metrics(result: dict, orders: dict, vehicles: dict, network: dict) -> dict:
    """计算全套评估指标，返回 metrics.json 结构（接口契约 §3.4）。"""
    routes = result.get("routes") or []
    packing = result.get("packing") or {}

    veh_by_id = {v["vehicle_id"]: v for v in (vehicles.get("vehicles") or [])}
    order_list = orders.get("orders") or []
    orders_by_store = {o["store_id"]: o for o in order_list}
    total_stores = len(order_list)
    warehouse_id = ((orders.get("meta") or {}).get("warehouse") or {}).get("id", "WH01")

    # packing 结果按 route_id 索引（vehicle_instance 形如 "R01-1"）
    pack_by_route: dict = {}
    for pv in packing.get("vehicles") or []:
        inst = pv.get("vehicle_instance") or ""
        if "-" in inst:
            pack_by_route[inst.rsplit("-", 1)[0]] = pv

    per_route: list[dict] = []
    total_distance = 0.0
    total_cost = 0.0
    weight_rates: list[float] = []
    on_time_count = 0
    served: set = set()

    for r in routes:
        rid = r.get("route_id")
        vt = r.get("vehicle_type")
        veh = veh_by_id.get(vt) or {}
        stores = _route_store_ids(r, warehouse_id)

        # --- 里程 ---
        dist = _route_distance_km(r, network)

        # --- 成本：优先取字段，缺失时按车型公式 = 固定 + 里程 × 每公里 ---
        if r.get("cost") is not None:
            cost = float(r["cost"])
        elif veh:
            cost = round(veh.get("fixed_cost", 0.0) + dist * veh.get("cost_per_km", 0.0), 2)
        else:
            cost = 0.0

        # --- 载重：优先取字段，缺失时按服务门店订单汇总 ---
        if r.get("total_load_kg") is not None:
            load = float(r["total_load_kg"])
        else:
            load = round(sum(_order_weight(orders_by_store[s]) for s in stores if s in orders_by_store), 2)
        cap = float(veh.get("max_weight_kg") or 0.0)
        load_rate = load / cap if cap > 0 else 0.0

        # --- 时效：arrive <= 时间窗结束即按时（首见门店计一次） ---
        on_time_stops = 0
        for d in r.get("stop_details") or []:
            sid = d.get("node")
            if not sid or sid in served:
                continue
            served.add(sid)
            o = orders_by_store.get(sid)
            arrive = d.get("arrive")
            if o and arrive and _to_min(arrive) <= _to_min(o["time_window"][1]):
                on_time_count += 1
                on_time_stops += 1

        total_distance += dist
        total_cost += cost
        weight_rates.append(load_rate)

        per_route.append({
            "route_id": rid,
            "vehicle_type": vt,
            "stop_count": len(stores),
            "distance_km": round(dist, 2),
            "total_load_kg": round(load, 2),
            "load_rate_weight": round(load_rate, 4),
            "cost": round(cost, 2),
            "on_time_stops": on_time_stops,
            "volume_utilization": (pack_by_route.get(rid) or {}).get("volume_utilization"),
        })

    # --- 体积利用率：优先 packing 输出；否则按 订单体积 / 车型容积 估算 ---
    vol_rates = [
        pv["volume_utilization"]
        for pv in packing.get("vehicles") or []
        if pv.get("volume_utilization") is not None
    ]
    if vol_rates:
        avg_volume = statistics.fmean(vol_rates)
    else:
        fallback = []
        for r in routes:
            veh = veh_by_id.get(r.get("vehicle_type")) or {}
            cap_vol = (
                veh.get("cargo_length_cm", 0.0)
                * veh.get("cargo_width_cm", 0.0)
                * veh.get("cargo_height_cm", 0.0)
            ) / 1e6
            if cap_vol <= 0:
                continue
            stores = _route_store_ids(r, warehouse_id)
            v = sum(_order_volume_m3(orders_by_store[s]) for s in stores if s in orders_by_store)
            fallback.append(v / cap_vol)
        avg_volume = statistics.fmean(fallback) if fallback else 0.0

    # --- 求解耗时 / 峰值内存（v1.1 契约：缺失时按 0 计并标注） ---
    warnings: list[str] = []
    solve_time = _read_opt_float(result, "solve_time_sec")
    if solve_time is None:
        solve_time = 0.0
        warnings.append("solve_time_sec 缺失（result 顶层未提供），已按 0 计")
    peak_mem = _read_opt_float(result, "peak_memory_mb")
    if peak_mem is None:
        peak_mem = _fallback_peak_memory_mb()
    if peak_mem is None:
        peak_mem = 0.0
        warnings.append("peak_memory_mb 缺失且无法探测（tracemalloc 未启动、psutil 不可用），已按 0 计")

    unassigned = result.get("unassigned_orders") or []

    metrics = {
        "vehicle_count": len(routes),
        "total_distance_km": round(total_distance, 2),
        "total_cost": round(total_cost, 2),
        "avg_weight_load_rate": round(statistics.fmean(weight_rates), 4) if weight_rates else 0.0,
        "avg_volume_utilization": round(avg_volume, 4),
        "on_time_rate": round(on_time_count / total_stores, 4) if total_stores else 0.0,
        "unassigned_order_count": len(unassigned),
        "solve_time_sec": solve_time,
        "peak_memory_mb": peak_mem,
    }

    meta = orders.get("meta") or {}
    out = {
        "scenario": meta.get("scenario", "pharmacy_chain"),
        "seed": meta.get("seed"),
        "metrics": metrics,
        "per_route": per_route,
    }
    if warnings:
        out["warnings"] = warnings
    return out
