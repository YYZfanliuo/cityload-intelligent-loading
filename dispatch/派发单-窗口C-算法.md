# 派发单 · 窗口C（算法）—— 主战场

> **窗口定位**：核心求解算法。这是整个项目的技术主体，也是评审维度 1/2/4 的主要支撑。
> **主规划窗口（WorkBuddy）** 是你的对接人，完成任何任务后向主规划窗口回报。
> ⚠️ **你承担关键路径上的大部分任务（T11→T13→T25→T31），是全项目最关键窗口。**

---

## 你的任务清单

### T11 · CVRPTW 求解器【截止 10/11 18:00 出初版，21:00 定稿】

**输入**：`data/orders.json`、`data/vehicles.json`、`data/network.json`、`data/rules.json`
**交付物**：`solver/routing.py`（实现 `solve_routing`）+ `tests/test_routing.py`

**技术要求**：
- 建模为 CVRPTW：容量约束 + 时间窗约束 + 单车最长工时约束
- 求解器用 OR-Tools `pywrapcp` 的 RoutingModel
- 多车型支持：不同类型的车有不同载重/容积/成本
- 距离用 `network.json` 的 `distance_matrix_km`，时长用 `duration_matrix_min`
- 目标函数：最小化综合成本 = 固定成本 × 车数 + 里程 × 每公里成本 + 超时惩罚
- 30 门店规模必须在 **60 秒内**返回结果
- 若部分订单无法分配，放入 `unassigned_orders` 而非直接报错

**验收标准**：
- 输出完全符合 `docs/interfaces.md` 3.1 节 `routes.json` 结构
- `sequence` 以中心仓（WH01）开始和结束
- 每辆车 `total_load_kg` ≤ 该车型 `max_weight_kg`（硬约束，必须校验）
- 各站点 `arrive` 时间落在 `time_window` 内（或标注为超时并计入惩罚）
- 单测通过

**降级预案**：若 18:00 仍无合法解 → 直接改用 OR-Tools 官方 CVRP 示例改造，时间窗改为软约束（大惩罚），并告知主规划窗口。

---

### T12 · 3D 装箱启发式【截止 10/11 19:00】

**输入**：`routes.json`、`orders.json`、`vehicles.json`、`rules.json`
**交付物**：`solver/packing.py`（实现 `solve_packing`）+ `tests/test_packing.py`

**算法要求**：
- 对每条 route 的每个车次分别装箱
- 约束：不超重、不超车厢尺寸、货物不重叠、**易碎品必须置于顶层**、`stackable:false` 的货物上方不能放东西、不超过 `max_stack_layers`
- 允许旋转（按 `rules.packing.rotation_axes`）
- 可用 `py3dbp` 库，或自研层叠启发式（建议：按体积从大到小排序 + 分层放置 + 剩余空间切割）
- 输出每个货物的 `position_cm`（x/y/z）与 `size_placed_cm`

**验收标准**：
- 输出符合 `docs/interfaces.md` 3.2 节 `packing.json` 结构
- 单测校验：任意两箱不重叠、总重不超载、尺寸不超车厢、易碎品 z 坐标在最高层
- 未放下的货物进 `unplaced` 而非丢弃

**降级预案**：若 17:00 无输出 → 降级为"按体积排序的简单层叠装箱"（约 100 行），放弃旋转与精细堆叠，务必保证能跑通。

---

### T13 · 按门店交付顺序的分组排箱【截止 10/11 20:00】

**输入**：`packing.json`、`routes.json`、`rules.json`
**交付物**：`solver/sequencing.py`（实现 `sequence_loading`）

**逻辑要求**：
- 核心原则：**后送达的货物先装（靠车头），先送达的货物后装（靠车尾）**，避免卸货时翻箱
- 基于 T12 的装箱结果，按门店交付顺序重排（或调整 z/位置的装填优先级）
- 检测并报告"阻挡"情况（先送货物被后送货物压在下面）→ 写入 `blocks_warning`

**验收标准**：
- 输出符合 `docs/interfaces.md` 3.3 节 `loading_plan.json` 结构
- `delivery_order` 与 routes 中该车的门店访问顺序一致
- `loading_steps` 逆序（最后送的第一步装）
- 在有多个门店的车上，`blocks_warning` 为空（即确实做到了无阻挡）

---

### T14 · 贪心 baseline【截止 10/11 20:30】

**输入**：同 T11
**交付物**：`solver/baseline.py`（实现 `solve_baseline`）

**逻辑要求**（模拟"人工经验"）：
- 按订单就近原则分配车辆：同一区域的门店塞进同一辆车，装满为止
- 不做路线优化的精细求解，按门店编号顺序访问
- 装箱用最简单的顺序装填（不重排、不按配送顺序）
- 输出结构同 `{"routes":..., "packing":..., "loading_plan":...}`

**验收标准**：
- 与优化版同接口，可被 `compute_metrics` 直接评估
- 对比结果中 baseline 的里程/成本应**明显劣于**优化版（否则说明 baseline 太强或优化不够，需检查）

---

### T25 · 端到端串联 `run.py`【截止 10/11 24:00】

**输入**：全部模块
**交付物**：`run.py`
```bash
python run.py --data data/ --out output/            # 优化方案
python run.py --data data/ --out output/ --baseline # baseline 方案
```
**输出**：`output/routes.json`、`packing.json`、`loading_plan.json`、`metrics.json`、`guide_text.json`、`figures/*.png`、`report.html`

**验收标准**：一条命令跑通，无报错，产物齐全。与窗口E 联调确认 HTML 能正常渲染。

---

### T31 · 对比实验【截止 10/12 12:00】

**输入**：`run.py`、2 组数据集（seed 42 与 seed 7，窗口B 提供）
**交付物**：`experiments/run_experiments.py` + `experiments/results.md`

**实验设计**：
- 每个数据集跑 **优化方案** 与 **baseline** 各 3 次（固定随机种子取均值）
- 对比指标：车辆数、总里程、总成本、平均载重利用率、平均体积利用率、时效达成率、求解耗时
- 输出 Markdown 对比表 + 计算提升百分比

**验收标准**：
- `results.md` 含完整对比表，每个数字可复现（附运行命令）
- 明确写出优化版相对 baseline 的提升幅度（如"里程降低 23.4%"）

---

### T32 · 求解性能记录【截止 10/12 13:00】

**输入**：T31 运行数据
**交付物**：`experiments/results.md` 中的性能小节，含：
- 30 门店规模下求解耗时、峰值内存
- 求解复杂度分析（VRP + 3D 装箱均为 NP-hard），以及规模扩大后的定性推演
- 扩展思路（分区分批求解、启发式预热、并行化）

**说明**：本项目不再实测 100 门店规模（时间不足），此节为评审维度 4（工程化潜力与规模扩展能力）的支撑材料，需写清楚、有逻辑。

---

## 任务优先级（按此顺序执行）

1. **T11**（13:00 拿到数据后立即开始）—— 关键路径，最先做
2. **T12** —— 与 T11 并行推进
3. **T13** —— 依赖 T11/T12
4. **T14** —— 可穿插，逻辑简单
5. **T25** —— 依赖前面全部
6. **T31/T32** —— 次日

## 交付物清单

| 任务 | 文件 | 截止 |
|------|------|------|
| T11 | `solver/routing.py`、`tests/test_routing.py` | 10/11 18:00（初版） |
| T12 | `solver/packing.py`、`tests/test_packing.py` | 10/11 19:00 |
| T13 | `solver/sequencing.py` | 10/11 20:00 |
| T14 | `solver/baseline.py` | 10/11 20:30 |
| T25 | `run.py` | 10/11 24:00 |
| T31 | `experiments/run_experiments.py`、`results.md` | 10/12 12:00 |
| T32 | `results.md` 性能小节 | 10/12 13:00 |

## 协作规则

1. 代码提交 `dev` 分支开 PR
2. 遇到接口歧义以 `docs/interfaces.md` 为准；确需变更的向主规划窗口申请
3. 检查点（10/11 的 13:00 / 18:00 / 21:00 / 24:00）向主规划简报：完成 / 阻塞 / 次日计划
4. **任一任务延误超 1 小时立即上报**，由主规划启动降级预案
5. 完成后回报格式：`【T11 完成】solver/routing.py 已实现，30 门店求解 12.3s，装载率 0.41，已通过单测`

## 参考文件

- `项目开发任务规划.md`（v2.0）
- `docs/interfaces.md`（**必读，接口标准**）
- `AI重构产业架构师大赛-赛题整理.md`（赛道二业务描述，重点看"城市配送智能配载"三个探索方向，你的三个算法模块正对应它）
