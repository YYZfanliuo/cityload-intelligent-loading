"""确定性安全装箱：极大空矩形孔隙回收与单箱完整支撑。"""
from __future__ import annotations

from math import fsum


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


def _pack_vehicle_legacy(items: list[dict], vehicle: dict, rules: dict):
    """先装普通件，再装易碎件；易碎件只需无上方货物，不要求全车同高。"""
    L, W, H = (float(vehicle[k]) for k in ("cargo_length_cm", "cargo_width_cm", "cargo_height_cm"))
    capacity = float(vehicle["max_weight_kg"])
    gap = max(0.0, float(rules.get("packing", {}).get("gap_cm", 0.0)))
    placed, unplaced, towers = [], [], []
    used_weight = 0.0
    # 地板货架只管理二维占地；各堆垛的高度与层数独立维护。
    shelf_x = shelf_y = row_width = 0.0
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
                    choice = (rotated, l, w, h, tower["x"], tower["y"], tower["top"], depth, tower)
                    break
                if choice is not None:
                    break
                # 换行只在成功放置后更新，避免失败方向污染货架状态。
                x, y, rw = shelf_x, shelf_y, row_width
                if x + l > L:
                    x, y, rw = 0.0, y + rw + gap, 0.0
                if y + w <= W:
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
        if fragile or not item.get("stackable", True):
            remaining = 0
        tower.update(l=l, w=w, top=z + h, depth=depth, remaining=remaining)
    volume = sum(b["size_placed_cm"]["l"] * b["size_placed_cm"]["w"] * b["size_placed_cm"]["h"] for b in placed)
    return placed, unplaced, volume / (L * W * H) if L * W * H else 0.0


def _split_rectangles(rectangles, x, y, l, w):
    """扣除已占用区域并保留极大空矩形，不把孔隙丢给固定货架。"""
    result = []
    for rx, ry, rl, rw in rectangles:
        if x >= rx + rl or x + l <= rx or y >= ry + rw or y + w <= ry:
            result.append((rx, ry, rl, rw))
            continue
        if x > rx:
            result.append((rx, ry, x - rx, rw))
        if x + l < rx + rl:
            result.append((x + l, ry, rx + rl - x - l, rw))
        if y > ry:
            result.append((rx, ry, rl, y - ry))
        if y + w < ry + rw:
            result.append((rx, y + w, rl, ry + rw - y - w))
    unique = list(dict.fromkeys(result))
    return [r for i, r in enumerate(unique) if not any(
        i != j and s[0] <= r[0] and s[1] <= r[1] and
        r[0] + r[2] <= s[0] + s[2] and r[1] + r[3] <= s[1] + s[3]
        for j, s in enumerate(unique))]


def _pack_surfaces(items, vehicle, rules):
    """在地板及每个安全箱顶回收二维孔隙，所有上层仍由单箱完整支撑。"""
    L, W, H = (float(vehicle[k]) for k in ("cargo_length_cm", "cargo_width_cm", "cargo_height_cm"))
    capacity = float(vehicle["max_weight_kg"])
    gap = max(0.0, float(rules.get("packing", {}).get("gap_cm", 0.0)))
    prepared = [(item, list(_orientations(item, rules))) for item in items]

    def order_key(entry):
        """维持体积降序，普通件先装，避免易碎箱受压。"""
        item, _ = entry
        volume = float(item["length_cm"]) * float(item["width_cm"]) * float(item["height_cm"])
        return (bool(item.get("fragile", False)), -volume)

    prepared.sort(key=order_key)
    surfaces = [{"z": 0.0, "depth": 0, "remaining": len(items),
                 "rectangles": [(0.0, 0.0, L, W)], "ancestors": ()}]
    placed, unplaced = [], []
    used_weight = 0.0
    for item, orientations in prepared:
        weight = float(item.get("weight_kg", 0.0))
        fragile = bool(item.get("fragile", False))
        limit = int(item.get("max_stack_layers", 1))
        choice = None
        if weight >= 0 and used_weight + weight <= capacity and limit >= 1:
            for si, surface in enumerate(surfaces):
                z, depth = surface["z"], surface["depth"] + 1
                ancestors = surface["ancestors"]
                if depth > limit or surface["remaining"] < 1:
                    continue
                if any(depth - ancestor_depth + 1 > ancestor_limit
                       for ancestor_depth, ancestor_limit in ancestors):
                    continue
                for rotated, (l, w, h) in orientations:
                    if min(l, w, h) <= 0 or z + h > H:
                        continue
                    for x, y, rl, rw in surface["rectangles"]:
                        if l > rl or w > rw:
                            continue
                        # 先复用已有支撑面，足迹尽量小，空隙用短边最佳适配。
                        score = (z == 0, l * w, min(rl - l, rw - w),
                                 rl * rw - l * w, y, x, z, h, si)
                        if choice is None or score < choice[0]:
                            choice = (score, si, rotated, l, w, h, x, y, z, depth)
        if choice is None:
            unplaced.append({"sku": item["sku"], "order_id": item["order_id"],
                             "store_id": item["store_id"], "qty": 1})
            continue
        _, si, rotated, l, w, h, x, y, z, depth = choice
        surface = surfaces[si]
        surface["rectangles"] = _split_rectangles(surface["rectangles"], x, y, l + gap, w + gap)
        placed.append({"sku": item["sku"], "order_id": item["order_id"], "store_id": item["store_id"],
                       "position_cm": {"x": x, "y": y, "z": z},
                       "size_placed_cm": {"l": l, "w": w, "h": h},
                       "layer": depth, "rotated": rotated, "weight_kg": weight})
        used_weight += weight
        if not fragile and item.get("stackable", True) and limit > 1:
            surfaces.append({"z": z + h, "depth": depth, "remaining": limit - 1,
                             "rectangles": [(x, y, l, w)],
                             "ancestors": (*surface["ancestors"], (depth, limit))})
    volume = sum(b["size_placed_cm"]["l"] * b["size_placed_cm"]["w"] * b["size_placed_cm"]["h"] for b in placed)
    return placed, unplaced, volume / (L * W * H) if L * W * H else 0.0


def _pack_vehicle(items: list[dict], vehicle: dict, rules: dict):
    """比较确定性安全启发式，按装下件数择优且保留原货架作为回退。"""
    best = _pack_vehicle_legacy(items, vehicle, rules)
    candidate = _pack_surfaces(items, vehicle, rules)
    return candidate if len(candidate[0]) > len(best[0]) else best


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
        total_weight = fsum(p["weight_kg"] for p in placed)
        capacity = float(vehicle["max_weight_kg"])
        outputs.append({"vehicle_instance": f"{route['route_id']}-1", "vehicle_type": route["vehicle_type"],
                        "placed": placed, "unplaced": unplaced, "volume_utilization": round(vol_rate, 4),
                        "weight_utilization": round(total_weight / capacity, 4) if capacity > 0 else 0.0})
    return {"status": "ok", "vehicles": outputs}
