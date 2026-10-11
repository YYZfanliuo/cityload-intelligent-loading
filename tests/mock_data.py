# -*- coding: utf-8 -*-
"""窗口E 自测用 —— mock 数据构造器（T23/T24 渲染管线联调的“假数据”）。

两个入口：

* ``mini_bundle()``       —— 完全自包含的小规模 mock（3 门店 / 2 车次），供单测使用；
* ``build_real_bundle()`` —— 读取仓库真实 ``data/`` 施工图（orders/vehicles/network），
  生成结构严格符合接口契约 v1.1 的 routes / packing / loading_plan / guide_text，
  并调用**真实的** ``eval/metrics.py::compute_metrics`` 计算 metrics。
  用于在窗口C 的求解器就绪前，先把「渲染 → HTML」链路整体跑通。

所有 mock 输出字段名与 docs/interfaces.md §3 完全一致；数据仅为管线演示，
窗口C 的 T25 联调后会被真实求解结果替换。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: mock 用车型车厢尺寸（与 data/vehicles.json 一致，避免依赖 report 私有表）
CARGO_DIMS = {
    "V01": (420.0, 200.0, 200.0),
    "V02": (340.0, 180.0, 180.0),
}


# --------------------------------------------------------------------- 通用工具

def _hhmm(minutes: int) -> str:
    return f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"


def _dist_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """平面近似球面距离（30°N 附近，mock 够用）。"""
    import math

    dy = (lat2 - lat1) * 111.0
    dx = (lng2 - lng1) * 111.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return round((dx * dx + dy * dy) ** 0.5, 3)


def _build_matrix(nodes: list[dict]) -> tuple[list[list[float]], list[list[float]]]:
    """按节点坐标生成对称的 distance_matrix_km / duration_matrix_min（30 km/h 估算）。"""
    n = len(nodes)
    dist = [[0.0] * n for _ in range(n)]
    dur = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = _dist_km(nodes[i]["lat"], nodes[i]["lng"], nodes[j]["lat"], nodes[j]["lng"])
            dist[i][j] = dist[j][i] = d
            dur[i][j] = dur[j][i] = round(d / 30.0 * 60.0, 3)
    return dist, dur


def _chunk(items: list, groups: int) -> list[list]:
    """把列表尽量均匀地切成 groups 份。"""
    k, m = divmod(len(items), groups)
    return [items[i * k + min(i, m):(i + 1) * k + min(i + 1, m)] for i in range(groups)]


def _order_weight(order: dict) -> float:
    return sum(it["qty"] * it["weight_kg"] for it in order.get("items") or [])


def _shelf_pack(entries: list[dict], L: float, W: float, H: float) -> tuple[list[dict], list[dict]]:
    """行式（shelf）简易装箱 —— 仅用于生成 mock 坐标：先沿 x 排、换行升 y、换层升 z。

    entries: [{sku, order_id, store_id, l, w, h}]；返回 (placed, unplaced)，
    placed 条目结构与契约 §3.2 一致。
    """
    placed, unplaced = [], []
    x = y = z = 0.0
    row_w = 0.0     # 当前行占用宽度（行内最大 w）
    layer_h = 0.0   # 当前层高度（层内最大 h）
    for it in entries:
        l, w, h = float(it["l"]), float(it["w"]), float(it["h"])
        if l <= 0 or w <= 0 or h <= 0:
            continue
        if x + l > L + 1e-6:            # 长度排满 → 换行
            y += row_w
            x, row_w = 0.0, 0.0
        if y + w > W + 1e-6:            # 宽度排满 → 换层
            z += layer_h
            y = x = row_w = layer_h = 0.0
        if z + h > H + 1e-6:            # 高度排满 → 放不下
            unplaced.append(it)
            continue
        placed.append({
            "sku": it["sku"],
            "order_id": it["order_id"],
            "store_id": it["store_id"],
            "position_cm": {"x": round(x, 2), "y": round(y, 2), "z": round(z, 2)},
            "size_placed_cm": {"l": l, "w": w, "h": h},
            "layer": 1,
            "rotated": False,
        })
        x += l
        row_w = max(row_w, w)
        layer_h = max(layer_h, h)
    return placed, unplaced


# --------------------------------------------------------------------- mini（自包含，单测用）

def mini_bundle() -> dict:
    """3 门店 / 2 车次的完全自包含 mock（不读 data/）。"""
    wh = {"id": "WH01", "lat": 30.2741, "lng": 120.1551}
    stores = [
        {"id": "S01", "lat": 30.3000, "lng": 120.2000, "name": "连锁药店01"},
        {"id": "S02", "lat": 30.2500, "lng": 120.1000, "name": "连锁药店02"},
        {"id": "S03", "lat": 30.2200, "lng": 120.1800, "name": "连锁药店03"},
    ]
    nodes = [wh] + stores
    dist, dur = _build_matrix(nodes)
    network = {
        "nodes": [{"id": n["id"], "lat": n["lat"], "lng": n["lng"]} for n in nodes],
        "node_order": [n["id"] for n in nodes],
        "distance_matrix_km": dist,
        "duration_matrix_min": dur,
    }

    # --- routes（R01: S01+S02；R02: S03）---
    def stop(sid, arrive):
        return {"node": sid, "arrive": arrive, "start_service": arrive,
                "end_service": arrive, "load_kg": 100.0}

    routes = {
        "status": "ok", "solve_time_sec": 3.2, "peak_memory_mb": 120.5,
        "routes": [
            {"route_id": "R01", "vehicle_type": "V01",
             "sequence": ["WH01", "S01", "S02", "WH01"],
             "stop_details": [stop("S01", "08:20"), stop("S02", "09:05")],
             "total_distance_km": 42.0, "total_duration_min": 130,
             "total_load_kg": 620.0, "load_rate_weight": 0.36, "cost": 267.0},
            {"route_id": "R02", "vehicle_type": "V02",
             "sequence": ["WH01", "S03", "WH01"],
             "stop_details": [stop("S03", "08:35")],
             "total_distance_km": 18.5, "total_duration_min": 70,
             "total_load_kg": 240.0, "load_rate_weight": 0.24, "cost": 131.8},
        ],
        "unassigned_orders": [],
        "summary": {"vehicle_count": 2, "total_distance_km": 60.5, "total_cost": 398.8},
    }

    # --- packing（手工不重叠坐标）---
    def box(sku, store, x, y, z, l, w, h):
        return {"sku": sku, "order_id": "O0001", "store_id": store,
                "position_cm": {"x": float(x), "y": float(y), "z": float(z)},
                "size_placed_cm": {"l": float(l), "w": float(w), "h": float(h)},
                "layer": 1, "rotated": False}

    packing = {
        "status": "ok",
        "vehicles": [
            {"vehicle_instance": "R01-1", "vehicle_type": "V01",
             "placed": [box("SKU0110", "S01", x, 0, 0, 40, 30, 25) for x in (0, 40, 80, 120)]
                     + [box("SKU0220", "S02", x, 32, 0, 35, 30, 25) for x in (0, 35, 70, 105)],
             "unplaced": [], "volume_utilization": 0.18, "weight_utilization": 0.36},
            {"vehicle_instance": "R02-1", "vehicle_type": "V02",
             "placed": [box("SKU0330", "S03", x, 0, 0, 35, 30, 25) for x in (0, 35, 70, 105)],
             "unplaced": [], "volume_utilization": 0.12, "weight_utilization": 0.24},
        ],
    }

    # --- loading_plan（后送先装：R01 先装 S02 再装 S01）---
    loading_plan = {
        "status": "ok",
        "plans": [
            {"vehicle_instance": "R01-1", "delivery_order": ["S01", "S02"],
             "loading_steps": [
                 {"step": 1, "action": "装", "store_id": "S02",
                  "items": [{"sku": "SKU0220", "qty": 4}], "note": "最后送达，最先装入，靠车头位置"},
                 {"step": 2, "action": "装", "store_id": "S01",
                  "items": [{"sku": "SKU0110", "qty": 4}], "note": "最先送达，最后装入，靠车尾位置"},
             ], "blocks_warning": []},
            {"vehicle_instance": "R02-1", "delivery_order": ["S03"],
             "loading_steps": [
                 {"step": 1, "action": "装", "store_id": "S03",
                  "items": [{"sku": "SKU0330", "qty": 4}], "note": "单店路线，一次装完"},
             ], "blocks_warning": []},
        ],
    }

    metrics = {
        "scenario": "pharmacy_chain", "seed": 42,
        "metrics": {
            "vehicle_count": 2, "total_distance_km": 60.5, "total_cost": 398.8,
            "avg_weight_load_rate": 0.3, "avg_volume_utilization": 0.15,
            "on_time_rate": 1.0, "unassigned_order_count": 0,
            "total_item_count": 12, "placed_item_count": 12, "unplaced_item_count": 0,
            "solve_time_sec": 3.2, "peak_memory_mb": 120.5,
        },
        "per_route": [
            {"route_id": "R01", "vehicle_type": "V01", "stop_count": 2, "distance_km": 42.0,
             "total_load_kg": 620.0, "load_rate_weight": 0.36, "cost": 267.0,
             "on_time_stops": 2, "volume_utilization": 0.18},
            {"route_id": "R02", "vehicle_type": "V02", "stop_count": 1, "distance_km": 18.5,
             "total_load_kg": 240.0, "load_rate_weight": 0.24, "cost": 131.8,
             "on_time_stops": 1, "volume_utilization": 0.12},
        ],
    }

    guide_text = {
        "status": "ok",
        "guides": [
            {"vehicle_instance": "R01-1",
             "text": ("第一步：将 S02（连锁药店02）的 1 类共 4 件货物装入车厢最内侧（靠车头），"
                      "作为最后送达的货物。\n"
                      "第二步：将 S01（连锁药店01）的 1 类共 4 件货物装入车厢外侧（靠车尾），"
                      "作为最先送达的货物。")},
            {"vehicle_instance": "R02-1",
             "text": "第一步：将 S03（连锁药店03）的 1 类共 4 件货物装入车厢，单店路线一次装完。"},
        ],
    }

    return {"network": network, "routes": routes, "packing": packing,
            "loading_plan": loading_plan, "metrics": metrics, "guide_text": guide_text}


# --------------------------------------------------------------------- real（读真实 data/）

def build_real_bundle(data_dir: str | Path, n_groups: int = 6) -> dict:
    """读取真实 data/（orders/vehicles/network/rules），组装契约合规的 mock 结果。

    * routes  —— 30 门店按顺序均匀分 6 车次（每车 5 店），车型按组重动态选 V01/V02；
    * packing —— shelf 层叠装箱生成真实不重叠坐标（每辆车最多几十件）；
    * loading_plan —— 按配送顺序倒序装载（后送先装）；
    * metrics —— 调用真实 eval/metrics.py::compute_metrics 计算；
    * guide_text —— 按“第 N 步：装入 Sxx（名称）…… ”模板生成。
    """
    data_dir = Path(data_dir)
    orders = json.loads((data_dir / "orders.json").read_text(encoding="utf-8"))
    vehicles = json.loads((data_dir / "vehicles.json").read_text(encoding="utf-8"))
    network = json.loads((data_dir / "network.json").read_text(encoding="utf-8"))
    rules = json.loads((data_dir / "rules.json").read_text(encoding="utf-8"))

    order_list = orders["orders"]
    node_order = list(network["node_order"])
    idx = {n: i for i, n in enumerate(node_order)}
    dist_m = network["distance_matrix_km"]
    dur_m = network["duration_matrix_min"]
    veh_by_id = {v["vehicle_id"]: v for v in vehicles["vehicles"]}
    orders_by_store = {o["store_id"]: o for o in order_list}

    groups = _chunk(order_list, n_groups)

    route_list, plans, pack_vehicles, guides = [], [], [], []
    for gi, group in enumerate(groups, start=1):
        if not group:
            continue
        route_id = f"R{gi:02d}"
        instance = f"{route_id}-1"
        seq_stores = [o["store_id"] for o in group]
        sequence = ["WH01"] + seq_stores + ["WH01"]

        # ---- 里程 / 时长 / 停靠时刻 ----
        distance = 0.0
        clock = 8 * 60
        stops = []
        load = 0.0
        for prev, cur in zip(sequence, sequence[1:]):
            travel = dur_m[idx[prev]][idx[cur]]
            distance += dist_m[idx[prev]][idx[cur]]
            clock += travel
            if cur == "WH01":
                continue
            order = orders_by_store[cur]
            start = max(clock, int(order["time_window"][0][:2]) * 60 + int(order["time_window"][0][3:]))
            end = start + int(order["service_minutes"])
            load += _order_weight(order)
            stops.append({"node": cur, "arrive": _hhmm(int(start)),
                          "start_service": _hhmm(int(start)), "end_service": _hhmm(int(end)),
                          "load_kg": round(load, 2)})
            clock = end

        # ---- 车型选择（按组重）----
        group_weight = sum(_order_weight(o) for o in group)
        vtype = "V01" if group_weight > 900 else "V02"
        veh = veh_by_id[vtype]
        cost = veh["fixed_cost"] + distance * veh["cost_per_km"]

        route_list.append({
            "route_id": route_id, "vehicle_type": vtype, "sequence": sequence,
            "stop_details": stops,
            "total_distance_km": round(distance, 2),
            "total_duration_min": round(clock - 8 * 60, 1),
            "total_load_kg": round(group_weight, 2),
            "load_rate_weight": round(group_weight / veh["max_weight_kg"], 4),
            "cost": round(cost, 2),
        })

        # ---- 装箱（shelf）----
        L, W, H = CARGO_DIMS[vtype]
        entries = []
        for o in group:
            for it in o["items"]:
                for _ in range(int(it["qty"])):
                    entries.append({"sku": it["sku"], "order_id": o["order_id"],
                                    "store_id": o["store_id"], "l": it["length_cm"],
                                    "w": it["width_cm"], "h": it["height_cm"]})
        entries.sort(key=lambda e: (-e["h"], -e["l"] * e["w"]))  # 大件先放
        placed, unplaced = _shelf_pack(entries, L, W, H)
        vol = sum(e["size_placed_cm"]["l"] * e["size_placed_cm"]["w"] * e["size_placed_cm"]["h"]
                  for e in placed) / (L * W * H)
        pack_vehicles.append({
            "vehicle_instance": instance, "vehicle_type": vtype,
            "placed": placed,
            "unplaced": [{"sku": e["sku"], "order_id": e["order_id"],
                          "store_id": e["store_id"], "qty": 1} for e in unplaced],
            "volume_utilization": round(vol, 4),
            "weight_utilization": round(group_weight / veh["max_weight_kg"], 4),
        })

        # ---- 装载计划（后送先装）----
        steps = []
        for si, sid in enumerate(reversed(seq_stores), start=1):
            o = orders_by_store[sid]
            items = [{"sku": it["sku"], "qty": int(it["qty"])} for it in o["items"]]
            if si == 1:
                note = "最后送达，最先装入，靠车头位置"
            elif si == len(seq_stores):
                note = "最先送达，最后装入，靠车尾位置"
            else:
                note = "按配送顺序倒序装载"
            steps.append({"step": si, "action": "装", "store_id": sid,
                          "items": items, "note": note})
        plans.append({"vehicle_instance": instance, "delivery_order": seq_stores,
                      "loading_steps": steps, "blocks_warning": []})

        # ---- 指引文案 ----
        lines = []
        for st in steps:
            name = orders_by_store[st["store_id"]]["store_name"]
            qty = sum(i["qty"] for i in st["items"])
            lines.append(f"第 {st['step']} 步：将 {st['store_id']}（{name}）的 "
                         f"{len(st['items'])} 类共 {qty} 件货物装入车厢，{st['note']}。")
        guides.append({"vehicle_instance": instance, "text": "\n".join(lines)})

    routes = {
        "status": "ok", "solve_time_sec": 12.3, "peak_memory_mb": 180.2,
        "routes": route_list, "unassigned_orders": [],
        "summary": {
            "vehicle_count": len(route_list),
            "total_distance_km": round(sum(r["total_distance_km"] for r in route_list), 2),
            "total_cost": round(sum(r["cost"] for r in route_list), 2),
        },
    }
    packing = {"status": "ok", "vehicles": pack_vehicles}
    loading_plan = {"status": "ok", "plans": plans}
    guide_text = {"status": "ok", "guides": guides}

    # ---- 真实指标模块计算 metrics ----
    from eval.metrics import compute_metrics  # 延迟导入，避免无依赖环境导入期失败

    metrics = compute_metrics(
        {"routes": route_list, "packing": packing,
         "solve_time_sec": routes["solve_time_sec"],
         "peak_memory_mb": routes["peak_memory_mb"], "unassigned_orders": []},
        orders, vehicles, network,
    )

    # ---- 组装契约 v1.3 件级装载计数（与 run.py 的职责一致）----
    placed_count = sum(len(v.get("placed") or []) for v in pack_vehicles)
    unplaced_count = sum(len(v.get("unplaced") or []) for v in pack_vehicles)
    metrics["metrics"]["total_item_count"] = placed_count + unplaced_count
    metrics["metrics"]["placed_item_count"] = placed_count
    metrics["metrics"]["unplaced_item_count"] = unplaced_count

    return {"network": network, "rules": rules, "routes": routes, "packing": packing,
            "loading_plan": loading_plan, "metrics": metrics, "guide_text": guide_text}
