"""公平贪心基线：距仓就近分组、门店编号访问、原顺序货架装箱。"""
from __future__ import annotations
import math
import time
from collections import Counter
from .routing import (_order_load, _order_volume, _vehicle_volume, _vehicle_pool,
                      _route_record, _travel, _minutes, _peak_memory_mb, DEPARTURE)
from .packing import _orientations, _overlap
from .sequencing import _blocks


def _schedule(stores, by_store, idx, duration, vehicle, rules):
    """对编号顺序整条路线检查服务时间、窗口与返仓工时。"""
    clock = DEPARTURE
    previous = "WH01"
    config = rules.get("routing", {})
    for store in sorted(stores):
        order = by_store[store]
        clock += _travel(duration[idx[previous]][idx[store]])
        if config.get("respect_time_window", True):
            clock = max(clock, round(_minutes(order["time_window"][0]) * 60))
            if not config.get("allow_overtime_penalty", False) and clock > round(_minutes(order["time_window"][1]) * 60):
                return False
        clock += _travel(order.get("service_minutes", 0))
        previous = store
    clock += _travel(duration[idx[previous]][idx["WH01"]])
    return clock - DEPARTURE <= math.floor(vehicle.get("max_work_minutes", 480) * 60)


def _sequential_pack(items, vehicle, rules):
    """逐件原顺序装填；先尝试已有完整支撑塔顶，否则顺序铺地板。"""
    L, W, H = (vehicle[k] for k in ("cargo_length_cm", "cargo_width_cm", "cargo_height_cm"))
    gap = max(0, rules.get("packing", {}).get("gap_cm", 0))
    placed, unplaced, towers = [], [], []
    sx = sy = row = weight = 0.0
    for item in items:
        choice = None
        limit = item.get("max_stack_layers", 1)
        if limit >= 1 and weight + item["weight_kg"] <= vehicle["max_weight_kg"] + 1e-9:
            for rotated, (l, w, h) in _orientations(item, rules):
                if min(l, w, h) <= 0 or l > L or w > W or h > H:
                    continue
                for tower in towers:
                    depth = tower["depth"] + 1
                    if (depth > limit or tower["remaining"] < 1 or l > tower["l"] or w > tower["w"] or tower["z"] + h > H):
                        continue
                    candidate = {"position_cm": {"x": tower["x"], "y": tower["y"], "z": tower["z"]}, "size_placed_cm": {"l": l, "w": w, "h": h}}
                    if not any(_overlap(candidate, box) for box in placed):
                        choice = (candidate, rotated, depth, tower, None)
                        break
                if choice:
                    break
                x, y, rw = sx, sy, row
                if x + l > L:
                    x, y, rw = 0, y + rw + gap, 0
                if y + w <= W:
                    candidate = {"position_cm": {"x": x, "y": y, "z": 0}, "size_placed_cm": {"l": l, "w": w, "h": h}}
                    if not any(_overlap(candidate, box) for box in placed):
                        choice = (candidate, rotated, 1, None, (x + l + gap, y, max(rw, w)))
                        break
        if choice is None:
            unplaced.append({"sku": item["sku"], "order_id": item["order_id"], "store_id": item["store_id"], "qty": 1})
            continue
        candidate, rotated, depth, tower, shelf = choice
        placed.append({"sku": item["sku"], "order_id": item["order_id"], "store_id": item["store_id"], **candidate, "layer": depth, "rotated": rotated, "weight_kg": item["weight_kg"]})
        weight = math.fsum(box["weight_kg"] for box in placed)
        remaining = limit - 1
        if tower is None:
            sx, sy, row = shelf
            tower = {}
            towers.append(tower)
        else:
            remaining = min(remaining, tower["remaining"] - 1)
        if item.get("fragile", False) or not item.get("stackable", True):
            remaining = 0
        pos, size = candidate["position_cm"], candidate["size_placed_cm"]
        tower.update(x=pos["x"], y=pos["y"], z=pos["z"] + size["h"], l=size["l"], w=size["w"], depth=depth, remaining=remaining)
    return placed, unplaced


def solve_baseline(orders: dict, vehicles: dict, network: dict, rules: dict) -> dict:
    """按相同约束和计费规则构造无精细路线/装箱优化的基线，不隐藏拒装。"""
    started = time.perf_counter()
    order_list = orders.get("orders", [])
    by_store = {o["store_id"]: o for o in order_list}
    if len(by_store) != len(order_list):
        raise ValueError("store_id必须唯一")
    nodes = network["node_order"]
    idx = {node: i for i, node in enumerate(nodes)}
    distances, duration = network["distance_matrix_km"], network["duration_matrix_min"]
    remaining = sorted(order_list, key=lambda o: (distances[idx["WH01"]][idx[o["store_id"]]], o["store_id"]))
    routes, packed, plans = [], [], []
    for vehicle in _vehicle_pool(vehicles):
        group = []
        load = volume = 0.0
        for order in list(remaining):
            w, vol = _order_load(order), _order_volume(order)
            if load + w > vehicle["max_weight_kg"] + 1e-9 or volume + vol > _vehicle_volume(vehicle) + 1e-9:
                continue
            stores = [o["store_id"] for o in group] + [order["store_id"]]
            if not _schedule(stores, by_store, idx, duration, vehicle, rules):
                continue
            group.append(order)
            remaining.remove(order)
            load += w
            volume += vol
        if not group:
            continue
        delivery = sorted(o["store_id"] for o in group)
        route = _route_record(f"R{len(routes)+1:02d}", vehicle, nodes, distances, duration, by_store, ["WH01", *delivery, "WH01"], rules)
        routes.append(route)
        items = [{**item, "order_id": by_store[store]["order_id"], "store_id": store} for store in delivery for item in by_store[store]["items"] for _ in range(item["qty"])]
        placed, unplaced = _sequential_pack(items, vehicle, rules)
        instance = route["route_id"] + "-1"
        occupied = math.fsum(b["size_placed_cm"]["l"] * b["size_placed_cm"]["w"] * b["size_placed_cm"]["h"] for b in placed)
        packed.append({"vehicle_instance": instance, "vehicle_type": vehicle["vehicle_id"], "placed": placed, "unplaced": unplaced, "volume_utilization": round(occupied / _vehicle_volume(vehicle), 4), "weight_utilization": round(math.fsum(b["weight_kg"] for b in placed) / vehicle["max_weight_kg"], 4)})
        steps = []
        for store in delivery:
            qty = Counter(b["sku"] for b in placed if b["store_id"] == store)
            if qty:
                steps.append({"step": len(steps)+1, "action": "装", "store_id": store, "items": [{"sku": sku, "qty": n} for sku, n in sorted(qty.items())], "note": "基线按门店编号顺序装填，不做逆配送排箱"})
        blockers = _blocks(placed, {store: rank for rank, store in enumerate(delivery)})
        warnings = [f"{early}被后送{late}阻挡（{kind}，{count}对），需搬移货物" for (early, late, kind), count in sorted(blockers.items())]
        plans.append({"vehicle_instance": instance, "delivery_order": delivery, "loading_steps": steps, "blocks_warning": warnings})
    elapsed = time.perf_counter() - started
    peak = _peak_memory_mb()
    unassigned = [o["order_id"] for o in remaining]
    routing = {"status": "partial" if unassigned else "ok", "solve_time_sec": round(elapsed, 6), "peak_memory_mb": round(peak, 3), "solver_backend": "baseline_greedy", "routes": routes, "unassigned_orders": unassigned, "summary": {"vehicle_count": len(routes), "total_distance_km": round(math.fsum(r["total_distance_km"] for r in routes), 3), "total_cost": round(math.fsum(r["cost"] for r in routes) + len(unassigned) * rules.get("routing", {}).get("unassigned_penalty", 1000), 3)}}
    return {"routes": routing, "packing": {"status": "ok", "vehicles": packed}, "loading_plan": {"status": "ok", "plans": plans}, "solve_time_sec": routing["solve_time_sec"], "peak_memory_mb": routing["peak_memory_mb"]}
