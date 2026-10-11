# -*- coding: utf-8 -*-
"""智载 CityLoad —— 模拟数据生成器（T02 · 窗口B）

业务场景：连锁药店配送。中心仓（杭州萧山）每天向门店配送药品，
SKU 含不同规格的 OTC 药品、保健品、日用品。

生成 4 个数据集到 --out 目录（结构见 docs/interfaces.md 第 1 章）：
    orders.json    订单（门店坐标 / 时间窗 / SKU 明细）
    vehicles.json  运力（2 种车型）
    network.json   路网（节点 + 距离矩阵 + 时长矩阵）
    rules.json     装载与调度规则

用法:
    python data/gen_data.py --stores 30 --seed 42 --out data/

特性:
    - 纯 Python 标准库（无第三方依赖），Python 3.10+
    - 同 seed 完全可复现（字节级一致）；换 seed 数据不同
    - 自动校准总货重到车队额定载重的 55%~75% 区间内（目标 ~58%），
      保证 2~4 辆车可装下且保留优化空间
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random

# ---------------------------------------------------------------- 常量定义

R_EARTH_KM = 6371.0088
KM_PER_LAT_DEG = 111.32  # 纬度 1 度对应公里数（局部近似）

WAREHOUSE = {
    "id": "WH01",
    "name": "中心仓",
    "lat": 30.2741,
    "lng": 120.1551,
}

SCENARIO = "pharmacy_chain"
DATE = "2026-10-12"

# 三类时间窗（等概率随机分配）
TIME_WINDOWS = [
    ["08:00", "12:00"],
    ["12:00", "16:00"],
    ["14:00", "18:00"],
]

# 运力池：4.2 米厢式货车 x4 + 依维柯 x3（额定总载重 9800 kg）
VEHICLES = [
    {
        "vehicle_id": "V01",
        "type": "4.2米厢式货车",
        "max_weight_kg": 1700.0,
        "cargo_length_cm": 420.0,
        "cargo_width_cm": 200.0,
        "cargo_height_cm": 200.0,
        "count": 4,
        "cost_per_km": 3.5,
        "fixed_cost": 120.0,
        "avg_speed_kmh": 30.0,
        "max_work_minutes": 480,
    },
    {
        "vehicle_id": "V02",
        "type": "依维柯",
        "max_weight_kg": 1000.0,
        "cargo_length_cm": 340.0,
        "cargo_width_cm": 180.0,
        "cargo_height_cm": 180.0,
        "count": 3,
        "cost_per_km": 2.8,
        "fixed_cost": 80.0,
        "avg_speed_kmh": 30.0,
        "max_work_minutes": 480,
    },
]

# 装载与调度规则（与 docs/interfaces.md 1.4 节一致）
RULES = {
    "packing": {
        "allow_rotation": True,
        "rotation_axes": ["lwh", "wlh", "hlw"],
        "consider_fragile": True,
        "consider_max_stack": True,
        "gap_cm": 0.0,
    },
    "routing": {
        "respect_time_window": True,
        "allow_overtime_penalty": True,
        "overtime_penalty_per_min": 2.0,
        "unassigned_penalty": 1000.0,
    },
}

# ---------------------------------------------------------------- SKU 目录

# 三个品类：OTC 药品 20 名 / 保健品 15 名 / 日用品 15 名，每名 3 个规格变体，
# 共 150 个 SKU。尺寸按品类分层（仍在接口要求的 5~40 cm 范围内），
# 更贴近真实包装；单件重量统一 U(0.05, 3.0) kg。
CATEGORIES = [
    {
        "key": "otc",
        "size_range": (6.0, 18.0),
        "names": [
            "感冒灵颗粒", "布洛芬缓释胶囊", "阿莫西林胶囊", "藿香正气水",
            "六味地黄丸", "维C银翘片", "复方甘草片", "皮炎平软膏",
            "云南白药气雾剂", "红霉素软膏", "蒙脱石散", "健胃消食片",
            "板蓝根颗粒", "双黄连口服液", "川贝枇杷膏", "蒲地蓝消炎片",
            "开塞露", "创可贴", "医用外科口罩", "碘伏消毒液",
        ],
        "specs": [
            "10g*9袋", "0.3g*24粒", "0.25g*36粒", "10ml*10支", "200丸",
            "12片*3板", "100ml", "50片", "10只装", "24片*2板",
        ],
    },
    {
        "key": "health",
        "size_range": (9.0, 26.0),
        "names": [
            "维生素C咀嚼片", "碳酸钙D3片", "深海鱼油软胶囊", "乳清蛋白粉",
            "阿胶固元糕", "益生菌冻干粉", "叶黄素酯片", "复合维生素片",
            "褪黑素片", "蜂胶软胶囊", "螺旋藻片", "铁质叶酸片",
            "葡萄糖酸锌口服液", "胶原蛋白肽粉", "辅酶Q10软胶囊",
        ],
        "specs": [
            "100片", "60粒*2瓶", "400g", "250ml", "90片",
            "1.2g*60袋", "500g", "30袋", "60片", "200g",
        ],
    },
    {
        "key": "daily",
        "size_range": (12.0, 32.0),
        "names": [
            "抽纸", "洗手液", "洗衣液", "牙膏", "洗发水",
            "沐浴露", "婴儿湿巾", "垃圾袋", "84消毒液", "洗洁精",
            "一次性纸杯", "棉签", "护手霜", "驱蚊液", "暖宝宝贴",
        ],
        "specs": [
            "130抽*3包", "500ml", "2kg", "120g", "400ml",
            "750ml", "80抽*3包", "45*50cm 90只", "1.25L", "150只",
        ],
    },
]

FRAGILE_RATIO = 0.08          # 约 8% 的 SKU 标记易碎
NON_STACKABLE_RATIO = 0.15    # 约 15% 标记 stackable=False

# 总重校准目标：车队额定 9800 kg 的 55%~75% 区间内，收敛到 ~58%
CALIBRATE_LO = 5500.0
CALIBRATE_HI = 5850.0
CALIBRATE_IDEAL = 5700.0


# ---------------------------------------------------------------- 工具函数

def clamp(v: float, lo: float, hi: float) -> float:
    """将数值截断到 [lo, hi] 区间。"""
    return max(lo, min(hi, v))


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """两经纬度点间的大圆距离（公里），Haversine 公式。"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return 2.0 * R_EARTH_KM * math.asin(math.sqrt(a))


# ---------------------------------------------------------------- 生成逻辑

def _quota_fix(rng: random.Random, catalog: list[dict], field: str, target: int) -> None:
    """配额校正：把目录中 field 为 True 的 SKU 数量精确调整到 target（确定性）。

    对 stackable 联动维护 max_stack_layers：不可堆叠 = 1，可堆叠取 3~8。
    """
    hit = [c for c in catalog if c[field]]
    miss = [c for c in catalog if not c[field]]
    if len(hit) < target:                     # 补足
        for c in rng.sample(miss, target - len(hit)):
            c[field] = True
    elif len(hit) > target:                   # 削减
        for c in rng.sample(hit, len(hit) - target):
            c[field] = False
    if field == "stackable":
        for c in catalog:
            c["max_stack_layers"] = rng.randint(3, 8) if c["stackable"] else 1


def build_sku_catalog(rng: random.Random) -> list[dict]:
    """构建 150 个 SKU 的全局目录（编码、名称、物理属性、装载属性）。

    fragile / stackable 先按概率生成，再做目录级配额校正，
    使整体比例精确贴近 8% / 15%（消除小样本随机波动）。
    """
    catalog: list[dict] = []
    idx = 0
    for cat in CATEGORIES:
        lo, hi = cat["size_range"]
        for name in cat["names"]:
            specs = rng.sample(cat["specs"], 3)  # 每名 3 个不同规格变体
            for spec in specs:
                idx += 1
                s = rng.uniform(lo, hi)
                length = clamp(round(s * rng.uniform(1.0, 1.3), 1), 5.0, 40.0)
                width = clamp(round(s * rng.uniform(0.7, 1.1), 1), 5.0, 40.0)
                height = clamp(round(s * rng.uniform(0.5, 0.9), 1), 5.0, 40.0)
                stackable = rng.random() >= NON_STACKABLE_RATIO
                catalog.append({
                    "sku": f"SKU{idx:04d}",
                    "name": f"{name} {spec}",
                    "weight_kg": round(rng.uniform(0.05, 3.0), 2),
                    "length_cm": length,
                    "width_cm": width,
                    "height_cm": height,
                    "fragile": False,
                    "stackable": stackable,
                    "max_stack_layers": 1,
                })
    # 配额校正（顺序固定，保证确定性）
    _quota_fix(rng, catalog, "fragile", round(len(catalog) * FRAGILE_RATIO))
    _quota_fix(rng, catalog, "stackable", len(catalog) - int(len(catalog) * NON_STACKABLE_RATIO + 0.5))
    return catalog


def gen_store_location(rng: random.Random) -> tuple[float, float]:
    """生成门店坐标：以中心仓为圆心、半径 3~25 km 内随机，Haversine 校验。"""
    cos_lat = math.cos(math.radians(WAREHOUSE["lat"]))
    while True:
        r_km = rng.uniform(3.0, 25.0)
        theta = rng.uniform(0.0, 2.0 * math.pi)
        lat = WAREHOUSE["lat"] + (r_km * math.cos(theta)) / KM_PER_LAT_DEG
        lng = WAREHOUSE["lng"] + (r_km * math.sin(theta)) / (KM_PER_LAT_DEG * cos_lat)
        lat, lng = round(lat, 6), round(lng, 6)
        d = haversine_km(WAREHOUSE["lat"], WAREHOUSE["lng"], lat, lng)
        if 3.0 <= d <= 25.0:
            return lat, lng


def build_orders(rng: random.Random, catalog: list[dict], n_stores: int) -> tuple[list[dict], list[dict]]:
    """生成门店订单。返回 (orders, item_lines)，item_lines 用于后续总重校准。"""
    orders: list[dict] = []
    all_lines: list[dict] = []
    for i in range(1, n_stores + 1):
        store_id = f"S{i:02d}"
        lat, lng = gen_store_location(rng)
        n_sku = rng.randint(5, 15)                     # 每店 5~15 个 SKU 行
        chosen = rng.sample(catalog, n_sku)            # 同店不重复选品
        items = []
        for sku in chosen:
            item = {
                "sku": sku["sku"],
                "name": sku["name"],
                "qty": rng.randint(3, 22),
                "weight_kg": sku["weight_kg"],
                "length_cm": sku["length_cm"],
                "width_cm": sku["width_cm"],
                "height_cm": sku["height_cm"],
                "fragile": sku["fragile"],
                "stackable": sku["stackable"],
                "max_stack_layers": sku["max_stack_layers"],
            }
            items.append(item)
            all_lines.append(item)
        orders.append({
            "order_id": f"O{i:04d}",
            "store_id": store_id,
            "store_name": f"连锁药店{i:02d}",
            "lat": lat,
            "lng": lng,
            "time_window": list(rng.choice(TIME_WINDOWS)),
            "service_minutes": rng.randint(10, 25),
            "items": items,
        })
    return orders, all_lines


def calibrate_total_weight(lines: list[dict], max_iter: int = 8) -> float:
    """校准总货重到 [CALIBRATE_LO, CALIBRATE_HI]，按比例缩放各行 qty（确定性）。

    空数据集（lines 为空）直接返回 0.0，不触发除零（空订单边界场景）。
    """
    if not lines:
        return 0.0
    total = 0.0
    for _ in range(max_iter):
        total = sum(it["qty"] * it["weight_kg"] for it in lines)
        if total <= 0.0:
            return round(total, 2)
        if CALIBRATE_LO <= total <= CALIBRATE_HI:
            return round(total, 2)
        ratio = CALIBRATE_IDEAL / total
        for it in lines:
            it["qty"] = max(1, min(99, int(round(it["qty"] * ratio))))
    return round(total, 2)


def build_network(orders: list[dict]) -> dict:
    """构建路网：节点表 + 对称距离矩阵 + 时长矩阵（30 km/h 折算）。"""
    nodes = [{"id": WAREHOUSE["id"], "lat": WAREHOUSE["lat"], "lng": WAREHOUSE["lng"]}]
    for o in orders:
        nodes.append({"id": o["store_id"], "lat": o["lat"], "lng": o["lng"]})

    n = len(nodes)
    dist = [[0.0] * n for _ in range(n)]
    for a in range(n):
        for b in range(a + 1, n):
            d = round(haversine_km(nodes[a]["lat"], nodes[a]["lng"],
                                   nodes[b]["lat"], nodes[b]["lng"]), 4)
            dist[a][b] = d
            dist[b][a] = d  # 严格对称

    dur = [[round(d / 30.0 * 60.0, 2) for d in row] for row in dist]

    return {
        "nodes": nodes,
        "distance_matrix_km": dist,
        "duration_matrix_min": dur,
        "node_order": [nd["id"] for nd in nodes],
    }


# ---------------------------------------------------------------- 主流程

def main() -> None:
    parser = argparse.ArgumentParser(description="智载 CityLoad 模拟数据生成器（T02）")
    parser.add_argument("--stores", type=int, default=30, help="门店数量（默认 30）")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（默认 42）")
    parser.add_argument("--out", type=str, default="data", help="输出目录（默认 data/）")
    args = parser.parse_args()

    rng = random.Random(args.seed)

    catalog = build_sku_catalog(rng)
    orders, lines = build_orders(rng, catalog, args.stores)
    total_weight = calibrate_total_weight(lines)
    network = build_network(orders)

    orders_doc = {
        "meta": {
            "scenario": SCENARIO,
            "date": DATE,
            "seed": args.seed,
            "warehouse": WAREHOUSE,
        },
        "orders": orders,
    }
    vehicles_doc = {"vehicles": VEHICLES}

    os.makedirs(args.out, exist_ok=True)

    def dump(name: str, doc: dict) -> None:
        path = os.path.join(args.out, name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write("\n")

    dump("orders.json", orders_doc)
    dump("vehicles.json", vehicles_doc)
    dump("network.json", network)
    dump("rules.json", RULES)

    # -------- 统计报告 --------
    n_lines = len(lines)
    n_pieces = sum(it["qty"] for it in lines)
    capacity = sum(v["max_weight_kg"] * v["count"] for v in VEHICLES)
    volume_m3 = sum(
        it["qty"] * it["length_cm"] * it["width_cm"] * it["height_cm"] / 1e6
        for it in lines
    )
    cargo_volume = sum(
        v["cargo_length_cm"] * v["cargo_width_cm"] * v["cargo_height_cm"] / 1e6 * v["count"]
        for v in VEHICLES
    )
    tw_count = {tuple(w): 0 for w in TIME_WINDOWS}
    for o in orders:
        tw_count[tuple(o["time_window"])] += 1
    fragile_n = sum(1 for it in lines if it["fragile"])
    nonstack_n = sum(1 for it in lines if not it["stackable"])
    pct_fragile = (fragile_n / n_lines * 100) if n_lines else 0.0
    pct_nonstack = (nonstack_n / n_lines * 100) if n_lines else 0.0

    print(f"[OK] {os.path.join(args.out, 'orders.json')}    "
          f"{args.stores} 门店 / {n_lines} SKU 行 / {n_pieces} 件")
    print(f"[OK] {os.path.join(args.out, 'vehicles.json')}  "
          f"{len(VEHICLES)} 车型 / {sum(v['count'] for v in VEHICLES)} 辆车 / "
          f"额定总载重 {capacity:.0f} kg")
    print(f"[OK] {os.path.join(args.out, 'network.json')}   "
          f"{len(network['nodes'])} 节点 / {len(network['nodes'])}x{len(network['nodes'])} 矩阵")
    print(f"[OK] {os.path.join(args.out, 'rules.json')}")
    print("--- 数据统计（seed=%d）---" % args.seed)
    print(f"总货重: {total_weight:.1f} kg "
          f"(占车队额定载重 {total_weight / capacity * 100:.1f}%；目标 55%~75%)")
    print(f"总件数: {n_pieces}")
    print(f"货物体积: {volume_m3:.1f} m³（全部车厢容积 {cargo_volume:.1f} m³）")
    print(f"易碎 SKU 行: {fragile_n}（{pct_fragile:.1f}%）；"
          f"不可堆叠 SKU 行: {nonstack_n}（{pct_nonstack:.1f}%）")
    tw_str = ", ".join(f"{'-'.join(k)} x{v}" for k, v in tw_count.items())
    print(f"时间窗分布: {tw_str}")


if __name__ == "__main__":
    main()
