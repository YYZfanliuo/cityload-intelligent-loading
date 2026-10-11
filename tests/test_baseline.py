import json
from pathlib import Path

from solver.baseline import solve_baseline

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


def test_baseline_contract_and_conservation():
    orders, vehicles, network, rules = (load(n) for n in ("orders.json", "vehicles.json", "network.json", "rules.json"))
    result = solve_baseline(orders, vehicles, network, rules)
    assert {"routes", "packing", "loading_plan"} <= result.keys()
    routes = result["routes"]
    assert "solve_time_sec" in result and "peak_memory_mb" in result
    assigned = {s for r in routes["routes"] for s in r["sequence"][1:-1]}
    assert assigned <= {o["store_id"] for o in orders["orders"]}
    expected = sum(i["qty"] for o in orders["orders"] for i in o["items"] if o["store_id"] in assigned)
    packed = result["packing"]
    actual = sum(len(v["placed"]) + sum(x["qty"] for x in v["unplaced"]) for v in packed["vehicles"])
    assert actual == expected
    from tests.test_packing import validate
    validate(packed, routes, orders, vehicles, rules)
    assert result["peak_memory_mb"] > 0
    for route in routes["routes"]:
        assert route["sequence"][1:-1] == sorted(route["sequence"][1:-1])
        spec = next(v for v in vehicles["vehicles"] if v["vehicle_id"] == route["vehicle_type"])
        assert route["total_load_kg"] <= spec["max_weight_kg"] + 1e-6
        assert route["total_duration_min"] <= spec["max_work_minutes"] + 1e-6


def test_baseline_is_deterministic():
    data = [load(n) for n in ("orders.json", "vehicles.json", "network.json", "rules.json")]
    a = solve_baseline(*data)
    b = solve_baseline(*data)
    assert a["routes"]["routes"] == b["routes"]["routes"]
    assert a["packing"] == b["packing"]


def test_sequential_pack_respects_order_and_fragile():
    from solver.baseline import _sequential_pack
    vehicle = {"cargo_length_cm": 10, "cargo_width_cm": 10, "cargo_height_cm": 30, "max_weight_kg": 10}
    base = {"order_id": "O1", "store_id": "S1", "qty": 1, "weight_kg": 1, "length_cm": 10, "width_cm": 10, "height_cm": 10, "max_stack_layers": 3, "stackable": True, "fragile": True}
    placed, unplaced = _sequential_pack([{**base, "sku": "first"}, {**base, "sku": "second", "fragile": False}], vehicle, {"packing": {"allow_rotation": False}})
    assert [b["sku"] for b in placed] == ["first"]
    assert [b["sku"] for b in unplaced] == ["second"]


def test_schedule_hard_window_and_work():
    from solver.baseline import _schedule
    orders = {"S1": {"time_window": ["08:00", "08:01"], "service_minutes": 2}}
    idx = {"WH01": 0, "S1": 1}
    duration = [[0, 2], [2, 0]]
    assert not _schedule(["S1"], orders, idx, duration, {"max_work_minutes": 60}, {"routing": {"respect_time_window": True, "allow_overtime_penalty": False}})
    assert _schedule(["S1"], orders, idx, duration, {"max_work_minutes": 60}, {"routing": {"respect_time_window": False}})
    assert not _schedule(["S1"], orders, idx, duration, {"max_work_minutes": 5}, {"routing": {"respect_time_window": False}})
