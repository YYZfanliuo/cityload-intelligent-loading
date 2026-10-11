"""异构车队 CVRPTW；费用以人民币计，容量和工时始终为硬约束。"""
from __future__ import annotations

import math
import os
import time
from typing import Any

try:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
except ImportError:  # 可选求解依赖缺失时使用保守构造
    pywrapcp = None
    routing_enums_pb2 = None

DEPARTURE = 8 * 3600
MONEY_SCALE = 60000


def _minutes(value: str) -> float:
    """解析分钟或秒精度时刻，返回分钟。"""
    parts = [int(part) for part in value.split(":")]
    return parts[0] * 60 + parts[1] + (parts[2] / 60 if len(parts) == 3 else 0)


def _stamp(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}"


def _order_load(order: dict[str, Any]) -> float:
    return sum(float(i.get("qty", 0)) * float(i.get("weight_kg", 0)) for i in order.get("items", []))


def _order_volume(order: dict) -> float:
    return sum(float(i.get("qty", 0)) * float(i.get("length_cm", 0)) * float(i.get("width_cm", 0)) * float(i.get("height_cm", 0)) for i in order.get("items", []))


def _vehicle_volume(vehicle: dict) -> float:
    return float(vehicle["cargo_length_cm"]) * float(vehicle["cargo_width_cm"]) * float(vehicle["cargo_height_cm"])


def _vehicle_pool(vehicles: dict) -> list[dict]:
    return [v for v in vehicles.get("vehicles", []) for _ in range(int(v.get("count", 0)))]


def _travel(minutes: float) -> int:
    """向上取整到秒，避免离散化把不可行路线误判可行。"""
    return math.ceil(float(minutes) * 60 - 1e-9)


def _route_record(route_id, vehicle, node_order, distance, duration, orders_by_store, sequence, rules, service_starts=None, end_time=None):
    """内部保留秒精度；按冻结接口截断显示 HH:MM，arrive 为服务开始。"""
    index = {node: i for i, node in enumerate(node_order)}
    details = []
    clock = DEPARTURE
    total_distance = load = overtime = 0.0
    routing = rules.get("routing", {})
    for previous, current in zip(sequence, sequence[1:]):
        pi, ci = index[previous], index[current]
        total_distance += float(distance[pi][ci])
        clock += _travel(duration[pi][ci])
        if current != "WH01":
            order = orders_by_store[current]
            earliest, latest = (round(_minutes(t) * 60) for t in order["time_window"])
            start = service_starts[current] if service_starts is not None else max(clock, earliest) if routing.get("respect_time_window", True) else clock
            if routing.get("respect_time_window", True) and routing.get("allow_overtime_penalty", False):
                overtime += max(0, start - latest) / 60
            clock = start + _travel(order.get("service_minutes", 0))
            load += _order_load(order)
            details.append({"node": current, "arrive": _stamp(start), "start_service": _stamp(start), "end_service": _stamp(clock), "load_kg": round(load, 3)})
    if end_time is not None:
        clock = end_time
    cost = float(vehicle.get("fixed_cost", 0)) + total_distance * float(vehicle.get("cost_per_km", 0)) + overtime * float(routing.get("overtime_penalty_per_min", 0))
    capacity = float(vehicle["max_weight_kg"])
    return {"route_id": route_id, "vehicle_type": vehicle["vehicle_id"], "sequence": sequence, "stop_details": details,
            "total_distance_km": round(total_distance, 3), "total_duration_min": round((clock - DEPARTURE) / 60, 3),
            "total_load_kg": round(load, 3), "load_rate_weight": round(load / capacity, 4) if capacity else 0.0, "cost": round(cost, 3)}


def _heuristic(orders, vehicles, node_order, distance, duration, rules):
    """保守回退：每次加入订单都检查双容量、窗口及返仓工时。"""
    idx = {node: i for i, node in enumerate(node_order)}
    remaining = list(orders)
    routes = []
    by_store = {o["store_id"]: o for o in orders}
    routing = rules.get("routing", {})
    for vehicle in vehicles:
        sequence = ["WH01"]
        load = volume = 0.0
        clock = DEPARTURE
        while remaining:
            candidates = []
            for order in remaining:
                if load + _order_load(order) > float(vehicle["max_weight_kg"]) + 1e-9 or volume + _order_volume(order) > _vehicle_volume(vehicle) + 1e-9:
                    continue
                node = order["store_id"]
                start = clock + _travel(duration[idx[sequence[-1]]][idx[node]])
                if routing.get("respect_time_window", True):
                    start = max(start, round(_minutes(order["time_window"][0]) * 60))
                    if not routing.get("allow_overtime_penalty", False) and start > round(_minutes(order["time_window"][1]) * 60):
                        continue
                end = start + _travel(order.get("service_minutes", 0))
                if end + _travel(duration[idx[node]][idx["WH01"]]) > DEPARTURE + math.floor(float(vehicle.get("max_work_minutes", 480)) * 60):
                    continue
                candidates.append((distance[idx[sequence[-1]]][idx[node]], node, end, order))
            if not candidates:
                break
            _, node, clock, order = min(candidates, key=lambda c: (c[0], c[1]))
            remaining.remove(order)
            load += _order_load(order)
            volume += _order_volume(order)
            sequence.append(node)
        if len(sequence) > 1:
            sequence.append("WH01")
            routes.append(_route_record(f"R{len(routes)+1:02d}", vehicle, node_order, distance, duration, by_store, sequence, rules))
    return routes, [o["order_id"] for o in remaining]


def _ortools_solve(order_list, fleet, node_order, distance, duration, rules):
    """以相同人民币缩放优化固定费、车型里程费、迟到费和弃单费。"""
    manager = pywrapcp.RoutingIndexManager(len(node_order), len(fleet), 0)
    model = pywrapcp.RoutingModel(manager)
    by_store = {o["store_id"]: o for o in order_list}
    services = [0] + [_travel(by_store[n].get("service_minutes", 0)) for n in node_order[1:]]
    for vehicle_no, vehicle in enumerate(fleet):
        rate = float(vehicle.get("cost_per_km", 0))
        def cost_cb(fi, ti, rate=rate):
            return round(float(distance[manager.IndexToNode(fi)][manager.IndexToNode(ti)]) * rate * MONEY_SCALE)
        callback = model.RegisterTransitCallback(cost_cb)
        model.SetArcCostEvaluatorOfVehicle(callback, vehicle_no)
        model.SetFixedCostOfVehicle(round(float(vehicle.get("fixed_cost", 0)) * MONEY_SCALE), vehicle_no)
    for name, demands, capacities in (
        ("Weight", [0] + [math.ceil(_order_load(by_store[n]) * 1000 - 1e-9) for n in node_order[1:]], [math.floor(float(v["max_weight_kg"]) * 1000 + 1e-9) for v in fleet]),
        ("Volume", [0] + [math.ceil(_order_volume(by_store[n]) - 1e-9) for n in node_order[1:]], [math.floor(_vehicle_volume(v) + 1e-9) for v in fleet]),
    ):
        def demand_cb(index, demands=demands):
            return demands[manager.IndexToNode(index)]
        model.AddDimensionWithVehicleCapacity(model.RegisterUnaryTransitCallback(demand_cb), 0, capacities, True, name)
    def time_cb(fi, ti):
        node = manager.IndexToNode(fi)
        return _travel(duration[node][manager.IndexToNode(ti)]) + services[node]
    horizon = max(DEPARTURE + max(_travel(v.get("max_work_minutes", 480)) for v in fleet), max(round(_minutes(t) * 60) for o in order_list for t in o["time_window"]))
    model.AddDimension(model.RegisterTransitCallback(time_cb), horizon, horizon, False, "Time")
    dimension = model.GetDimensionOrDie("Time")
    routing = rules.get("routing", {})
    for node in range(1, len(node_order)):
        index = manager.NodeToIndex(node)
        if routing.get("respect_time_window", True):
            earliest, latest = (round(_minutes(t) * 60) for t in by_store[node_order[node]]["time_window"])
            if routing.get("allow_overtime_penalty", False):
                dimension.CumulVar(index).SetRange(earliest, max(horizon, earliest))
                dimension.SetCumulVarSoftUpperBound(index, latest, round(float(routing.get("overtime_penalty_per_min", 0)) * MONEY_SCALE / 60))
            else:
                dimension.CumulVar(index).SetRange(earliest, latest)
        model.AddDisjunction([index], round(float(routing.get("unassigned_penalty", 1000)) * MONEY_SCALE))
    for number, vehicle in enumerate(fleet):
        start, end = model.Start(number), model.End(number)
        dimension.CumulVar(start).SetValue(DEPARTURE)
        model.solver().Add(dimension.CumulVar(end) - dimension.CumulVar(start) <= math.floor(float(vehicle.get("max_work_minutes", 480)) * 60))
        model.AddVariableMinimizedByFinalizer(dimension.CumulVar(end))
    # 固定路径下最早开始服务，输出与罚费一致。
    for node in range(1, len(node_order)):
        model.AddVariableMinimizedByFinalizer(dimension.CumulVar(manager.NodeToIndex(node)))
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(10)
    solution = model.SolveWithParameters(params)
    if solution is None:
        return None
    routes = []
    for number, vehicle in enumerate(fleet):
        index = model.Start(number)
        sequence, starts = ["WH01"], {}
        while not model.IsEnd(index):
            node = manager.IndexToNode(index)
            if node:
                sequence.append(node_order[node])
                starts[node_order[node]] = solution.Value(dimension.CumulVar(index))
            index = solution.Value(model.NextVar(index))
        if len(sequence) > 1:
            sequence.append("WH01")
            routes.append(_route_record(f"R{len(routes)+1:02d}", vehicle, node_order, distance, duration, by_store, sequence, rules, starts, solution.Value(dimension.CumulVar(index))))
    visited = {s for route in routes for s in route["sequence"]}
    return routes, [o["order_id"] for o in order_list if o["store_id"] not in visited]


def _peak_memory_mb():
    """进程生命周期原生常驻内存峰值，含 OR-Tools；不是单次求解增量。"""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.PeakWorkingSetSize / (1024 * 1024)
    import resource
    import sys
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024 if sys.platform == "darwin" else 1024)


def solve_routing(orders: dict, vehicles: dict, network: dict, rules: dict) -> dict:
    """固定 08:00 出发；仅依赖缺失或无解时安全回退，不吞编程异常。"""
    started = time.perf_counter()
    order_list = list(orders.get("orders", []))
    if len({o["store_id"] for o in order_list}) != len(order_list):
        raise ValueError("store_id 必须唯一")
    original = network["node_order"]
    node_order = ["WH01"] + [o["store_id"] for o in order_list]
    indices = [original.index(n) for n in node_order]
    distance = [[network["distance_matrix_km"][i][j] for j in indices] for i in indices]
    duration = [[network["duration_matrix_min"][i][j] for j in indices] for i in indices]
    fleet = _vehicle_pool(vehicles)
    result = None
    reason = "ortools_unavailable" if pywrapcp is None else "empty_fleet" if not fleet else "empty_orders" if not order_list else None
    if reason is None:
        result = _ortools_solve(order_list, fleet, node_order, distance, duration, rules)
        if result is None:
            reason = "no_solution"
    if result is None:
        result = _heuristic(order_list, fleet, node_order, distance, duration, rules)
    routes, unassigned = result
    total_cost = sum(r["cost"] for r in routes) + len(unassigned) * float(rules.get("routing", {}).get("unassigned_penalty", 1000))
    return {"status": "ok" if not unassigned else "partial", "solve_time_sec": round(time.perf_counter() - started, 6),
            "peak_memory_mb": round(_peak_memory_mb(), 3), "peak_memory_scope": "process_lifetime_peak_working_set",
            "solver_backend": "ortools" if reason is None else "heuristic", "fallback_reason": reason,
            "routes": routes, "unassigned_orders": unassigned,
            "summary": {"vehicle_count": len(routes), "total_distance_km": round(sum(r["total_distance_km"] for r in routes), 3), "total_cost": round(total_cost, 3)}}
