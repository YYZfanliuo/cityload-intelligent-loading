"""根据已装货生成逆配送装载步骤，并核验车尾卸货阻挡。"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy


_EPS = 1e-7


def _interval_overlap(a, b, axis, size):
    ap, bp = a["position_cm"][axis], b["position_cm"][axis]
    return min(ap + a["size_placed_cm"][size],
               bp + b["size_placed_cm"][size]) - max(ap, bp) > _EPS


def _strict_overlap(a, b, axis, side):
    """用于坐标修改安全门禁，接触面必须保持非正相交。"""
    ap, bp = a["position_cm"][axis], b["position_cm"][axis]
    return ap < bp + b["size_placed_cm"][side] and bp < ap + a["size_placed_cm"][side]


def _blocks(placed, ranks):
    """按门店实际卸货时仍留车的货物检测；同店货物可以先卸顶部/外侧。"""
    blocks = Counter()
    for early in placed:
        for late in placed:
            if ranks[early["store_id"]] >= ranks[late["store_id"]]:
                continue
            ep, lp = early["position_cm"], late["position_cm"]
            ed, ld = early["size_placed_cm"], late["size_placed_cm"]
            if (_interval_overlap(early, late, "x", "l") and
                    _interval_overlap(early, late, "y", "w") and
                    lp["z"] >= ep["z"] + ed["h"] - _EPS):
                blocks[(early["store_id"], late["store_id"], "上下压")] += 1
            if (_interval_overlap(early, late, "y", "w") and
                    _interval_overlap(early, late, "z", "h") and
                    lp["x"] + ld["l"] <= ep["x"] + _EPS):
                blocks[(early["store_id"], late["store_id"], "车尾通道")] += 1
    return blocks


def sequence_loading(packing: dict, routes: dict, rules: dict) -> dict:
    """返回冻结 loading_plan 接口，仅统计 placed 的逐件货物。

    原点在车尾，卸货通道假设为保持 y/z 的沿负 x 直线抽取。
    如整体 x 镜像能严格减少阻挡对数，将就地更新该车 placed 的 x；
    使用已占用区间镜像，无需车型尺寸，保持边界、支撑、间隙与无重叠。
    不改变任何 z、尺寸、层数、装箱数量或 unplaced，也不重排 placed 列表。
    不能解决的跨店堆叠/通道阻挡如实告警，不宣称可完成全部订单。
    rules 的装箱约束无需重新放宽：几何镜像原样保留已有安全条件。
    """
    by_route = {route["route_id"]: route for route in routes.get("routes", [])}
    plans = []
    for vehicle in packing.get("vehicles", []):
        instance = vehicle["vehicle_instance"]
        route_id = instance.rsplit("-", 1)[0]
        if route_id not in by_route:
            raise ValueError(f"车辆实例 {instance} 没有匹配的 route_id")
        delivery = list(by_route[route_id].get("sequence", [])[1:-1])
        ranks = {store: index for index, store in enumerate(delivery)}
        placed = vehicle.get("placed", [])
        for box in placed:
            if box["store_id"] not in ranks:
                raise ValueError(f"已装货门店 {box['store_id']} 不在路线 {route_id} 中")
        blocks = _blocks(placed, ranks)
        if blocks and placed:
            left = min(box["position_cm"]["x"] for box in placed)
            right = max(box["position_cm"]["x"] + box["size_placed_cm"]["l"]
                        for box in placed)
            mirrored = deepcopy(placed)
            for box in mirrored:
                box["position_cm"]["x"] = (left + right - box["position_cm"]["x"]
                                             - box["size_placed_cm"]["l"])
            mirrored_blocks = _blocks(mirrored, ranks)
            # 浮点镜像可能将接触面变成极小正相交；严格检查后才采用。
            geometry_safe = True
            gap = max(0.0, float(rules.get("packing", {}).get("gap_cm", 0.0)))
            for i, box in enumerate(mirrored):
                p, d = box["position_cm"], box["size_placed_cm"]
                if p["x"] < left or p["x"] + d["l"] > right:
                    geometry_safe = False
                    break
                if p["z"] > 0 and not any(
                        base is not box and
                        base["position_cm"]["z"] + base["size_placed_cm"]["h"] == p["z"] and
                        all(base["position_cm"][a] <= p[a] and
                            p[a] + d[s] <= base["position_cm"][a] + base["size_placed_cm"][s]
                            for a, s in (("x", "l"), ("y", "w")))
                        for base in mirrored):
                    geometry_safe = False
                    break
                for j, other in enumerate(mirrored):
                    if i == j:
                        continue
                    old, old_other = placed[i], placed[j]
                    op, od = other["position_cm"], other["size_placed_cm"]
                    old_p, old_d = old["position_cm"], old["size_placed_cm"]
                    old_op, old_od = old_other["position_cm"], old_other["size_placed_cm"]
                    if all(_strict_overlap(box, other, axis, side)
                           for axis, side in zip("xyz", "lwh")):
                        geometry_safe = False
                        break
                    # 保留原支撑箱关系，严格拒绝镜像舍入导致的微小悬空。
                    supported = (old_p["z"] > 0 and
                                 old_op["z"] + old_od["h"] == old_p["z"] and
                                 all(old_op[a] <= old_p[a] and
                                     old_p[a] + old_d[s] <= old_op[a] + old_od[s]
                                     for a, s in (("x", "l"), ("y", "w"))))
                    if supported and not (op["x"] <= p["x"] and
                                          p["x"] + d["l"] <= op["x"] + od["l"]):
                        geometry_safe = False
                        break
                    # 新增足迹相交可能令易碎件承压，即使三维箱体并未重叠。
                    if (not _strict_overlap(old, old_other, "x", "l") and
                            _strict_overlap(box, other, "x", "l") and
                            _strict_overlap(box, other, "y", "w")):
                        geometry_safe = False
                        break
                    old_gap = max(old_op["x"] - old_p["x"] - old_d["l"],
                                  old_p["x"] - old_op["x"] - old_od["l"])
                    new_gap = max(op["x"] - p["x"] - d["l"],
                                  p["x"] - op["x"] - od["l"])
                    if (gap > 0 and old_gap >= gap and
                            _strict_overlap(box, other, "y", "w") and
                            _strict_overlap(box, other, "z", "h") and new_gap < gap):
                        geometry_safe = False
                        break
                if not geometry_safe:
                    break
            if geometry_safe and sum(mirrored_blocks.values()) < sum(blocks.values()):
                for original, reflected in zip(placed, mirrored):
                    original["position_cm"]["x"] = reflected["position_cm"]["x"]
                blocks = mirrored_blocks
        quantities = {}
        for box in placed:
            quantities.setdefault(box["store_id"], Counter())[box["sku"]] += 1
        steps = []
        for store in reversed(delivery):
            if store not in quantities:
                continue
            steps.append({"step": len(steps) + 1, "action": "装", "store_id": store,
                          "items": [{"sku": sku, "qty": qty}
                                    for sku, qty in sorted(quantities[store].items())],
                          "note": "按配送逆序装入；仅含已装箱货物，未装箱货物不计入本步骤"})
        warnings = [f"{early} 的货物被后送门店 {late} 的货物阻挡（{kind}，{count} 对）；"
                    "需调整装箱或在卸货时搬移，不能保证无翻箱卸货"
                    for (early, late, kind), count in sorted(blocks.items())]
        plans.append({"vehicle_instance": instance, "delivery_order": delivery,
                      "loading_steps": steps, "blocks_warning": warnings})
    return {"status": "ok", "plans": plans}
