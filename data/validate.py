# -*- coding: utf-8 -*-
"""智载 CityLoad —— 数据集契约校验器（窗口B · T02 配套自检工具）

对照 `docs/interfaces.md` 第 1 章，校验一个数据目录中的 4 个文件
（orders.json / vehicles.json / network.json / rules.json）。

用法:
    python data/validate.py --data data/          # 校验 seed 42 组
    python data/validate.py --data data/seed7/    # 校验 seed 7 组

校验项:
    - 字段齐全性（契约要求的全部字段）
    - 取值约束（qty≥1、尺寸 5-40cm、重量 0.05-3kg、时间窗 结束>开始 等）
    - network 矩阵：维度一致 / 严格对称 / 对角线为零 / node_order 对齐
    - orders 与 network 门店坐标一致性
    - 门店距中心仓 3–25 km

退出码: 0 = 无错误；1 = 有错误（打印错误清单）
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

R_EARTH_KM = 6371.0088

ORDER_FIELDS = ("order_id", "store_id", "store_name", "lat", "lng",
                "time_window", "service_minutes", "items")
ITEM_FIELDS = ("sku", "name", "qty", "weight_kg", "length_cm", "width_cm",
               "height_cm", "fragile", "stackable", "max_stack_layers")
VEHICLE_FIELDS = ("vehicle_id", "type", "max_weight_kg", "cargo_length_cm",
                  "cargo_width_cm", "cargo_height_cm", "count", "cost_per_km",
                  "fixed_cost", "avg_speed_kmh", "max_work_minutes")


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """两经纬度点间大圆距离（km）。"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return 2.0 * R_EARTH_KM * math.asin(math.sqrt(a))


def load_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 各文件校验

def validate_orders(doc: dict, errs: list[str]) -> None:
    if not isinstance(doc, dict) or "orders" not in doc:
        errs.append("orders.json: 缺少顶层 orders 字段")
        return
    meta = doc.get("meta") or {}
    for k in ("scenario", "date", "seed", "warehouse"):
        if k not in meta:
            errs.append(f"orders.json meta: 缺少 {k}")
    if set((meta.get("warehouse") or {}).keys()) != {"id", "name", "lat", "lng"}:
        errs.append("orders.json meta.warehouse: 字段应为 id/name/lat/lng")

    seen_orders, seen_stores = set(), set()
    for o in doc["orders"]:
        oid = o.get("order_id", "?")
        for k in ORDER_FIELDS:
            if k not in o:
                errs.append(f"orders.json {oid}: 缺字段 {k}")
        if oid in seen_orders:
            errs.append(f"orders.json: order_id 重复 {oid}")
        seen_orders.add(oid)
        if o.get("store_id") in seen_stores:
            errs.append(f"orders.json: store_id 重复 {o.get('store_id')}")
        seen_stores.add(o.get("store_id"))
        tw = o.get("time_window") or []
        if len(tw) != 2 or tw[1] <= tw[0]:
            errs.append(f"orders.json {oid}: 时间窗无效 {tw}")
        for it in o.get("items") or []:
            tag = f"{oid}/{it.get('sku')}"
            for k in ITEM_FIELDS:
                if k not in it:
                    errs.append(f"orders.json {tag}: 缺字段 {k}")
            if it.get("qty", 0) < 1:
                errs.append(f"orders.json {tag}: qty<1")
            if not (5.0 <= it.get("length_cm", 0) <= 40.0 and
                    5.0 <= it.get("width_cm", 0) <= 40.0 and
                    5.0 <= it.get("height_cm", 0) <= 40.0):
                errs.append(f"orders.json {tag}: 尺寸越界（应 5-40cm）")
            if not (0.05 <= it.get("weight_kg", 0) <= 3.0):
                errs.append(f"orders.json {tag}: 重量越界（应 0.05-3.0kg）")
            if it.get("max_stack_layers", 0) < 1:
                errs.append(f"orders.json {tag}: max_stack_layers<1")


def validate_vehicles(doc: dict, errs: list[str]) -> None:
    for v in (doc.get("vehicles") or []):
        for k in VEHICLE_FIELDS:
            if k not in v:
                errs.append(f"vehicles.json {v.get('vehicle_id', '?')}: 缺字段 {k}")
    if not doc.get("vehicles"):
        errs.append("vehicles.json: vehicles 为空")


def validate_network(doc: dict, errs: list[str]) -> None:
    n = len(doc.get("node_order") or [])
    nodes = doc.get("nodes") or []
    dm = doc.get("distance_matrix_km") or []
    tm = doc.get("duration_matrix_min") or []
    if len(nodes) != n:
        errs.append("network.json: nodes 与 node_order 数量不一致")
    if len(dm) != n or any(len(r) != n for r in dm):
        errs.append("network.json: 距离矩阵维度与 node_order 不一致")
        return
    if len(tm) != n or any(len(r) != n for r in tm):
        errs.append("network.json: 时长矩阵维度与 node_order 不一致")
    if any(abs(dm[i][j] - dm[j][i]) > 1e-9 for i in range(n) for j in range(n)):
        errs.append("network.json: 距离矩阵不对称")
    if any(dm[i][i] != 0.0 for i in range(n)):
        errs.append("network.json: 距离矩阵对角线非零")
    if [nd.get("id") for nd in nodes] != list(doc.get("node_order") or []):
        errs.append("network.json: nodes 顺序与 node_order 不一致")


def validate_rules(doc: dict, errs: list[str]) -> None:
    for k in ("packing", "routing"):
        if k not in doc:
            errs.append(f"rules.json: 缺少 {k} 段")
    for k in ("allow_rotation", "rotation_axes", "consider_fragile",
              "consider_max_stack", "gap_cm"):
        if k not in (doc.get("packing") or {}):
            errs.append(f"rules.json packing: 缺少 {k}")
    for k in ("respect_time_window", "allow_overtime_penalty",
              "overtime_penalty_per_min", "unassigned_penalty"):
        if k not in (doc.get("routing") or {}):
            errs.append(f"rules.json routing: 缺少 {k}")


def cross_check(orders_doc: dict, network_doc: dict, errs: list[str]) -> None:
    """orders 与 network 的门店坐标一致性 + 门店 3–25km 范围。"""
    nodes = {nd.get("id"): (nd.get("lat"), nd.get("lng"))
             for nd in network_doc.get("nodes") or []}
    wh = (orders_doc.get("meta") or {}).get("warehouse") or {}
    for o in orders_doc.get("orders") or []:
        sid = o.get("store_id")
        if sid not in nodes:
            errs.append(f"cross: {sid} 不在 network 节点中")
            continue
        if nodes[sid] != (o.get("lat"), o.get("lng")):
            errs.append(f"cross: {sid} 坐标不一致（orders vs network）")
        if wh.get("lat") is not None:
            d = haversine_km(wh["lat"], wh["lng"], o["lat"], o["lng"])
            if not (3.0 <= d <= 25.0):
                errs.append(f"cross: {sid} 距中心仓 {d:.2f}km 超出 3–25km")


# ---------------------------------------------------------------- 主流程

def main() -> None:
    parser = argparse.ArgumentParser(description="数据集契约校验器（对照 docs/interfaces.md 第 1 章）")
    parser.add_argument("--data", default="data", help="数据目录（默认 data）")
    args = parser.parse_args()
    base = args.data

    try:
        orders = load_json(os.path.join(base, "orders.json"))
        vehicles = load_json(os.path.join(base, "vehicles.json"))
        network = load_json(os.path.join(base, "network.json"))
        rules = load_json(os.path.join(base, "rules.json"))
    except FileNotFoundError as e:
        print(f"[FAIL] 缺少文件: {e}")
        sys.exit(1)

    errs: list[str] = []
    validate_orders(orders, errs)
    validate_vehicles(vehicles, errs)
    validate_network(network, errs)
    validate_rules(rules, errs)
    cross_check(orders, network, errs)

    # ---- 统计摘要 ----
    olist = orders.get("orders") or []
    lines = [it for o in olist for it in o.get("items") or []]
    total_weight = sum(round(it["qty"] * it["weight_kg"], 4) for it in lines)
    capacity = sum(v["max_weight_kg"] * v["count"] for v in vehicles.get("vehicles") or [])
    n_pieces = sum(it["qty"] for it in lines)
    dists = []
    wh = (orders.get("meta") or {}).get("warehouse") or {}
    for o in olist:
        if wh.get("lat") is not None:
            dists.append(haversine_km(wh["lat"], wh["lng"], o["lat"], o["lng"]))

    print(f"=== 数据集校验: {base} ===")
    print(f"门店数: {len(olist)} | SKU 行: {len(lines)} | 件数: {n_pieces}")
    if capacity:
        print(f"总重: {total_weight:.1f} kg | 占车队额定载重: {total_weight / capacity * 100:.1f}%")
    if dists:
        print(f"门店距中心仓: {min(dists):.2f} ~ {max(dists):.2f} km")

    if errs:
        print(f"\n[FAIL] {len(errs)} 个错误:")
        for e in errs[:80]:
            print("  -", e)
        sys.exit(1)
    print("\n[PASS] 0 错误 —— 完全符合 docs/interfaces.md 第 1 章契约")


if __name__ == "__main__":
    main()
