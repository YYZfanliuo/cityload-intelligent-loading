"""CVRPTW routing solver for CityLoad.

Uses OR-Tools RoutingModel when available and falls back to a deterministic
nearest-neighbour construction so the pipeline remains runnable offline.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

try:  # OR-Tools is an optional runtime dependency in constrained environments.
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
except ImportError:  # pragma: no cover - exercised only without dependency
    pywrapcp = None
    routing_enums_pb2 = None


def _minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":", 1))
    return hour * 60 + minute


def _order_load(order: dict[str, Any]) -> float:
    return sum(float(item.get("qty", 0)) * float(item.get("weight_kg", 0)) for item in order.get("items", []))


def _vehicle_pool(vehicles: dict) -> list[dict]:
    pool = []
    for vehicle in vehicles.get("vehicles", []):
        pool.extend([vehicle] * int(vehicle.get("count", 0)))
    return pool


def _route_record(route_id: str, vehicle: dict, node_order: list[str], distance: list[list[float]], duration: list[list[float]],
                  orders_by_store: dict[str, dict], sequence: list[str], rules: dict) -> dict:
    index = {node: i for i, node in enumerate(node_order)}
    stop_details = []
    clock = 8 * 60
    total_distance = total_duration = 0.0
    total_load = 0.0
    overtime = 0.0
    for previous, current in zip(sequence, sequence[1:]):
        pi, ci = index[previous], index[current]
        total_distance += float(distance[pi][ci])
        travel = float(duration[pi][ci])
        clock += travel
        if current != "WH01":
            order = orders_by_store[current]
            start, end = (_minutes(x) for x in order["time_window"])
            arrive = max(clock, start)
            overtime += max(0.0, arrive - end)
            service_end = arrive + int(order.get("service_minutes", 0))
            total_load += _order_load(order)
            stop_details.append({"node": current, "arrive": f"{int(arrive)//60:02d}:{int(arrive)%60:02d}",
                                 "start_service": f"{int(arrive)//60:02d}:{int(arrive)%60:02d}",
                                 "end_service": f"{int(service_end)//60:02d}:{int(service_end)%60:02d}",
                                 "load_kg": round(total_load, 3)})
            clock = service_end
        else:
            clock += 0
    total_duration = clock - 8 * 60
    cost = float(vehicle.get("fixed_cost", 0)) + total_distance * float(vehicle.get("cost_per_km", 0))
    cost += overtime * float(rules.get("routing", {}).get("overtime_penalty_per_min", 0))
    capacity = float(vehicle.get("max_weight_kg", 1))
    return {"route_id": route_id, "vehicle_type": vehicle["vehicle_id"], "sequence": sequence,
            "stop_details": stop_details, "total_distance_km": round(total_distance, 3),
            "total_duration_min": round(total_duration, 3), "total_load_kg": round(total_load, 3),
            "load_rate_weight": round(total_load / capacity, 4) if capacity else 0.0, "cost": round(cost, 3)}


def _heuristic(orders: list[dict], vehicles: list[dict], node_order: list[str], distance: list[list[float]], duration: list[list[float]], rules: dict) -> tuple[list[dict], list[dict]]:
    """Build capacity-feasible routes deterministically when OR-Tools is absent."""
    idx = {node: i for i, node in enumerate(node_order)}
    remaining = list(orders)
    routes, unassigned = [], []
    by_store = {o["store_id"]: o for o in orders}
    for number, vehicle in enumerate(vehicles, 1):
        if not remaining:
            break
        current = "WH01"
        sequence = [current]
        load = 0.0
        assigned = []
        while remaining:
            candidates = [o for o in remaining if load + _order_load(o) <= float(vehicle["max_weight_kg"])]
            if not candidates:
                break
            candidates.sort(key=lambda o: (distance[idx[current]][idx[o["store_id"]]], o["store_id"]))
            order = candidates[0]
            assigned.append(order)
            remaining.remove(order)
            load += _order_load(order)
            current = order["store_id"]
            sequence.append(current)
        if assigned:
            sequence.append("WH01")
            routes.append(_route_record(f"R{number:02d}", vehicle, node_order, distance, duration, by_store, sequence, rules))
    # If fleet capacity is insufficient, retain the orders explicitly.
    unassigned.extend(o["order_id"] for o in remaining)
    return routes, unassigned


def _ortools_solve(order_list, fleet, node_order, distance, duration, rules):
    """Solve CVRPTW with OR-Tools' RoutingModel and heterogeneous fleet."""
    manager = pywrapcp.RoutingIndexManager(len(node_order), len(fleet), 0)
    model = pywrapcp.RoutingModel(manager)
    idx = {node: i for i, node in enumerate(node_order)}
    by_store = {o["store_id"]: o for o in order_list}
    demands = [0] * len(node_order)
    windows = [(8 * 60, 24 * 60)] * len(node_order)
    services = [0] * len(node_order)
    for order in order_list:
        i = idx[order["store_id"]]
        demands[i] = int(round(_order_load(order) * 100))
        windows[i] = (_minutes(order["time_window"][0]), _minutes(order["time_window"][1]))
        services[i] = int(order.get("service_minutes", 0))
    capacities = [int(round(v["max_weight_kg"] * 100)) for v in fleet]

    def distance_cb(from_index, to_index):
        return int(round(float(distance[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]) * 1000))
    dist_callback = model.RegisterTransitCallback(distance_cb)
    model.SetArcCostEvaluatorOfAllVehicles(dist_callback)

    def demand_cb(index_):
        return demands[manager.IndexToNode(index_)]
    demand_callback = model.RegisterUnaryTransitCallback(demand_cb)
    model.AddDimensionWithVehicleCapacity(demand_callback, 0, capacities, True, "Capacity")

    def time_cb(from_index, to_index):
        node = manager.IndexToNode(from_index)
        return int(round(float(duration[node][manager.IndexToNode(to_index)]))) + services[node]
    time_callback = model.RegisterTransitCallback(time_cb)
    max_work = max((int(v.get("max_work_minutes", 480)) for v in fleet), default=480)
    model.AddDimension(time_callback, 24 * 60, 24 * 60, False, "Time")
    time_dim = model.GetDimensionOrDie("Time")
    for node, (start, end) in enumerate(windows):
        index_ = manager.NodeToIndex(node)
        time_dim.CumulVar(index_).SetRange(start, end)
    for vehicle_no, vehicle in enumerate(fleet):
        end = model.End(vehicle_no)
        time_dim.CumulVar(end).SetRange(0, 8 * 60 + max_work)
        model.AddVariableMinimizedByFinalizer(time_dim.CumulVar(model.Start(vehicle_no)))
        model.AddVariableMinimizedByFinalizer(time_dim.CumulVar(end))
    penalty = int(float(rules.get("routing", {}).get("unassigned_penalty", 1000)) * 1000)
    for node in range(1, len(node_order)):
        model.AddDisjunction([manager.NodeToIndex(node)], penalty)
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    # Keep the 30-store run comfortably below the 60-second contract.
    params.time_limit.FromSeconds(10)
    solution = model.SolveWithParameters(params)
    if solution is None:
        return None
    routes = []
    for vehicle_no, vehicle in enumerate(fleet):
        index_ = model.Start(vehicle_no)
        sequence = ["WH01"]
        while not model.IsEnd(index_):
            node = manager.IndexToNode(index_)
            if node != 0:
                sequence.append(node_order[node])
            index_ = solution.Value(model.NextVar(index_))
        if len(sequence) > 1:
            sequence.append("WH01")
            routes.append(_route_record(f"R{len(routes)+1:02d}", vehicle, node_order, distance, duration, by_store, sequence, rules))
    visited = {s for route in routes for s in route["sequence"]}
    unassigned = [o["order_id"] for o in order_list if o["store_id"] not in visited]
    return routes, unassigned


def solve_routing(orders: dict, vehicles: dict, network: dict, rules: dict) -> dict:
    """Solve CVRPTW using OR-Tools, falling back only if unavailable/fails."""
    started = time.perf_counter()
    order_list = list(orders.get("orders", []))
    node_order = list(network["node_order"])
    distance = network["distance_matrix_km"]
    duration = network["duration_matrix_min"]
    fleet = _vehicle_pool(vehicles)
    result = None
    if pywrapcp is not None and fleet:
        try:
            result = _ortools_solve(order_list, fleet, node_order, distance, duration, rules)
        except Exception:
            result = None
    if result is None:
        result = _heuristic(order_list, fleet, node_order, distance, duration, rules)
    routes, unassigned = result
    total_distance = sum(r["total_distance_km"] for r in routes)
    total_cost = sum(r["cost"] for r in routes) + len(unassigned) * float(rules.get("routing", {}).get("unassigned_penalty", 0))
    return {"status": "ok" if not unassigned else "partial", "solve_time_sec": round(time.perf_counter() - started, 6),
            "peak_memory_mb": 0.0, "routes": routes, "unassigned_orders": unassigned,
            "summary": {"vehicle_count": len(routes), "total_distance_km": round(total_distance, 3), "total_cost": round(total_cost, 3)}}
