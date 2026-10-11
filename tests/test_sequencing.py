"""装载步骤守恒、路线匹配及真实几何阻挡测试。"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from solver.sequencing import sequence_loading


def box(store, x=0, y=0, z=0, l=10, w=10, h=10, sku="SKU01"):
    return {"sku": sku, "order_id": "O01", "store_id": store,
            "position_cm": {"x": x, "y": y, "z": z},
            "size_placed_cm": {"l": l, "w": w, "h": h},
            "layer": 1 + int(z / h), "rotated": False}


def route(route_id="R01", stores=("S01", "S02", "S03")):
    return {"route_id": route_id, "sequence": ["WH01", *stores, "WH01"]}


def vehicle(placed, instance="R01-1", unplaced=None):
    return {"vehicle_instance": instance, "vehicle_type": "V01",
            "placed": placed, "unplaced": unplaced or []}


def plan(placed, stores=("S01", "S02", "S03")):
    return sequence_loading({"vehicles": [vehicle(placed)]},
                            {"routes": [route(stores=stores)]}, {})["plans"][0]


def test_reverse_steps_sku_counts_and_unplaced_excluded():
    placed = [box("S01", x=0), box("S02", x=10), box("S03", x=20),
              box("S03", x=20, y=10), box("S03", x=20, y=20, sku="SKU02")]
    unplaced = [{"store_id": "S03", "sku": "SKU01", "qty": 7}]
    packing = {"vehicles": [vehicle(placed, unplaced=unplaced)]}
    result = sequence_loading(packing, {"routes": [route()]}, {})
    steps = result["plans"][0]["loading_steps"]
    assert [s["store_id"] for s in steps] == ["S03", "S02", "S01"]
    assert [s["step"] for s in steps] == [1, 2, 3]
    assert steps[0]["items"] == [{"sku": "SKU01", "qty": 2}, {"sku": "SKU02", "qty": 1}]
    assert sum(item["qty"] for step in steps for item in step["items"]) == len(placed)
    assert result["plans"][0]["blocks_warning"] == []
    assert packing["vehicles"][0]["unplaced"] == unplaced


def test_missing_placed_store_has_no_invented_step():
    result = plan([box("S03")])
    assert result["delivery_order"] == ["S01", "S02", "S03"]
    assert [s["store_id"] for s in result["loading_steps"]] == ["S03"]


def test_multiple_routes_and_instances_match_id_not_vehicle_type():
    packing = {"vehicles": [vehicle([box("S04")], "R02-1"),
                            vehicle([box("S02")], "R01-2"),
                            vehicle([box("S01")], "R01-1")]}
    routes = {"routes": [route(), route("R02", ("S04", "S05"))]}
    plans = sequence_loading(packing, routes, {})["plans"]
    assert [p["vehicle_instance"] for p in plans] == ["R02-1", "R01-2", "R01-1"]
    assert plans[0]["delivery_order"] == ["S04", "S05"]
    assert plans[1]["delivery_order"] == plans[2]["delivery_order"] == ["S01", "S02", "S03"]


def test_empty_data_and_empty_vehicle():
    assert sequence_loading({}, {}, {}) == {"status": "ok", "plans": []}
    result = plan([])
    assert result["loading_steps"] == result["blocks_warning"] == []
    assert result["delivery_order"] == ["S01", "S02", "S03"]


def test_unmatched_route_and_store_fail_explicitly():
    with pytest.raises(ValueError, match="route_id"):
        sequence_loading({"vehicles": [vehicle([])]}, {}, {})
    with pytest.raises(ValueError, match="不在路线"):
        plan([box("UNKNOWN")])


def test_vertical_late_store_on_top_is_real_warning_and_no_unsafe_repair():
    placed = [box("S01"), box("S02", z=10)]
    original = deepcopy(placed)
    result = plan(placed, ("S01", "S02"))
    assert any("上下压" in warning for warning in result["blocks_warning"])
    assert placed == original


def test_earlier_store_on_top_is_unblocked():
    placed = [box("S02"), box("S01", z=10)]
    assert plan(placed, ("S01", "S02"))["blocks_warning"] == []


def test_x_mirror_repairs_reversed_channel_and_preserves_support_and_dimensions():
    placed = [box("S02", x=3, l=20), box("S02", x=8, z=10),
              box("S01", x=23, l=10)]
    original = deepcopy(placed)
    assert plan(placed, ("S01", "S02"))["blocks_warning"] == []
    assert [b["position_cm"]["x"] for b in placed] == [13, 18, 3]
    assert placed[0]["position_cm"]["x"] <= placed[1]["position_cm"]["x"]
    assert placed[1]["position_cm"]["x"] + 10 <= placed[0]["position_cm"]["x"] + 20
    for before, after in zip(original, placed):
        assert before["size_placed_cm"] == after["size_placed_cm"]
        assert before["position_cm"]["z"] == after["position_cm"]["z"]
        assert before["layer"] == after["layer"]


def test_repeated_sequence_is_idempotent_and_cloned_inputs_are_isolated():
    source = {"vehicles": [vehicle([box("S02"), box("S01", x=10)])]}
    routes = {"routes": [route(stores=("S01", "S02"))]}
    snapshot = deepcopy((source, routes))
    first, second = deepcopy(source), deepcopy(source)
    result = sequence_loading(first, routes, {})
    assert first != source  # 保留生产接口的就地修复行为。
    assert sequence_loading(first, routes, {}) == result
    assert sequence_loading(second, routes, {}) == result
    assert first == second
    assert (source, routes) == snapshot
    first["vehicles"][0]["placed"][0]["position_cm"]["x"] = 999
    assert first != second and (source, routes) == snapshot


def test_float_mirror_rejects_loss_of_strict_complete_support():
    x, length, width = 5.592453147881197, 8.37739611104678, 1.4497063821020266
    placed = [box("S02", x=x, l=length), box("S02", x=x, z=10, l=width),
              box("S01", x=x + length + 1)]
    original = deepcopy(placed)
    mirrored = deepcopy(placed)
    right = x + length + 11
    for candidate in mirrored:
        candidate["position_cm"]["x"] = x + right - candidate["position_cm"]["x"] - candidate["size_placed_cm"]["l"]
    assert all(x <= b["position_cm"]["x"] and
               b["position_cm"]["x"] + b["size_placed_cm"]["l"] <= right for b in mirrored)
    assert mirrored[2]["position_cm"]["x"] + 10 <= mirrored[0]["position_cm"]["x"]
    assert mirrored[1]["position_cm"]["x"] + width > mirrored[0]["position_cm"]["x"] + length
    result = plan(placed, ("S01", "S02"))
    assert placed == original
    assert any("车尾通道" in warning for warning in result["blocks_warning"])


def test_conflicting_channel_directions_cannot_be_cleared_by_whole_mirror():
    placed = [box("S01", x=10), box("S02", x=0),
              box("S01", x=0, y=20), box("S02", x=10, y=20)]
    original = deepcopy(placed)
    result = plan(placed, ("S01", "S02"))
    assert len(result["blocks_warning"]) == 1
    assert "车尾通道" in result["blocks_warning"][0]
    assert "1 对" in result["blocks_warning"][0]
    assert placed == original


def test_already_good_coordinates_are_not_changed_and_touching_is_not_overlap():
    placed = [box("S01", x=0), box("S02", x=10), box("S03", x=0, y=10)]
    original = deepcopy(placed)
    assert plan(placed)["blocks_warning"] == []
    assert placed == original


def test_warning_checks_all_overhead_not_only_immediate_support():
    placed = [box("S01"), box("S01", z=10), box("S03", z=20)]
    result = plan(placed)
    assert any("上下压，2 对" in warning for warning in result["blocks_warning"])


def test_same_store_stack_and_channel_need_no_cross_store_warning():
    assert plan([box("S01"), box("S01", z=10), box("S01", x=10)],
                ("S01",))["blocks_warning"] == []


def test_real_data_mirror_preserves_packing_safety():
    from solver.packing import solve_packing
    from solver.routing import solve_routing

    data = Path(__file__).resolve().parents[1] / "data"
    orders, vehicles, network, rules = [json.loads((data / f"{name}.json").read_text(
        encoding="utf-8")) for name in ("orders", "vehicles", "network", "rules")]
    routes = solve_routing(orders, vehicles, network, rules)
    packing = solve_packing(routes, orders, vehicles, rules)
    original = deepcopy(packing)
    result = sequence_loading(packing, routes, rules)
    from test_packing import validate
    validate(packing, routes, orders, vehicles, rules)
    types = {v["vehicle_id"]: v for v in vehicles["vehicles"]}
    mirrored = []
    for before, after in zip(original["vehicles"], packing["vehicles"]):
        if before != after:
            mirrored.append(after["vehicle_instance"])
        for i, placed in enumerate(after["placed"]):
            assert not any(all(min(placed["position_cm"][axis] + placed["size_placed_cm"][size],
                                   other["position_cm"][axis] + other["size_placed_cm"][size]) -
                               max(placed["position_cm"][axis], other["position_cm"][axis]) > 1e-7
                               for axis, size in (("x", "l"), ("y", "w"), ("z", "h")))
                           for other in after["placed"][i + 1:])
            for axis, size, limit in (("x", "l", "cargo_length_cm"),
                                     ("y", "w", "cargo_width_cm"),
                                     ("z", "h", "cargo_height_cm")):
                start = placed["position_cm"][axis]
                assert start >= -1e-7
                assert start + placed["size_placed_cm"][size] <= types[after["vehicle_type"]][limit] + 1e-7
            if placed["position_cm"]["z"] > 1e-7:
                assert any(abs(base["position_cm"]["z"] + base["size_placed_cm"]["h"]
                               - placed["position_cm"]["z"]) <= 1e-7 and
                           all(base["position_cm"][axis] <= placed["position_cm"][axis] + 1e-7 and
                               placed["position_cm"][axis] + placed["size_placed_cm"][size] <=
                               base["position_cm"][axis] + base["size_placed_cm"][size] + 1e-7
                               for axis, size in (("x", "l"), ("y", "w")))
                           for base in after["placed"] if base is not placed)
            before["placed"][i]["position_cm"]["x"] = placed["position_cm"]["x"]
        assert before == after
    count = sum(len(v["placed"]) for v in packing["vehicles"])
    assert sum(item["qty"] for p in result["plans"] for s in p["loading_steps"]
               for item in s["items"]) == count
    print({"mirrored_vehicles": mirrored, "placed": count,
           "unplaced": sum(len(v["unplaced"]) for v in packing["vehicles"]),
           "warnings": sum(len(p["blocks_warning"]) for p in result["plans"]),
           "warning_vehicles": sum(bool(p["blocks_warning"]) for p in result["plans"])})
