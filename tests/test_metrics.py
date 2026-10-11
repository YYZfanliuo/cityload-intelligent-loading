# -*- coding: utf-8 -*-
"""tests/test_metrics.py —— 评估指标模块单测（T15 · 窗口B）

包含 3 个手算验证 case（成本 / 载重率 / 体积率 / 时效），
及空路由、重复停靠、未分配订单格式兼容等边界测试。
运行：pytest tests/test_metrics.py -v
"""

import os

import pytest

from eval.metrics import compute_metrics


# ---------------------------------------------------------------- 共用构造

def _base_orders(n_stores=2):
    orders = []
    for i in range(1, n_stores + 1):
        orders.append({
            "order_id": f"O{i:04d}",
            "store_id": f"S{i:02d}",
            "store_name": f"连锁药店{i:02d}",
            "lat": 30.28, "lng": 120.16,
            "time_window": ["08:00", "12:00"],
            "service_minutes": 15,
            "items": [],
        })
    return {
        "meta": {
            "scenario": "pharmacy_chain", "date": "2026-10-12", "seed": 42,
            "warehouse": {"id": "WH01", "name": "中心仓", "lat": 30.2741, "lng": 120.1551},
        },
        "orders": orders,
    }


def _item(qty, weight, l=20.0, w=20.0, h=25.0):
    return {
        "sku": "SKU0001", "name": "测试品", "qty": qty, "weight_kg": weight,
        "length_cm": l, "width_cm": w, "height_cm": h,
        "fragile": False, "stackable": True, "max_stack_layers": 5,
    }


def _base_vehicles():
    return {"vehicles": [{
        "vehicle_id": "V01", "type": "4.2米厢式货车",
        "max_weight_kg": 1000.0,
        "cargo_length_cm": 420.0, "cargo_width_cm": 200.0, "cargo_height_cm": 200.0,
        "count": 4, "cost_per_km": 3.5, "fixed_cost": 120.0,
        "avg_speed_kmh": 30.0, "max_work_minutes": 480,
    }]}


def _base_network():
    return {
        "nodes": [
            {"id": "WH01", "lat": 30.2741, "lng": 120.1551},
            {"id": "S01", "lat": 30.28, "lng": 120.16},
            {"id": "S02", "lat": 30.29, "lng": 120.17},
        ],
        "distance_matrix_km": [[0.0, 10.0, 12.0], [10.0, 0.0, 5.0], [12.0, 5.0, 0.0]],
        "duration_matrix_min": [[0.0, 20.0, 24.0], [20.0, 0.0, 10.0], [24.0, 10.0, 0.0]],
        "node_order": ["WH01", "S01", "S02"],
    }


# ---------------------------------------------------------------- 手算 Case

def test_case_a_hand_calc_cost_weight_on_time():
    """手算 Case A：里程/成本/载重率全缺失字段时必须能重算，且数值精确。

    手算：S01=100件×3.0kg=300kg、S02=250件×2.0kg=500kg；
    里程 = 10+5+12 = 27 km；成本 = 120 + 27×3.5 = 214.5；
    载重率 = (300+500)/1000 = 0.8；两店均 12:00 前到达 → on_time = 2/2 = 1.0；
    体积回退 = (100×0.01 + 250×0.001)/16.8 = 1.25/16.8 ≈ 0.0744。
    """
    orders = _base_orders(2)
    orders["orders"][0]["items"] = [_item(qty=100, weight=3.0)]          # 300 kg, 1.0 m³
    orders["orders"][1]["items"] = [_item(qty=250, weight=2.0, l=10, w=10, h=10)]  # 500 kg, 0.25 m³

    result = {
        "status": "ok",
        "routes": [{
            "route_id": "R01", "vehicle_type": "V01",
            "sequence": ["WH01", "S01", "S02", "WH01"],
            "stop_details": [
                {"node": "S01", "arrive": "08:30", "start_service": "08:30",
                 "end_service": "08:45", "load_kg": 300.0},
                {"node": "S02", "arrive": "09:40", "start_service": "09:40",
                 "end_service": "09:55", "load_kg": 500.0},
            ],
            # total_distance_km / total_load_kg / cost 故意缺失 → 走重算分支
        }],
        "unassigned_orders": [],
    }

    out = compute_metrics(result, orders, _base_vehicles(), _base_network())
    m = out["metrics"]

    assert out["scenario"] == "pharmacy_chain"
    assert out["seed"] == 42
    assert m["vehicle_count"] == 1
    assert m["total_distance_km"] == pytest.approx(27.0)
    assert m["total_cost"] == pytest.approx(214.5)              # 120 + 27×3.5
    assert m["avg_weight_load_rate"] == pytest.approx(0.8)      # 800 / 1000
    assert m["avg_volume_utilization"] == pytest.approx(1.25 / 16.8, abs=1e-4)
    assert m["on_time_rate"] == pytest.approx(1.0)
    assert m["unassigned_order_count"] == 0
    assert m["solve_time_sec"] == 0.0                           # v1.1：缺失按 0 计
    assert any("solve_time_sec" in w for w in out.get("warnings", []))  # 且带标注
    assert len(out["per_route"]) == 1
    assert out["per_route"][0]["load_rate_weight"] == pytest.approx(0.8)


def test_case_b_hand_calc_packing_unassigned_partial_on_time():
    """手算 Case B：packing 体积率、未分配订单、部分按时、solve_time 读取。

    手算：packing 体积率取 (0.62+0.34)/2 = 0.48；
    按时 = S01 一店 / 总 3 店 = 1/3；未分配 = 1；
    R02 无 cost 字段 → 120 + 30×3.5 = 225；总成本 = 190 + 225 = 415。
    """
    orders = _base_orders(3)
    orders["orders"][0]["time_window"] = ["08:00", "12:00"]
    orders["orders"][1]["time_window"] = ["12:00", "16:00"]
    orders["orders"][2]["time_window"] = ["14:00", "18:00"]
    for o in orders["orders"]:
        o["items"] = [_item(qty=1, weight=1.0)]

    result = {
        "status": "ok",
        "solve_time_sec": 12.3,
        "routes": [
            {"route_id": "R01", "vehicle_type": "V01",
             "sequence": ["WH01", "S01", "WH01"],
             "stop_details": [{"node": "S01", "arrive": "08:10", "load_kg": 1.0}],
             "total_distance_km": 20.0, "cost": 190.0, "total_load_kg": 1.0},
            {"route_id": "R02", "vehicle_type": "V01",
             "sequence": ["WH01", "S02", "WH01"],
             "stop_details": [{"node": "S02", "arrive": "16:30", "load_kg": 1.0}],  # 晚点 30 分钟
             "total_distance_km": 30.0},   # cost 缺失 → 120 + 30×3.5 = 225
        ],
        "unassigned_orders": ["O0003"],
        "packing": {
            "status": "ok",
            "vehicles": [
                {"vehicle_instance": "R01-1", "vehicle_type": "V01", "volume_utilization": 0.62},
                {"vehicle_instance": "R02-1", "vehicle_type": "V01", "volume_utilization": 0.34},
            ],
        },
    }

    out = compute_metrics(result, orders, _base_vehicles(), _base_network())
    m = out["metrics"]

    assert m["vehicle_count"] == 2
    assert m["total_distance_km"] == pytest.approx(50.0)
    assert m["total_cost"] == pytest.approx(415.0)              # 190 + 225
    assert m["avg_volume_utilization"] == pytest.approx(0.48)   # (0.62+0.34)/2
    assert m["on_time_rate"] == pytest.approx(1.0 / 3.0, abs=1e-4)
    assert m["unassigned_order_count"] == 1
    assert m["solve_time_sec"] == pytest.approx(12.3)
    # per_route 中 R02 的 volume 应从 packing 关联
    r02 = [p for p in out["per_route"] if p["route_id"] == "R02"][0]
    assert r02["volume_utilization"] == pytest.approx(0.34)


def test_case_c_empty_routes_edge():
    """边界：空路由（无车辆出动）不崩溃，全部归零。"""
    orders = _base_orders(2)
    result = {"status": "ok", "routes": [], "unassigned_orders": []}

    out = compute_metrics(result, orders, _base_vehicles(), _base_network())
    m = out["metrics"]

    assert m["vehicle_count"] == 0
    assert m["total_distance_km"] == 0.0
    assert m["total_cost"] == 0.0
    assert m["avg_weight_load_rate"] == 0.0
    assert m["avg_volume_utilization"] == 0.0
    assert m["on_time_rate"] == 0.0     # 有 2 家门店但都没被服务
    assert m["unassigned_order_count"] == 0


def test_case_d_dedup_and_dict_unassigned():
    """边界：同店重复出现只计一次时效；unassigned 支持字典元素；solve_time 从 summary 读。"""
    orders = _base_orders(2)
    for o in orders["orders"]:
        o["items"] = [_item(qty=1, weight=1.0)]

    result = {
        "status": "ok",
        "summary": {"solve_time_sec": 9.9, "vehicle_count": 2},
        "routes": [
            {"route_id": "R01", "vehicle_type": "V01",
             "sequence": ["WH01", "S01", "WH01"],
             "stop_details": [{"node": "S01", "arrive": "09:00", "load_kg": 1.0}]},
            {"route_id": "R02", "vehicle_type": "V01",
             "sequence": ["WH01", "S01", "WH01"],   # S01 重复出现，不得重复计数
             "stop_details": [{"node": "S01", "arrive": "09:00", "load_kg": 1.0}]},
        ],
        "unassigned_orders": [{"order_id": "O0002", "reason": "超载"}],
    }

    out = compute_metrics(result, orders, _base_vehicles(), _base_network())
    m = out["metrics"]

    assert m["on_time_rate"] == pytest.approx(0.5)      # S01 记 1 次 / 总 2 店
    assert m["unassigned_order_count"] == 1             # 字典元素可计数
    assert m["solve_time_sec"] == pytest.approx(9.9)    # 从 summary 读取


# ---------------------------------------------------------------- 真实数据冒烟

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


@pytest.mark.skipif(
    not os.path.exists(os.path.join(DATA_DIR, "orders.json")),
    reason="data/orders.json 不存在（需先运行 data/gen_data.py）",
)
def test_smoke_with_real_dataset():
    """冒烟：用真实生成数据集构造单一路径，指标可计算且结构完整。"""
    import json

    def load(name):
        with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
            return json.load(f)

    orders = load("orders.json")
    vehicles = load("vehicles.json")
    network = load("network.json")

    # 构造：一辆车按 node_order 依次服务全部门店
    seq = list(network["node_order"]) + ["WH01"]
    result = {
        "status": "ok",
        "solve_time_sec": 1.0,   # 可选元数据放 result 顶层（约定见 eval/metrics.py 模块说明）
        "routes": [{
            "route_id": "R01", "vehicle_type": "V01",
            "sequence": seq,
            "stop_details": [
                {"node": sid, "arrive": "10:00", "load_kg": 100.0}
                for sid in network["node_order"][1:]
            ],
        }],
        "unassigned_orders": [],
    }

    out = compute_metrics(result, orders, vehicles, network)
    m = out["metrics"]

    assert m["vehicle_count"] == 1
    assert m["total_distance_km"] > 0
    assert m["total_cost"] > 0
    assert m["solve_time_sec"] == pytest.approx(1.0)
    assert len(out["per_route"]) == 1
    assert out["per_route"][0]["stop_count"] == 30
