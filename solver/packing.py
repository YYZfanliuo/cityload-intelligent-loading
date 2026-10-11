"""Deterministic 3D layer packing heuristic for CityLoad."""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def _orientations(item: dict, rules: dict):
    base = (float(item["length_cm"]), float(item["width_cm"]), float(item["height_cm"]))
    names = {"lwh": base, "wlh": (base[1], base[0], base[2]), "hlw": (base[2], base[0], base[1])}
    allowed = rules.get("packing", {}).get("rotation_axes", ["lwh"])
    seen = set()
    for name in allowed:
        dims = names.get(name)
        if dims and dims not in seen:
            seen.add(dims)
            yield name != "lwh", dims


def _overlap(a, b):
    ap, ad = a["position_cm"], a["size_placed_cm"]
    bp, bd = b["position_cm"], b["size_placed_cm"]
    return (ap["x"] < bp["x"] + bd["l"] and ap["x"] + ad["l"] > bp["x"] and
            ap["y"] < bp["y"] + bd["w"] and ap["y"] + ad["w"] > bp["y"] and
            ap["z"] < bp["z"] + bd["h"] and ap["z"] + ad["h"] > bp["z"])


def _pack_vehicle(items: list[dict], vehicle: dict, rules: dict):
    L, W, H = (float(vehicle[k]) for k in ("cargo_length_cm", "cargo_width_cm", "cargo_height_cm"))
    gap = float(rules.get("packing", {}).get("gap_cm", 0.0))
    placed, unplaced = [], []
    # Fragile goods are deliberately processed last, placing them on the highest layer.
    items = sorted(items, key=lambda x: (bool(x.get("fragile", False)),
                                         -float(x["length_cm"])*float(x["width_cm"])*float(x["height_cm"])))
    layers: list[dict[str, Any]] = []
    for item in items:
        done = False
        for rotated, (l, w, h) in _orientations(item, rules):
            if l > L or w > W or h > H:
                continue
            # First-fit shelf layers. A new layer is allowed only above stackable goods.
            for layer in layers:
                if layer["z"] + h > H or layer["fragile"]:
                    continue
                x, y = layer["x"], layer["y"]
                if x + l > L:
                    x, y = 0.0, y + layer["row_h"] + gap
                if y + w > W:
                    continue
                candidate = {"position_cm": {"x": x, "y": y, "z": layer["z"]},
                             "size_placed_cm": {"l": l, "w": w, "h": h}}
                if any(_overlap(candidate, old) for old in placed):
                    continue
                placed.append({"sku": item["sku"], "order_id": item["order_id"], "store_id": item["store_id"],
                               **candidate, "layer": layer["number"], "rotated": rotated})
                layer["x"] = x + l + gap
                layer["row_h"] = max(layer["row_h"], w)
                done = True
                break
            if done:
                break
            # Create a new layer above the highest existing layer, or at floor.
            z = 0.0 if not layers else max(layer["z"] + layer["height"] for layer in layers)
            if z + h <= H:
                # Never place a new layer on a non-stackable or fragile layer.
                if layers and any(not layer["stackable"] or layer["fragile"] for layer in layers if layer["z"] + layer["height"] == z):
                    continue
                layer = {"z": z, "height": h, "x": l + gap, "y": 0.0, "row_h": w,
                         "number": len(layers) + 1, "stackable": bool(item.get("stackable", True)),
                         "fragile": bool(item.get("fragile", False))}
                placed.append({"sku": item["sku"], "order_id": item["order_id"], "store_id": item["store_id"],
                               "position_cm": {"x": 0.0, "y": 0.0, "z": z},
                               "size_placed_cm": {"l": l, "w": w, "h": h}, "layer": layer["number"], "rotated": rotated})
                layers.append(layer)
                done = True
                break
        if not done:
            unplaced.append({"sku": item["sku"], "order_id": item["order_id"], "store_id": item["store_id"], "qty": 1})
    used_volume = sum(x["size_placed_cm"]["l"] * x["size_placed_cm"]["w"] * x["size_placed_cm"]["h"] for x in placed)
    used_weight = sum(x.get("weight_kg", 0) for x in placed)
    return placed, unplaced, used_volume / (L * W * H) if L * W * H else 0.0


def solve_packing(routes: dict, orders: dict, vehicles: dict, rules: dict) -> dict:
    """Pack each route's order pieces into its vehicle using layered shelves."""
    by_store = {o["store_id"]: o for o in orders.get("orders", [])}
    vehicle_types = {v["vehicle_id"]: v for v in vehicles.get("vehicles", [])}
    outputs = []
    for route in routes.get("routes", []):
        vehicle = vehicle_types[route["vehicle_type"]]
        items = []
        for store in route.get("sequence", [])[1:-1]:
            for item in by_store.get(store, {}).get("items", []):
                for _ in range(int(item.get("qty", 0))):
                    items.append({**item, "order_id": by_store[store]["order_id"], "store_id": store})
        placed, unplaced, vol_rate = _pack_vehicle(items, vehicle, rules)
        total_weight = 0.0
        for p in placed:
            source = next((i for i in items if i["sku"] == p["sku"] and i["order_id"] == p["order_id"]), {})
            p["weight_kg"] = float(source.get("weight_kg", 0))
            total_weight += p["weight_kg"]
        outputs.append({"vehicle_instance": f"{route['route_id']}-1", "vehicle_type": route["vehicle_type"],
                        "placed": placed, "unplaced": unplaced, "volume_utilization": round(vol_rate, 4),
                        "weight_utilization": round(total_weight / float(vehicle["max_weight_kg"]), 4)})
    return {"status": "ok", "vehicles": outputs}
