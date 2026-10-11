"""根据已装货生成逆配送装载步骤，并核验车尾卸货阻挡。"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy


_EPS = 1e-7


def _interval_overlap(a, b, axis, size):
    ap, bp = a["position_cm"][axis], b["position_cm"][axis]
    return min(ap + a["size_placed_cm"][size],
               bp + b["size_placed_cm"][size]) - max(ap, bp) > _EPS


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
            if sum(mirrored_blocks.values()) < sum(blocks.values()):
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
