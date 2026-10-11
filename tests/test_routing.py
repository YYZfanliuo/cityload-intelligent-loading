import json
from pathlib import Path

import pytest

from solver import routing

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


def _case():
    orders = {"orders": [{"order_id": "O1", "store_id": "S1", "time_window": ["08:00", "09:00"], "service_minutes": 2,
                          "items": [{"qty": 1, "weight_kg": 2, "length_cm": 10, "width_cm": 10, "height_cm": 10}]}]}
    vehicles = {"vehicles": [{"vehicle_id": "V1", "count": 1, "max_weight_kg": 10, "cargo_length_cm": 10,
                              "cargo_width_cm": 10, "cargo_height_cm": 10, "fixed_cost": 5, "cost_per_km": 3, "max_work_minutes": 60}]}
    network = {"node_order": ["WH01", "S1"], "distance_matrix_km": [[0, 1], [1, 0]], "duration_matrix_min": [[0, 1.01], [1.01, 0]]}
    rules = {"routing": {"respect_time_window": True, "allow_overtime_penalty": False, "overtime_penalty_per_min": 2, "unassigned_penalty": 1000}}
    return orders, vehicles, network, rules


def _assert_feasible(result, orders, vehicles, network, rules):
    by_store = {o["store_id"]: o for o in orders["orders"]}
    by_vehicle = {v["vehicle_id"]: v for v in vehicles["vehicles"]}
    idx = {n: i for i, n in enumerate(network["node_order"])}
    visited = []
    for route in result["routes"]:
        vehicle = by_vehicle[route["vehicle_type"]]
        assert route["sequence"][0] == route["sequence"][-1] == "WH01"
        delivered = [by_store[n] for n in route["sequence"][1:-1]]
        visited.extend(o["order_id"] for o in delivered)
        assert sum(routing._order_load(o) for o in delivered) <= vehicle["max_weight_kg"] + 1e-9
        assert sum(routing._order_volume(o) for o in delivered) <= routing._vehicle_volume(vehicle) + 1e-9
        assert route["total_duration_min"] <= vehicle["max_work_minutes"] + .001
        clock = routing.DEPARTURE
        for previous, stop in zip(route["sequence"], route["stop_details"]):
            order = by_store[stop["node"]]
            arrival = round(routing._minutes(stop["arrive"]) * 60)
            start = round(routing._minutes(stop["start_service"]) * 60)
            end = round(routing._minutes(stop["end_service"]) * 60)
            assert stop["arrive"] == stop["start_service"]
            assert len(stop["start_service"]) == 5
            actual_arrival = clock + routing._travel(network["duration_matrix_min"][idx[previous]][idx[stop["node"]]])
            actual_start = max(actual_arrival, round(routing._minutes(order["time_window"][0]) * 60)) if rules["routing"]["respect_time_window"] else actual_arrival
            assert start <= actual_start < start + 60
            actual_end = actual_start + routing._travel(order["service_minutes"])
            assert end <= actual_end < end + 60
            if rules["routing"]["respect_time_window"]:
                assert start >= routing._minutes(order["time_window"][0]) * 60
                if not rules["routing"]["allow_overtime_penalty"]:
                    assert start <= routing._minutes(order["time_window"][1]) * 60
            clock = actual_end
        clock += routing._travel(network["duration_matrix_min"][idx[route["sequence"][-2]]][idx["WH01"]])
        assert route["total_duration_min"] == pytest.approx((clock - routing.DEPARTURE) / 60, abs=.001)
    assert len(visited) == len(set(visited))
    assert not set(visited) & set(result["unassigned_orders"])
    assert sorted(visited + result["unassigned_orders"]) == sorted(o["order_id"] for o in orders["orders"])
    assert result["status"] == ("partial" if result["unassigned_orders"] else "ok")


def test_real_data_constraints_and_objective_stability():
    args = (_load("orders.json"), _load("vehicles.json"), _load("network.json"), _load("rules.json"))
    results = [routing.solve_routing(*args) for _ in range(2)]
    for result in results:
        _assert_feasible(result, *args)
        assert result["solve_time_sec"] < 60
        assert result["peak_memory_mb"] > 0
        assert result["summary"]["vehicle_count"] == len(result["routes"])
    # 限时局部搜索不保证精确序列相同，只验收约束、订单守恒与费用质量稳定。
    costs = [r["summary"]["total_cost"] for r in results]
    assert max(costs) <= min(costs) * 1.1


def test_hand_computed_fixed_and_vehicle_cost():
    args = _case()
    expensive = dict(args[1]["vehicles"][0], vehicle_id="V2", fixed_cost=0, cost_per_km=10)
    args[1]["vehicles"].insert(0, expensive)
    result = routing.solve_routing(*args)
    _assert_feasible(result, *args)
    assert result["routes"][0]["vehicle_type"] == "V1"
    assert result["summary"]["total_cost"] == 11
    assert result["routes"][0]["stop_details"][0]["start_service"] == "08:01"


@pytest.mark.parametrize("fallback", [False, True])
def test_capacity_work_windows_and_empty_cases(monkeypatch, fallback):
    if fallback:
        monkeypatch.setattr(routing, "pywrapcp", None)
    for failure in ("weight", "volume", "work", "window", "fleet", "empty"):
        args = _case()
        if failure == "weight":
            args[1]["vehicles"][0]["max_weight_kg"] = 1
        elif failure == "volume":
            args[1]["vehicles"][0]["cargo_height_cm"] = 9
        elif failure == "work":
            args[1]["vehicles"].append(dict(args[1]["vehicles"][0], vehicle_id="V2", count=0, max_work_minutes=600))
            args[1]["vehicles"][0]["max_work_minutes"] = 4
        elif failure == "window":
            args[0]["orders"][0]["time_window"] = ["07:00", "08:01"]
        elif failure == "fleet":
            args[1]["vehicles"][0]["count"] = 0
        else:
            args[0]["orders"] = []
        result = routing.solve_routing(*args)
        _assert_feasible(result, *args)
        assert result["routes"] == []
        assert result["unassigned_orders"] == ([] if failure == "empty" else ["O1"])
        if fallback:
            assert result["solver_backend"] == "heuristic"
            assert result["fallback_reason"] == "ortools_unavailable"


@pytest.mark.parametrize("fallback", [False, True])
def test_soft_window_and_window_disabled(monkeypatch, fallback):
    if fallback:
        monkeypatch.setattr(routing, "pywrapcp", None)
    args = _case()
    args[0]["orders"][0]["time_window"] = ["07:00", "08:01"]
    args[3]["routing"]["allow_overtime_penalty"] = True
    result = routing.solve_routing(*args)
    _assert_feasible(result, *args)
    assert result["summary"]["total_cost"] == pytest.approx(11 + 2 / 60, abs=.001)
    args[3]["routing"]["respect_time_window"] = False
    args[0]["orders"][0]["time_window"] = ["20:00", "21:00"]
    result = routing.solve_routing(*args)
    _assert_feasible(result, *args)
    assert result["summary"]["total_cost"] == 11


@pytest.mark.parametrize("fallback", [False, True])
def test_waiting_is_included_in_per_vehicle_work_limit(monkeypatch, fallback):
    if fallback:
        monkeypatch.setattr(routing, "pywrapcp", None)
    args = _case()
    args[0]["orders"][0]["time_window"] = ["10:00", "11:00"]
    result = routing.solve_routing(*args)
    _assert_feasible(result, *args)
    assert result["unassigned_orders"] == ["O1"]
    args[1]["vehicles"][0]["max_work_minutes"] = 180
    result = routing.solve_routing(*args)
    _assert_feasible(result, *args)
    assert result["routes"][0]["stop_details"][0]["start_service"] == "10:00"
    assert result["routes"][0]["total_duration_min"] == pytest.approx(123 + 1 / 60, abs=.001)


def test_each_vehicle_has_its_own_work_limit():
    args = _case()
    args[1]["vehicles"][0]["max_work_minutes"] = 4
    args[1]["vehicles"].append(dict(args[1]["vehicles"][0], vehicle_id="V2", max_work_minutes=60, fixed_cost=20))
    result = routing.solve_routing(*args)
    _assert_feasible(result, *args)
    assert result["routes"][0]["vehicle_type"] == "V2"
    assert result["summary"]["total_cost"] == 26


def test_fixed_fee_can_prefer_unassigned():
    args = _case()
    args[1]["vehicles"][0]["fixed_cost"] = 2000
    result = routing.solve_routing(*args)
    if routing.pywrapcp is None:
        pytest.skip("货币最优化需要OR-Tools；回退只保证约束")
    assert result["routes"] == []
    assert result["summary"]["total_cost"] == 1000


def test_subset_orders_and_reordered_warehouse_are_supported(monkeypatch):
    monkeypatch.setattr(routing, "pywrapcp", None)
    args = _case()
    args[2]["node_order"] = ["UNUSED", "S1", "WH01"]
    args[2]["distance_matrix_km"] = [[0, 9, 9], [9, 0, 1], [9, 1, 0]]
    args[2]["duration_matrix_min"] = [[0, 9, 9], [9, 0, 1.01], [9, 1.01, 0]]
    result = routing.solve_routing(*args)
    _assert_feasible(result, *args)
    assert result["summary"]["total_cost"] == 11


def test_programming_errors_are_not_silently_hidden(monkeypatch):
    if routing.pywrapcp is None:
        pytest.skip("本测试需要进入OR-Tools分支")
    def broken(*args):
        raise RuntimeError("测试异常")
    monkeypatch.setattr(routing, "_ortools_solve", broken)
    with pytest.raises(RuntimeError, match="测试异常"):
        routing.solve_routing(*_case())
