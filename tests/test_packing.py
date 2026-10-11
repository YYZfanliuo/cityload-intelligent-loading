import json
from collections import Counter
from copy import deepcopy
from itertools import permutations
from math import fsum
from pathlib import Path
from random import Random

import pytest

from solver.packing import solve_packing, _overlap
from solver.routing import solve_routing

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


def item(sku="A", dims=(10, 10, 10), qty=1, weight=1, **flags):
    return dict(sku=sku, length_cm=dims[0], width_cm=dims[1], height_cm=dims[2],
                qty=qty, weight_kg=weight, fragile=False, stackable=True,
                max_stack_layers=10) | flags


def scenario(items, size=(20, 20, 30), capacity=100, packing=None):
    orders = {"orders": [{"order_id": "O1", "store_id": "S1", "items": items}]}
    vehicles = {"vehicles": [{"vehicle_id": "V1", "cargo_length_cm": size[0],
                               "cargo_width_cm": size[1], "cargo_height_cm": size[2],
                               "max_weight_kg": capacity}]}
    routes = {"routes": [{"route_id": "R1", "vehicle_type": "V1", "sequence": ["WH", "S1", "WH"]}]}
    rules = {"packing": {"allow_rotation": False, "rotation_axes": ["lwh"], "gap_cm": 0} | (packing or {})}
    result = solve_packing(routes, orders, vehicles, rules)
    validate(result, routes, orders, vehicles, rules)
    return result["vehicles"][0]


def footprint_intersects(a, b):
    ap, ad, bp, bd = a["position_cm"], a["size_placed_cm"], b["position_cm"], b["size_placed_cm"]
    return (ap["x"] < bp["x"] + bd["l"] and bp["x"] < ap["x"] + ad["l"] and
            ap["y"] < bp["y"] + bd["w"] and bp["y"] < ap["y"] + ad["w"])


def validate(result, routes, orders, vehicles, rules):
    """独立于求解器校验几何、支撑链、安全约束与逐订单逐SKU守恒。"""
    assert result["status"] == "ok"
    assert len(result["vehicles"]) == len(routes["routes"])
    specs = {v["vehicle_id"]: v for v in vehicles["vehicles"]}
    sources = {(o["order_id"], o["store_id"], i["sku"]): i for o in orders["orders"] for i in o["items"]}
    for packed, route in zip(result["vehicles"], routes["routes"]):
        spec = specs[packed["vehicle_type"]]
        expected = Counter()
        for store in route["sequence"][1:-1]:
            for order in orders["orders"]:
                if order["store_id"] == store:
                    for source in order["items"]:
                        expected[order["order_id"], store, source["sku"]] += source["qty"]
        actual = Counter((b["order_id"], b["store_id"], b["sku"]) for b in packed["placed"])
        for b in packed["unplaced"]:
            actual[b["order_id"], b["store_id"], b["sku"]] += b["qty"]
        assert actual == expected
        boxes = packed["placed"]
        weight = 0
        parents = {}
        for idx, b in enumerate(boxes):
            source = sources[b["order_id"], b["store_id"], b["sku"]]
            p, d = b["position_cm"], b["size_placed_cm"]
            dims = tuple(d[k] for k in ("l", "w", "h"))
            base = dict(zip("lwh", (source[k] for k in ("length_cm", "width_cm", "height_cm"))))
            names = rules["packing"].get("rotation_axes", ["lwh"]) if rules["packing"].get("allow_rotation", True) else ["lwh"]
            assert dims in {tuple(base[k] for k in name) for name in names if name in {"".join(p) for p in permutations("lwh")}}
            if not rules["packing"].get("allow_rotation", True):
                assert b["rotated"] is False
            assert b["rotated"] or dims == tuple(base[k] for k in "lwh")
            for axis, side, cap in (("x", "l", "cargo_length_cm"), ("y", "w", "cargo_width_cm"), ("z", "h", "cargo_height_cm")):
                assert p[axis] >= 0 and d[side] > 0
                assert p[axis] + d[side] <= spec[cap] + 1e-9
            # 不依赖生产代码的相交判断。
            for other in boxes[idx + 1:]:
                op, od = other["position_cm"], other["size_placed_cm"]
                intersects = all(p[a] < op[a] + od[s] and op[a] < p[a] + d[s]
                                 for a, s in (("x", "l"), ("y", "w"), ("z", "h")))
                assert not intersects
                assert not _overlap(b, other)
            weight += source["weight_kg"]
            assert b["weight_kg"] == source["weight_kg"]
            if p["z"] == 0:
                assert b["layer"] == 1
            else:
                supports = []
                for j, other in enumerate(boxes):
                    op, od = other["position_cm"], other["size_placed_cm"]
                    if (abs(op["z"] + od["h"] - p["z"]) < 1e-9 and
                            op["x"] <= p["x"] and p["x"] + d["l"] <= op["x"] + od["l"] and
                            op["y"] <= p["y"] and p["y"] + d["w"] <= op["y"] + od["w"]):
                        supports.append(j)
                assert supports, "非地板箱必须有完整支撑"
                parents[idx] = supports[0]
                assert b["layer"] == boxes[supports[0]]["layer"] + 1
            if source["fragile"] or not source["stackable"]:
                assert not any(c is not b and footprint_intersects(b, c) and
                               c["position_cm"]["z"] >= p["z"] + d["h"] for c in boxes)
        # 每个承重祖先均限制含自身的剩余链长，而不只检查新箱层数。
        for idx, b in enumerate(boxes):
            current, height = idx, 1
            while True:
                ancestor = boxes[current]
                source = sources[ancestor["order_id"], ancestor["store_id"], ancestor["sku"]]
                assert height <= source["max_stack_layers"]
                assert ancestor["layer"] <= source["max_stack_layers"]
                if current not in parents:
                    break
                current = parents[current]
                height += 1
        weight = fsum(sources[b["order_id"], b["store_id"], b["sku"]]["weight_kg"] for b in boxes)
        assert weight <= spec["max_weight_kg"]
        expected_rate = round(weight / spec["max_weight_kg"], 4) if spec["max_weight_kg"] else 0.0
        assert packed["weight_utilization"] == expected_rate
        volume = sum(b["size_placed_cm"]["l"] * b["size_placed_cm"]["w"] * b["size_placed_cm"]["h"] for b in boxes)
        assert packed["volume_utilization"] == round(volume / (spec["cargo_length_cm"] * spec["cargo_width_cm"] * spec["cargo_height_cm"]), 4)


def test_overweight_independent_of_geometry():
    p = scenario([item(qty=3, weight=6)], capacity=10)
    assert (len(p["placed"]), len(p["unplaced"])) == (1, 2)
    p = scenario([item(weight=11)], capacity=10)
    assert len(p["unplaced"]) == 1
    assert len(scenario([item(weight=1)], capacity=0)["unplaced"]) == 1


def test_oversize_and_exact_capacity():
    assert len(scenario([item(dims=(21, 21, 31), qty=2)])["unplaced"]) == 2
    p = scenario([item(dims=(20, 20, 30), weight=100)])
    assert len(p["placed"]) == 1 and p["weight_utilization"] == 1


@pytest.mark.parametrize("axis", ["lwh", "lhw", "wlh", "whl", "hlw", "hwl"])
def test_all_six_rotations(axis):
    base = dict(zip("lwh", (7, 11, 13)))
    size = tuple(base[k] for k in axis)
    p = scenario([item(dims=(7, 11, 13))], size=size,
                 packing={"allow_rotation": True, "rotation_axes": [axis]})
    assert len(p["placed"]) == 1
    assert p["placed"][0]["rotated"] == (axis != "lwh")


def test_rotation_disabled_and_axis_restriction():
    flags = {"rotation_axes": ["wlh"]}
    assert len(scenario([item(dims=(15, 10, 5))], size=(10, 15, 5), packing=flags)["unplaced"]) == 1
    assert len(scenario([item(dims=(15, 10, 5))], size=(10, 15, 5),
                        packing=flags | {"allow_rotation": True})["placed"]) == 1
    assert len(scenario([item(dims=(15, 10, 5))], size=(10, 15, 5),
                        packing={"allow_rotation": True, "rotation_axes": ["lwh"]})["unplaced"]) == 1


def test_floor_rows_and_variable_heights():
    p = scenario([item(qty=6, stackable=False)], size=(20, 30, 10))
    assert len(p["placed"]) == 6
    assert {b["position_cm"]["y"] for b in p["placed"]} == {0, 10, 20}
    p = scenario([item("tall", dims=(10, 10, 15)), item("short", dims=(10, 10, 8)),
                  item("top", dims=(10, 10, 7))], size=(10, 10, 30))
    assert [b["position_cm"]["z"] for b in p["placed"]] == [0, 15, 23]


def test_fragile_no_above_even_with_flags_off():
    p = scenario([item("normal", qty=2), item("fragile", qty=2, fragile=True)], size=(20, 10, 30),
                 packing={"consider_fragile": False})
    assert len(p["placed"]) == 4 and not p["unplaced"]
    assert {b["position_cm"]["z"] for b in p["placed"] if b["sku"] == "fragile"} == {0, 20}
    # 易碎件可处于较低塔顶，不必与旁边不可堆叠的高箱统一平面。
    p = scenario([item("high", dims=(10, 10, 20), stackable=False),
                  item("low", dims=(10, 10, 10)), item("fragile", fragile=True)], size=(20, 10, 30))
    assert len(p["placed"]) == 3 and not p["unplaced"]
    assert next(b for b in p["placed"] if b["sku"] == "fragile")["position_cm"]["z"] == 10
    assert len(scenario([item(qty=3, fragile=True)], size=(20, 10, 30))["placed"]) == 2


def test_fragile_max_one_uses_floor_without_pressure():
    p = scenario([item("normal", qty=2),
                  item("fragile", fragile=True, max_stack_layers=1)], size=(20, 10, 30))
    assert len(p["placed"]) == 3 and not p["unplaced"]
    fragile = next(b for b in p["placed"] if b["sku"] == "fragile")
    assert fragile["position_cm"]["z"] == 0 and fragile["layer"] == 1
    # 即使允许旋转且关闭易碎开关，易碎件也不能承压。
    p = scenario([item("fragile", qty=3, fragile=True, max_stack_layers=1)], size=(10, 10, 30),
                 packing={"allow_rotation": True, "consider_fragile": False})
    assert len(p["placed"]) == 1 and len(p["unplaced"]) == 2


@pytest.mark.parametrize("flags", [{"stackable": False}, {"max_stack_layers": 1}, {"max_stack_layers": 2}])
def test_stackability_and_stack_limits(flags):
    p = scenario([item(qty=4, **flags)], size=(10, 10, 50), packing={"consider_max_stack": False})
    assert len(p["placed"]) == (2 if flags.get("max_stack_layers") == 2 else 1)


def test_limits_on_all_ancestors_and_new_item():
    p = scenario([item("base", dims=(10, 10, 10), max_stack_layers=2),
                  item("middle", dims=(10, 10, 9)), item("top", dims=(10, 10, 8))], size=(10, 10, 50))
    assert len(p["placed"]) == 2 and p["unplaced"][0]["sku"] == "top"
    p = scenario([item("base"), item("one", dims=(9, 9, 9), max_stack_layers=1)], size=(10, 10, 50))
    assert len(p["unplaced"]) == 1


@pytest.mark.parametrize("fragile", [False, True])
def test_complete_support_rejects_overhang(fragile):
    p = scenario([item("base", dims=(8, 8, 20)),
                  item("wide", dims=(10, 10, 5), fragile=fragile)], size=(10, 10, 30))
    assert len(p["placed"]) == 1 and p["unplaced"][0]["sku"] == "wide"


def test_reclaims_floor_holes_without_overhang():
    p = scenario([item("long", dims=(15, 10, 10), stackable=False),
                  item("square", dims=(10, 10, 10), qty=2, stackable=False),
                  item("small", dims=(5, 10, 10), stackable=False)], size=(20, 20, 10))
    assert len(p["placed"]) == 4 and not p["unplaced"]


def test_multiple_children_keep_complete_single_box_support():
    p = scenario([item("base", dims=(20, 20, 10)),
                  item("child", dims=(10, 10, 9), qty=4, stackable=False)], size=(20, 20, 20))
    assert len(p["placed"]) == 5 and not p["unplaced"]
    assert {b["layer"] for b in p["placed"] if b["sku"] == "child"} == {2}


def test_branching_support_checks_shared_ancestor_limit():
    p = scenario([item("base", dims=(20, 20, 10), max_stack_layers=2),
                  item("middle", dims=(10, 10, 9), qty=4),
                  item("top", dims=(10, 10, 8), qty=4)], size=(20, 20, 50))
    assert len(p["placed"]) == 5 and len(p["unplaced"]) == 4
    assert all(b["layer"] <= 2 for b in p["placed"])


def test_gap_is_horizontal_and_support_is_in_contact():
    p = scenario([item(qty=5, max_stack_layers=2)], size=(21, 10, 20), packing={"gap_cm": 1})
    assert len(p["placed"]) == 4
    assert {b["position_cm"]["x"] for b in p["placed"]} == {0, 11}
    assert {b["position_cm"]["z"] for b in p["placed"]} == {0, 10}


def test_fragile_still_respects_support_height_and_stack_limits():
    p = scenario([item("base", qty=2, max_stack_layers=1),
                  item("fragile", qty=2, fragile=True)], size=(20, 10, 30))
    assert len(p["placed"]) == 2 and len(p["unplaced"]) == 2
    p = scenario([item("base", qty=4, max_stack_layers=3),
                  item("fragile", qty=2, fragile=True)], size=(20, 10, 20))
    assert len(p["placed"]) == 4 and len(p["unplaced"]) == 2
    p = scenario([item("base", qty=4, max_stack_layers=2),
                  item("fragile", qty=2, fragile=True)], size=(20, 10, 30))
    assert len(p["placed"]) == 4 and len(p["unplaced"]) == 2
    p = scenario([item("base", dims=(10, 10, 12), qty=2),
                  item("fragile", qty=2, fragile=True)], size=(20, 10, 22))
    assert len(p["placed"]) == 4 and not p["unplaced"]
    assert scenario([])["placed"] == []


def test_quantity_conservation_multiple_orders_same_store_and_sku():
    orders = {"orders": [{"order_id": "O1", "store_id": "S1", "items": [item(qty=2)]},
                          {"order_id": "O2", "store_id": "S1", "items": [item(qty=3, weight=4)]}]}
    vehicles = {"vehicles": [{"vehicle_id": "V1", "cargo_length_cm": 10, "cargo_width_cm": 10,
                               "cargo_height_cm": 30, "max_weight_kg": 6}]}
    routes = {"routes": [{"route_id": "R1", "vehicle_type": "V1", "sequence": ["WH", "S1", "WH"]}]}
    rules = {"packing": {"allow_rotation": False}}
    result = solve_packing(routes, orders, vehicles, rules)
    validate(result, routes, orders, vehicles, rules)
    assert len(result["vehicles"][0]["placed"]) == 3
    assert len(result["vehicles"][0]["unplaced"]) == 2


@pytest.mark.parametrize("seed", range(12))
def test_small_random_conservation_and_constraints(seed):
    rng = Random(seed)
    items = [item(str(n), dims=tuple(rng.randint(3, 12) for _ in range(3)),
                  qty=rng.randint(1, 4), weight=rng.randint(1, 8),
                  fragile=rng.choice([False, False, True]), stackable=rng.choice([False, True]),
                  max_stack_layers=rng.randint(1, 4)) for n in range(8)]
    scenario(items, size=(20, 20, 30), capacity=60,
             packing={"allow_rotation": True, "rotation_axes": ["".join(p) for p in permutations("lwh")],
                      "gap_cm": rng.choice([0, 1])})


def test_packing_outputs_are_isolated_across_repeated_inputs_and_sequencing():
    from solver.sequencing import sequence_loading

    orders = {"orders": [{"order_id": "O1", "store_id": "S1", "items": [item()]},
                          {"order_id": "O2", "store_id": "S2", "items": [item()]}]}
    vehicles = {"vehicles": [{"vehicle_id": "V1", "cargo_length_cm": 20,
                               "cargo_width_cm": 10, "cargo_height_cm": 10,
                               "max_weight_kg": 100}]}
    routes = {"routes": [{"route_id": "R1", "vehicle_type": "V1",
                           "sequence": ["WH", "S2", "S1", "WH"]}]}
    rules = {"packing": {"allow_rotation": False}}
    snapshot = deepcopy((routes, orders, vehicles, rules))
    first = solve_packing(routes, orders, vehicles, rules)
    second = solve_packing(routes, orders, vehicles, rules)
    assert first == second
    delivery_routes = deepcopy(routes)
    delivery_routes["routes"][0]["sequence"] = ["WH", "S1", "S2", "WH"]
    sequence_loading(first, delivery_routes, rules)
    assert first != second
    assert second == solve_packing(routes, orders, vehicles, rules)
    assert (routes, orders, vehicles, rules) == snapshot
    validate(first, routes, orders, vehicles, rules)
    validate(second, routes, orders, vehicles, rules)


def test_real_30_store_constraints():
    orders, vehicles, network, rules = load("orders.json"), load("vehicles.json"), load("network.json"), load("rules.json")
    assert len(orders["orders"]) == 30
    routes = solve_routing(orders, vehicles, network, rules)
    result = solve_packing(routes, orders, vehicles, rules)
    validate(result, routes, orders, vehicles, rules)
    assert result == solve_packing(routes, orders, vehicles, rules)
    assigned = {s for r in routes["routes"] for s in r["sequence"][1:-1]}
    all_stores = {o["store_id"] for o in orders["orders"]}
    unassigned = {o["order_id"] for o in orders["orders"] if o["store_id"] not in assigned}
    assert assigned <= all_stores
    assert set(routes["unassigned_orders"]) == unassigned
    for p in result["vehicles"]:
        print(f"{p['vehicle_instance']}: placed={len(p['placed'])}, unplaced={len(p['unplaced'])}")
    print(f"30店: total={sum(i['qty'] for o in orders['orders'] for i in o['items'])}, "
          f"placed={sum(len(p['placed']) for p in result['vehicles'])}, "
          f"unplaced={sum(len(p['unplaced']) for p in result['vehicles'])}, "
          f"routing_unassigned={sum(i['qty'] for o in orders['orders'] if o['store_id'] not in assigned for i in o['items'])}")
