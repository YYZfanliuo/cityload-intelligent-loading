"""确定性装箱启发式：地板货架与单箱完整支撑的独立堆垛。"""
from __future__ import annotations


def _orientations(item: dict, rules: dict):
    """按配置枚举六种轴排列；禁用旋转时仅保留原始方向。"""
    base = dict(zip("lwh", (float(item[k]) for k in
                               ("length_cm", "width_cm", "height_cm"))))
    packing = rules.get("packing", {})
    allowed = packing.get("rotation_axes", ["lwh"]) if packing.get("allow_rotation", True) else ["lwh"]
    seen = set()
    for name in allowed:
        if name not in {"lwh", "lhw", "wlh", "whl", "hlw", "hwl"}:
            continue
        dims = tuple(base[axis] for axis in name)
        if dims not in seen:
            seen.add(dims)
            yield name != "lwh", dims


def _overlap(a, b):
    """判断两个轴对齐箱体是否存在正体积相交。"""
    ap, ad = a["position_cm"], a["size_placed_cm"]
    bp, bd = b["position_cm"], b["size_placed_cm"]
    return (ap["x"] < bp["x"] + bd["l"] and ap["x"] + ad["l"] > bp["x"] and
            ap["y"] < bp["y"] + bd["w"] and ap["y"] + ad["w"] > bp["y"] and
            ap["z"] < bp["z"] + bd["h"] and ap["z"] + ad["h"] > bp["z"])


def _pack_vehicle(items: list[dict], vehicle: dict, rules: dict):
    """先装普通件，再装易碎件；任何不可行件逐件返回，不放宽安全约束。"""
    L, W, H = (float(vehicle[k]) for k in ("cargo_length_cm", "cargo_width_cm", "cargo_height_cm"))
    capacity = float(vehicle["max_weight_kg"])
    gap = max(0.0, float(rules.get("packing", {}).get("gap_cm", 0.0)))
    placed, unplaced, towers = [], [], []
    used_weight = 0.0
    # 地板货架只管理二维占地；各堆垛的高度与层数独立维护。
    shelf_x = shelf_y = row_width = 0.0
    normal_top = 0.0
    normal_layer = 0
    fragile_plane = None
    items = sorted(items, key=lambda i: (bool(i.get("fragile", False)),
                                         -float(i["length_cm"]) * float(i["width_cm"]) * float(i["height_cm"])))
    for item in items:
        weight = float(item.get("weight_kg", 0.0))
        fragile = bool(item.get("fragile", False))
        limit = int(item.get("max_stack_layers", 1))
        choice = None
        if weight >= 0 and used_weight + weight <= capacity and limit >= 1:
            for rotated, (l, w, h) in _orientations(item, rules):
                if min(l, w, h) <= 0 or l > L or w > W or h > H:
                    continue
                for tower in towers:
                    # remaining 同时维护所有下方箱体的剩余可堆叠层数。
                    depth = tower["depth"] + 1
                    if (tower["remaining"] < 1 or depth > limit or
                            l > tower["l"] or w > tower["w"] or tower["top"] + h > H):
                        continue
                    if fragile and (tower["top"] < normal_top or depth < normal_layer or
                                    (fragile_plane is not None and (tower["top"], depth) != fragile_plane)):
                        continue
                    choice = (rotated, l, w, h, tower["x"], tower["y"], tower["top"], depth, tower)
                    break
                if choice is not None:
                    break
                # 换行只在成功放置后更新，避免失败方向污染货架状态。
                x, y, rw = shelf_x, shelf_y, row_width
                if x + l > L:
                    x, y, rw = 0.0, y + rw + gap, 0.0
                if y + w <= W and (not fragile or (normal_top == 0 and normal_layer == 0)):
                    choice = (rotated, l, w, h, x, y, 0.0, 1, None)
                    next_shelf = (x + l + gap, y, max(rw, w))
                    break
        if choice is None:
            unplaced.append({"sku": item["sku"], "order_id": item["order_id"],
                             "store_id": item["store_id"], "qty": 1})
            continue
        rotated, l, w, h, x, y, z, depth, tower = choice
        box = {"sku": item["sku"], "order_id": item["order_id"], "store_id": item["store_id"],
               "position_cm": {"x": x, "y": y, "z": z},
               "size_placed_cm": {"l": l, "w": w, "h": h},
               "layer": depth, "rotated": rotated, "weight_kg": weight}
        placed.append(box)
        used_weight += weight
        remaining = limit - 1
        if tower is None:
            shelf_x, shelf_y, row_width = next_shelf
            tower = {"x": x, "y": y}
            towers.append(tower)
        else:
            remaining = min(remaining, tower["remaining"] - 1)
        if fragile:
            fragile_plane = (z, depth)
        if fragile or not item.get("stackable", True):
            remaining = 0
        tower.update(l=l, w=w, top=z + h, depth=depth, remaining=remaining)
        if not fragile:
            normal_top = max(normal_top, z + h)
            normal_layer = max(normal_layer, depth)
    volume = sum(b["size_placed_cm"]["l"] * b["size_placed_cm"]["w"] * b["size_placed_cm"]["h"] for b in placed)
    return placed, unplaced, volume / (L * W * H) if L * W * H else 0.0


def solve_packing(routes: dict, orders: dict, vehicles: dict, rules: dict) -> dict:
    """保持冻结接口，为每条路线逐件装箱；未分配路线订单不属于本接口输入货量。"""
    by_store = {}
    for order in orders.get("orders", []):
        by_store.setdefault(order["store_id"], []).append(order)
    vehicle_types = {v["vehicle_id"]: v for v in vehicles.get("vehicles", [])}
    outputs = []
    for route in routes.get("routes", []):
        vehicle = vehicle_types[route["vehicle_type"]]
        items = []
        for store in route.get("sequence", [])[1:-1]:
            for order in by_store.get(store, []):
                for item in order.get("items", []):
                    for _ in range(int(item.get("qty", 0))):
                        items.append({**item, "order_id": order["order_id"], "store_id": store})
        placed, unplaced, vol_rate = _pack_vehicle(items, vehicle, rules)
        total_weight = sum(p["weight_kg"] for p in placed)
        capacity = float(vehicle["max_weight_kg"])
        outputs.append({"vehicle_instance": f"{route['route_id']}-1", "vehicle_type": route["vehicle_type"],
                        "placed": placed, "unplaced": unplaced, "volume_utilization": round(vol_rate, 4),
                        "weight_utilization": round(total_weight / capacity, 4) if capacity > 0 else 0.0})
    return {"status": "ok", "vehicles": outputs}
