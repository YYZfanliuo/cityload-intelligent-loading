import json
from pathlib import Path

from solver.routing import solve_routing


ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


def test_routing_contract_and_capacity():
    result = solve_routing(_load("orders.json"), _load("vehicles.json"), _load("network.json"), _load("rules.json"))
    assert result["status"] in {"ok", "partial"}
    assert "solve_time_sec" in result and "peak_memory_mb" in result
    assert result["summary"]["vehicle_count"] == len(result["routes"])
    vehicles = {v["vehicle_id"]: v for v in _load("vehicles.json")["vehicles"]}
    for route in result["routes"]:
        assert route["sequence"][0] == "WH01"
        assert route["sequence"][-1] == "WH01"
        assert route["total_load_kg"] <= vehicles[route["vehicle_type"]]["max_weight_kg"] + 1e-6


def test_routing_is_deterministic():
    args = (_load("orders.json"), _load("vehicles.json"), _load("network.json"), _load("rules.json"))
    first = solve_routing(*args)
    second = solve_routing(*args)
    assert first["routes"] == second["routes"]
    assert first["unassigned_orders"] == second["unassigned_orders"]
