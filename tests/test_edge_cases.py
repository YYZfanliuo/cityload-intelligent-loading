# -*- coding: utf-8 -*-
"""T33 边界 case 测试（窗口B）——订单超载 / 易碎品堆叠 / 运力不足

在真实数据（data/，seed 42）及其派生的极端输入上实测，覆盖 T33 要求的边界场景：
    - test_insufficient_fleet_reports_unassigned  ：运力不足导致订单未分配
    - test_overload_single_order_graceful         ：单订单重量 > 单车载重
    - test_fragile_not_compressed_real_data       ：易碎品堆叠（真实数据全链路几何验证）
    - test_fragile_pressure_never_stacked_illegally：易碎品堆叠（压力场景，单层放不下）

断言原则：只锁「优雅性」契约 —— 不崩溃、结构合法、硬约束不被违反、
异常被如实报告（unassigned / unplaced）；不锁优化优度（那是窗口C 实验的职责）。

运行：pytest tests/test_edge_cases.py -v
"""

from __future__ import annotations

import copy
import json
import os

import pytest

from solver.packing import solve_packing
from solver.routing import solve_routing

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOL = 0.05


def _load(name: str) -> dict:
    with open(os.path.join(ROOT, "data", name), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def base():
    return (_load("orders.json"), _load("vehicles.json"),
            _load("network.json"), _load("rules.json"))


@pytest.fixture(scope="module")
def full_pipeline(base):
    """全链路缓存：真实数据跑一次 routing + packing，供易碎几何验证共享。"""
    orders, vehicles, network, rules = base
    routes = solve_routing(orders, vehicles, network, rules)
    packing = solve_packing(routes, orders, vehicles, rules)
    return routes, packing


def _item_map(orders: dict) -> dict:
    return {(o["order_id"], it["sku"]): it
            for o in orders["orders"] for it in o["items"]}


def _fragile_violations(packing: dict, item_map: dict, tol: float = TOL) -> int:
    """几何独立验证：统计「被压 / 与任何货物相交」的易碎品件数（应为 0）。"""
    viol = 0
    for v in packing.get("vehicles", []):
        placed = v.get("placed", [])
        frags = [it for it in placed
                 if (item_map.get((it["order_id"], it["sku"])) or {}).get("fragile")]
        for F in frags:
            fx, fy, fz = F["position_cm"]["x"], F["position_cm"]["y"], F["position_cm"]["z"]
            fl, fw, fh = (F["size_placed_cm"]["l"], F["size_placed_cm"]["w"], F["size_placed_cm"]["h"])
            fx2, fy2, fz2 = fx + fl, fy + fw, fz + fh
            for P in placed:
                if P is F:
                    continue
                px, py, pz = P["position_cm"]["x"], P["position_cm"]["y"], P["position_cm"]["z"]
                pl, pw, ph = (P["size_placed_cm"]["l"], P["size_placed_cm"]["w"], P["size_placed_cm"]["h"])
                if not (px < fx2 - tol and px + pl > fx + tol and
                        py < fy2 - tol and py + pw > fy + tol):
                    continue
                on_top = abs(pz - fz2) < tol                          # 直接压在易碎品顶面
                intersect = pz < fz2 - tol and pz + ph > fz + tol     # 与易碎品体积相交
                if on_top or intersect:
                    viol += 1
    return viol


def _count_pieces(lst) -> int:
    """unplaced 列表的件数（元素为 {sku, order_id, store_id, qty} 字典）。"""
    return sum(int(u.get("qty", 1)) if isinstance(u, dict) else 1 for u in lst)


# ---------------------------------------------------------------- 边界 1：运力不足

def test_insufficient_fleet_reports_unassigned(base):
    """运力不足：5700kg 货量只配 1 辆 V01(1700kg) → partial + 未分配如实报告。

    期望：不崩溃；最多 1 辆车上路；未分配订单非空；
    不丢单（已服务门店 + 未分配订单 == 总订单数）；载重硬约束不被违反。
    """
    orders, vehicles, network, rules = base
    v_short = copy.deepcopy(vehicles)
    for v in v_short["vehicles"]:
        v["count"] = 1 if v["vehicle_id"] == "V01" else 0

    r = solve_routing(orders, v_short, network, rules)

    assert r["status"] in ("ok", "partial")
    assert len(r["routes"]) <= 1
    assert len(r["unassigned_orders"]) > 0
    served = {d["node"] for rt in r["routes"] for d in rt.get("stop_details", [])}
    assert len(served) + len(r["unassigned_orders"]) == len(orders["orders"])
    vmap = {v["vehicle_id"]: v for v in vehicles["vehicles"]}
    for rt in r["routes"]:
        assert rt["total_load_kg"] <= vmap[rt["vehicle_type"]]["max_weight_kg"] + 1e-6


# ---------------------------------------------------------------- 边界 2：单订单超载

def test_overload_single_order_graceful(base):
    """单订单超载：S01 放大至 ~5.2t（> 单车 1700kg）→ 该单被拒收，不硬塞、不崩溃。

    期望：O0001 进入 unassigned；S01 不出现在任何 route；
    其余订单绝大多数正常服务；所有 route 载重不超限。
    """
    orders, vehicles, network, rules = base
    o_over = copy.deepcopy(orders)
    s1 = o_over["orders"][0]                      # O0001 / S01
    for it in s1["items"]:
        it["qty"] *= 25
    assert sum(it["qty"] * it["weight_kg"] for it in s1["items"]) > 1700  # 场景前提

    r = solve_routing(o_over, vehicles, network, rules)

    assert r["status"] in ("ok", "partial")
    assert "O0001" in r["unassigned_orders"]
    served = {d["node"] for rt in r["routes"] for d in rt.get("stop_details", [])}
    assert "S01" not in served                    # 不得超载硬塞
    assert len(served) >= 28                      # 其余订单正常服务
    vmap = {v["vehicle_id"]: v for v in vehicles["vehicles"]}
    for rt in r["routes"]:
        assert rt["total_load_kg"] <= vmap[rt["vehicle_type"]]["max_weight_kg"] + 1e-6


# ---------------------------------------------------------------- 边界 3：易碎品堆叠

def test_fragile_not_compressed_real_data(base, full_pipeline):
    """易碎品堆叠（真实数据全链路）：几何独立验证 0 违规 —— 无一易碎品被压/相交。"""
    orders, _, _, _ = base
    _, packing = full_pipeline
    im = _item_map(orders)
    placed_fragile = sum(
        1 for v in packing.get("vehicles", []) for it in v.get("placed", [])
        if (im.get((it["order_id"], it["sku"])) or {}).get("fragile")
    )
    assert placed_fragile > 0                     # 场景非空：确有易碎品被放置
    assert _fragile_violations(packing, im) == 0


def test_fragile_pressure_never_stacked_illegally():
    """易碎品压力场景：32 件大易碎箱（单层放不下）→ 装不下的进 unplaced，不得违规叠放。

    车厢 V02 底面 340×180 = 61200 cm²，32 件易碎箱底面积 50×45×32 = 72000 cm²，
    单层物理放不下 → 算法必须「部分拒装」或「合法垫高」，但绝不允许易碎品被压。
    期望：不崩溃；0 几何违规；数量守恒（placed + unplaced == 40 件）。
    """
    items = []
    for i in range(32):
        items.append({"sku": "SKU9101", "name": f"易碎大箱{i + 1}", "qty": 1,
                      "weight_kg": 2.0, "length_cm": 50.0, "width_cm": 45.0,
                      "height_cm": 35.0, "fragile": True, "stackable": True,
                      "max_stack_layers": 5})
    for i in range(8):
        items.append({"sku": "SKU9201", "name": f"普通箱{i + 1}", "qty": 1,
                      "weight_kg": 1.0, "length_cm": 40.0, "width_cm": 40.0,
                      "height_cm": 40.0, "fragile": False, "stackable": True,
                      "max_stack_layers": 5})
    assert 32 * 50 * 45 > 340 * 180               # 场景前提：单层放不下

    orders = {
        "meta": {"scenario": "pharmacy_chain", "date": "2026-10-12", "seed": 0,
                 "warehouse": {"id": "WH01", "name": "中心仓", "lat": 30.2741, "lng": 120.1551}},
        "orders": [{"order_id": "O9001", "store_id": "S90", "store_name": "压力测试店",
                    "lat": 30.28, "lng": 120.16, "time_window": ["08:00", "12:00"],
                    "service_minutes": 15, "items": items}],
    }
    vehicles = {"vehicles": [{
        "vehicle_id": "V02", "type": "依维柯", "max_weight_kg": 1000.0,
        "cargo_length_cm": 340.0, "cargo_width_cm": 180.0, "cargo_height_cm": 180.0,
        "count": 1, "cost_per_km": 2.8, "fixed_cost": 80.0,
        "avg_speed_kmh": 30.0, "max_work_minutes": 480}]}
    routes = {"status": "ok", "routes": [{
        "route_id": "R90", "vehicle_type": "V02",
        "sequence": ["WH01", "S90", "WH01"],
        "stop_details": [{"node": "S90", "arrive": "08:30", "start_service": "08:30",
                          "end_service": "08:45", "load_kg": 72.0}]}],
        "unassigned_orders": []}

    p = solve_packing(routes, orders, vehicles, _load("rules.json"))

    assert p["status"] == "ok"
    im = _item_map(orders)
    assert _fragile_violations(p, im) == 0
    total_in = sum(it["qty"] for it in items)
    total_out = sum(len(v.get("placed", [])) + _count_pieces(v.get("unplaced", []))
                    for v in p["vehicles"])
    assert total_out == total_in                  # 数量守恒：不凭空消失
