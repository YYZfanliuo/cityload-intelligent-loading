import json
from pathlib import Path

from solver.packing import solve_packing, _overlap
from solver.routing import solve_routing

ROOT = Path(__file__).resolve().parents[1]

def load(name):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))

def test_packing_constraints():
    orders, vehicles, network, rules = load("orders.json"), load("vehicles.json"), load("network.json"), load("rules.json")
    routes = solve_routing(orders, vehicles, network, rules)
    result = solve_packing(routes, orders, vehicles, rules)
    assert result["status"] == "ok"
    vehicle_types = {v["vehicle_id"]: v for v in vehicles["vehicles"]}
    for packed in result["vehicles"]:
        spec = vehicle_types[packed["vehicle_type"]]
        total = 0
        for i, box in enumerate(packed["placed"]):
            d, p = box["size_placed_cm"], box["position_cm"]
            assert p["x"] + d["l"] <= spec["cargo_length_cm"] + 1e-6
            assert p["y"] + d["w"] <= spec["cargo_width_cm"] + 1e-6
            assert p["z"] + d["h"] <= spec["cargo_height_cm"] + 1e-6
            assert all(not _overlap(box, other) for other in packed["placed"][i+1:])
        assert packed["weight_utilization"] <= 1.0 + 1e-6
