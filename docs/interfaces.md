# 接口契约 v1.0（T03）—— 冻结于 2026-10-11 13:00

> 本文件是**唯一事实来源**。任何窗口不得擅自更改；如需变更，向主规划窗口申请并同步全部窗口。
> 所有数据结构均为 JSON，字段名严格按下文拼写（大小写敏感）。

---

## 0. 数据流总览

```
orders.json ──┐
vehicles.json ├─→ [solve_routing] ─→ routes.json
network.json ─┘                          │
                                         ▼
goods.csv ────────────────────────→ [solve_packing] ─→ packing.json
                                         │
                                         ▼
                                  [sequence_loading] ─→ loading_plan.json
                                         │
              ┌──────────────────────────┼──────────────────────┐
              ▼                          ▼                      ▼
        [compute_metrics]         [render_figures]      [gen_guide_text]
              │                          │                      │
              ▼                          ▼                      ▼
        metrics.json              figures/*.png          guide_text.json
              └──────────────────────────┴──────────────────────┘
                                         ▼
                                  [build_html] ─→ report.html
```

数据文件统一放在 `data/`，输出统一放在 `output/`。

---

## 1. 输入数据结构

### 1.1 `data/orders.json` —— 订单（窗口B 产出，窗口C 消费）

```json
{
  "meta": {
    "scenario": "pharmacy_chain",
    "date": "2026-10-12",
    "seed": 42,
    "warehouse": {"id": "WH01", "name": "中心仓", "lat": 30.2741, "lng": 120.1551}
  },
  "orders": [
    {
      "order_id": "O0001",
      "store_id": "S01",
      "store_name": "连锁药店01",
      "lat": 30.2801,
      "lng": 120.1601,
      "time_window": ["08:00", "12:00"],
      "service_minutes": 15,
      "items": [
        {
          "sku": "SKU0001",
          "name": "感冒灵颗粒 10g*9袋",
          "qty": 3,
          "weight_kg": 0.15,
          "length_cm": 12.0,
          "width_cm": 8.0,
          "height_cm": 6.0,
          "fragile": false,
          "stackable": true,
          "max_stack_layers": 6
        }
      ]
    }
  ]
}
```

**字段约束**
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| order_id | string | ✔ | 唯一，格式 `O` + 4 位数字 |
| store_id | string | ✔ | 唯一门店编号 `S` + 2 位数字 |
| lat/lng | float | ✔ | 十进制度，杭州萧山周边范围 |
| time_window | [string,string] | ✔ | `"HH:MM"`，结束 > 开始 |
| service_minutes | int | ✔ | 门店卸货服务时长（分钟） |
| items[].qty | int | ✔ | ≥1 |
| items[].weight_kg | float | ✔ | 单件重量 |
| items[].length/width/height_cm | float | ✔ | 单件外廓尺寸 |
| items[].fragile | bool | ✔ | true = 不可承压（必须置顶） |
| items[].stackable | bool | ✔ | false = 不可在其上堆叠 |
| items[].max_stack_layers | int | ✔ | 最大堆叠层数（含自身） |

### 1.2 `data/vehicles.json` —— 运力（窗口B 产出，窗口C 消费）

```json
{
  "vehicles": [
    {
      "vehicle_id": "V01",
      "type": "4.2米厢式货车",
      "max_weight_kg": 1500.0,
      "cargo_length_cm": 420.0,
      "cargo_width_cm": 200.0,
      "cargo_height_cm": 200.0,
      "count": 4,
      "cost_per_km": 3.5,
      "fixed_cost": 120.0,
      "avg_speed_kmh": 30.0,
      "max_work_minutes": 480
    }
  ]
}
```

**字段约束**
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| vehicle_id | string | ✔ | 车型池编号 `V` + 2 位 |
| max_weight_kg | float | ✔ | 最大载重 |
| cargo_*_cm | float | ✔ | 车厢内部可用尺寸 |
| count | int | ✔ | 该车型可用数量 |
| cost_per_km | float | ✔ | 每公里变动成本（元） |
| fixed_cost | float | ✔ | 每车次固定成本（元） |
| avg_speed_kmh | float | ✔ | 平均行驶速度 |
| max_work_minutes | int | ✔ | 单车单日最长工作时长 |

### 1.3 `data/network.json` —— 路网与距离（窗口B 产出，窗口C 消费）

```json
{
  "nodes": [{"id": "WH01", "lat": 30.2741, "lng": 120.1551}, {"id": "S01", "lat": 30.2801, "lng": 120.1601}],
  "distance_matrix_km": [[0.0, 1.23], [1.23, 0.0]],
  "duration_matrix_min": [[0.0, 2.46], [2.46, 0.0]],
  "node_order": ["WH01", "S01"]
}
```

**字段约束**：`node_order` 定义矩阵行列顺序，必须与两个矩阵维度一致；矩阵为对称方阵。

### 1.4 `data/rules.json` —— 装载与调度规则（窗口B 产出，窗口C 消费）

```json
{
  "packing": {
    "allow_rotation": true,
    "rotation_axes": ["lwh", "wlh", "hlw"],
    "consider_fragile": true,
    "consider_max_stack": true,
    "gap_cm": 0.0
  },
  "routing": {
    "respect_time_window": true,
    "allow_overtime_penalty": true,
    "overtime_penalty_per_min": 2.0,
    "unassigned_penalty": 1000.0
  }
}
```

---

## 2. 模块接口签名（Python）

所有函数签名固定如下，实现不得改变参数与返回结构。

### 2.1 `solver/routing.py` —— 窗口C

```python
def solve_routing(orders: dict, vehicles: dict, network: dict, rules: dict) -> dict:
    """返回 routes.json 结构（见 3.1）。必须在 60 秒内返回（30 门店规模）。"""
```

### 2.2 `solver/packing.py` —— 窗口C

```python
def solve_packing(routes: dict, orders: dict, vehicles: dict, rules: dict) -> dict:
    """返回 packing.json 结构（见 3.2）。对每条 route 的每个 vehicle 分别装箱。"""
```

### 2.3 `solver/sequencing.py` —— 窗口C

```python
def sequence_loading(packing: dict, routes: dict, rules: dict) -> dict:
    """返回 loading_plan.json 结构（见 3.3）。按配送顺序倒序装载（后送先装）。"""
```

### 2.4 `solver/baseline.py` —— 窗口C

```python
def solve_baseline(orders: dict, vehicles: dict, network: dict, rules: dict) -> dict:
    """贪心基线：最近门店优先分配 + 顺序装箱。返回 {"routes":..., "packing":..., "loading_plan":...}"""
```

### 2.5 `eval/metrics.py` —— 窗口B

```python
def compute_metrics(result: dict, orders: dict, vehicles: dict, network: dict) -> dict:
    """result 可为 routes.json / baseline 输出。返回 metrics.json 结构（见 3.4）。"""
```

### 2.6 `agent/parser.py` —— 窗口D

```python
def parse_requirement(text: str) -> dict:
    """自然语言 → orders 片段 JSON；解析失败返回 {"error": "..."}。不得抛出异常。"""

def load_structured_fallback(path: str) -> dict:
    """旁路：直接读取结构化 orders.json。"""
```

### 2.7 `agent/explainer.py` —— 窗口D

```python
def gen_guide_text(loading_plan: dict, routes: dict) -> dict:
    """返回 guide_text.json 结构（见 3.5）。纯模板生成，不依赖 LLM。"""
```

### 2.8 `report/render.py` —— 窗口E

```python
def render_figures(routes: dict, packing: dict, loading_plan: dict, network: dict, outdir: str) -> list[str]:
    """生成 PNG 到 outdir，返回文件路径列表，命名规范：route_map.png / packing_V01.png / step_V01_1.png"""
```

### 2.9 `report/build_html.py` —— 窗口E

```python
def build_html(routes: dict, metrics: dict, guide_text: dict, figure_paths: list[str], outpath: str) -> str:
    """返回生成的 HTML 文件路径。图片以相对路径引用。"""
```

### 2.10 `run.py` —— 窗口C 主责，窗口E 联调

```python
"""一条命令跑通全流程：
   python run.py --data data/ --out output/ [--baseline]
输出：output/routes.json, output/packing.json, output/loading_plan.json,
      output/metrics.json, output/guide_text.json, output/figures/*.png, output/report.html
"""
```

---

## 3. 输出数据结构

### 3.1 `routes.json`

```json
{
  "status": "ok",
  "solve_time_sec": 12.3,
  "peak_memory_mb": 180.2,
  "routes": [
    {
      "route_id": "R01",
      "vehicle_type": "V01",
      "sequence": ["WH01", "S03", "S01", "S07", "WH01"],
      "stop_details": [
        {"node": "S03", "arrive": "08:12", "start_service": "08:12", "end_service": "08:27", "load_kg": 220.5}
      ],
      "total_distance_km": 43.2,
      "total_duration_min": 138,
      "total_load_kg": 620.3,
      "load_rate_weight": 0.41,
      "cost": 271.2
    }
  ],
  "unassigned_orders": [],
  "summary": {"vehicle_count": 2, "total_distance_km": 78.5, "total_cost": 512.4}
}
```

> **性能字段约定**（v1.1 新增）：`solve_time_sec` 与 `peak_memory_mb` 必须由求解器在结果**顶层**输出；`compute_metrics` 会优先从 result 顶层读取这两个字段，缺失时按 0 计并在 metrics 中标注。`run.py` 主流程建议全程 `tracemalloc.start()` 以获取峰值内存。

### 3.2 `packing.json`

```json
{
  "status": "ok",
  "vehicles": [
    {
      "vehicle_instance": "R01-1",
      "vehicle_type": "V01",
      "placed": [
        {
          "sku": "SKU0001",
          "order_id": "O0001",
          "store_id": "S03",
          "position_cm": {"x": 0.0, "y": 0.0, "z": 0.0},
          "size_placed_cm": {"l": 12.0, "w": 8.0, "h": 6.0},
          "layer": 1,
          "rotated": false
        }
      ],
      "unplaced": [],
      "volume_utilization": 0.62,
      "weight_utilization": 0.41
    }
  ]
}
```

坐标约定：原点在车厢**左下后角**（靠车尾、贴左、贴地）；x 沿车长（向车头），y 沿车宽，z 垂直向上。

### 3.3 `loading_plan.json`

```json
{
  "status": "ok",
  "plans": [
    {
      "vehicle_instance": "R01-1",
      "delivery_order": ["S03", "S01", "S07"],
      "loading_steps": [
        {
          "step": 1,
          "action": "装",
          "store_id": "S07",
          "items": [{"sku": "SKU0001", "qty": 3}],
          "note": "最后送达，最先装入，靠车头位置"
        }
      ],
      "blocks_warning": []
    }
  ]
}
```

### 3.4 `metrics.json`

```json
{
  "scenario": "pharmacy_chain",
  "seed": 42,
  "metrics": {
    "vehicle_count": 2,
    "total_distance_km": 78.5,
    "total_cost": 512.4,
    "avg_weight_load_rate": 0.41,
    "avg_volume_utilization": 0.62,
    "on_time_rate": 1.0,
    "unassigned_order_count": 0,
    "solve_time_sec": 12.3,
    "peak_memory_mb": 180.2
  },
  "per_route": []
}
```

### 3.5 `guide_text.json`

```json
{
  "status": "ok",
  "guides": [
    {"vehicle_instance": "R01-1", "text": "第一步：将 S07（连锁药店07）的 3 件货物装入车厢最内侧靠车头处……"}
  ]
}
```

---

## 4. 命名与目录规范

```
项目根/
├── run.py                    # 一键入口
├── requirements.txt          # 窗口A 维护
├── README.md
├── data/                     # 输入数据（窗口B）
│   ├── orders.json  vehicles.json  network.json  rules.json
│   └── gen_data.py  schema.md
├── solver/                   # 窗口C
│   ├── routing.py  packing.py  sequencing.py  baseline.py
├── eval/                     # 窗口B
│   └── metrics.py
├── agent/                    # 窗口D
│   ├── parser.py  explainer.py  prompts/
├── report/                   # 窗口E
│   ├── render.py  build_html.py  templates/
├── output/                   # 运行产物（git 忽略大图，保留 report.html）
├── experiments/              # 窗口C
│   └── run_experiments.py  results.md
├── docs/                     # 主规划
│   ├── interfaces.md  testing.md  compliance.md
└── tests/                    # 各窗口自带单测
    ├── test_routing.py  test_packing.py  test_metrics.py
```

**代码规范**：Python 3.10+；函数必须有 docstring；单测用 pytest；不引入未在 `requirements.txt` 声明的依赖。

---

## 5. 变更记录

| 日期 | 版本 | 变更 | 批准 |
|------|------|------|------|
| 2026-10-11 | v1.0 | 初始冻结 | 主规划窗口 |
| 2026-10-11 | v1.1 | 3.1 节 routes.json 顶层新增 `solve_time_sec` / `peak_memory_mb` 性能字段（窗口C 必须输出，窗口B 的 compute_metrics 消费） | 主规划窗口（依窗口B 申请，17:15 批准） |
