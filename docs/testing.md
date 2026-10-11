# 测试记录与边界 case 清单（T33）

> **状态**：✅ **已定稿**（终稿基点：commit `9bb81f6`）—— 全量 155 通过 + 1 跳过 + 0 失败
> **最后更新**：2026-10-11 21:30（窗口B：T33 定稿）

---

## 1. 单测清单与运行结果

### 1.1 指标模块 `tests/test_metrics.py`（窗口B）✅ 已完成

```bash
pytest tests/test_metrics.py -v
```

结果摘要（跑于 2026-10-11，Python 3.13.14 / pytest 9.1.1）：

```
tests/test_metrics.py::test_case_a_hand_calc_cost_weight_on_time PASSED              [ 20%]
tests/test_metrics.py::test_case_b_hand_calc_packing_unassigned_partial_on_time PASSED [ 40%]
tests/test_metrics.py::test_case_c_empty_routes_edge PASSED                          [ 60%]
tests/test_metrics.py::test_case_d_dedup_and_dict_unassigned PASSED                  [ 80%]
tests/test_metrics.py::test_smoke_with_real_dataset PASSED                           [100%]
============================== 5 passed in 0.12s ==============================
```

> 2026-10-11 17:45 复验通过（v1.1 契约对齐：`solve_time_sec`/`peak_memory_mb` 缺失时按 0 计并在输出 `warnings` 中标注）。

| 用例 | 类型 | 验证内容 |
|------|------|----------|
| case_a | 手算 | 里程 27km / 成本 120+27×3.5=214.5 / 载重率 800÷1000=0.8 / 体积回退 1.25÷16.8 |
| case_b | 手算 | 体积率 (0.62+0.34)/2=0.48 / 未分配 1 单 / 按时 1÷3 / solve_time=12.3 |
| case_c | 边界 | 空路由全指标归零、不崩溃 |
| case_d | 边界 | 同店重复停靠只计一次时效；unassigned 支持字典元素；solve_time 从 summary 读取 |
| smoke | 冒烟 | 真实 30 店数据集上结构完整、指标可算 |

### 1.2 数据生成器 `data/gen_data.py` + 校验器 `data/validate.py`（窗口B）✅ 已完成

| 检查项 | 方法 | 结果 |
|--------|------|------|
| 结构契约（seed42 组） | `data/validate.py --data data/` 全字段/取值范围校验 | 30 店 328 行，0 错误 |
| 结构契约（seed7 组） | `data/validate.py --data data/seed7/` | 30 店 318 行，0 错误 |
| 坐标一致性 | orders.json 与 network.json 门店坐标逐店比对 | 两组全部一致 |
| 距离矩阵 | 对称性、对角线零、门店 3–25km 校验 | 42 组 3.46–24.93 km；7 组 3.39–24.42 km |
| 可复现性 | 同 seed 连跑 2 次 diff 4 文件 | 两组均字节级完全一致 |
| 双组差异 | seed 42 vs seed 7 | 行数 328/318、件数 3656/3751、坐标 0 重合 |
| 总量健康 | 多 seed 统计总重占比 | 57.2%–58.4%，全部落在 55%–75% 要求内 |

### 1.3 全量单测快照（窗口B 统一执行）✅

> 运行口径：从 dev 分支提交版本导出**干净工作树快照**后运行全量测试，
> 与各窗口进行中的未提交修改相互隔离（可复现：commit hash + 下述命令）。
> 环境提示：本机 pytest 收尾清理临时目录可能被系统安全机制拦截（表现为 EXIT=1 且无汇总行，
> **非测试失败**，以逐项统计为准）；将 TMP/TEMP/TMPDIR 设为自管目录即可获得干净汇总。

```bash
git worktree add <tmpdir> <commit> && cd <tmpdir>
TMP=<tmpdir>/.mytmp TEMP=<tmpdir>/.mytmp TMPDIR=<tmpdir>/.mytmp \
    python -m pytest tests/ -v --tb=line
```

**终稿快照：commit `9bb81f6`（2026-10-11 21:20）—— 156 项收集，155 通过 + 1 跳过，0 失败（70s）**

| 测试文件 | 通过 | 跳过 | 归属 |
|----------|------|------|------|
| test_baseline.py | 4 | 0 | 窗口C |
| test_build_html.py | 6 | 0 | 窗口E |
| test_edge_cases.py | 4 | 0 | 窗口B |
| test_experiments.py | 20 | 0 | 窗口C |
| test_explainer.py | 22 | 1 | 窗口D |
| test_final_outputs.py | 4 | 0 | 窗口C/E |
| test_metrics.py | 5 | 0 | 窗口B |
| test_packing.py | 38 | 0 | 窗口C |
| test_parser.py | 14 | 0 | 窗口D |
| test_render.py | 6 | 0 | 窗口E |
| test_routing.py | 15 | 0 | 窗口C |
| test_run.py | 2 | 0 | 窗口C/E |
| test_sequencing.py | 15 | 0 | 窗口C |
| **合计** | **155** | **1** | — |

- 跳过项：`test_explainer.py::test_real_pipeline_integration`（设计性跳过，接口保留）。
- ✅ **routing 确定性（已解决）**：T31/T31b 已固定 OR-Tools 随机性（固定自然下降搜索 + 双轮业务一致性验证）。
  旧 `test_routing_is_deterministic`（曾因 `time_limit` 受 CPU 负载影响存在偶发失败风险，主规划 19:10 通报）
  已由 `test_real_data_constraints_and_objective_stability` 等测试替代；终稿多轮快照均全绿，无偶发失败复现。
- 快照演进：`5498ae5` 82 项（81+1）→ `aa6a91a` 123 项（122+1）→ `9bb81f6` 156 项（155+1，终稿）。

### 1.4 窗口E 模块补测（T33 补测项 #1）✅

**（a）E 单测**：`test_render.py`（6 项）+ `test_build_html.py`（6 项）全部通过（见 1.3 快照表）。

**（b）mock 端到端管线**（`tests/mock_pipeline.py`：真实 `data/` 组装 mock 求解结果 → 渲染 → HTML）：

| 输入数据 | 图件 | report.html | 用时 | 结果 |
|----------|------|-------------|------|------|
| seed42 组（`data/`） | 37 张（路线图×1 + 车厢 3D 图×6 + 分步图×30） | 18,956 B | 12.3s | ✅ 全通 |
| seed7 组（`data/seed7/`） | 37 张 | 18,948 B | 13.0s | ✅ 全通（第二组数据兼容验证） |

> 该链路中 `metrics` 由真实 `eval/metrics.py` 计算 —— B/C/E 三方模块在 mock 层面已联通。
> T25 `run.py` 完成后已由真实管线取代（终稿产物见 `outputs/`，另有 `test_run.py` / `test_final_outputs.py` 覆盖）。

### 1.5 边界补测：超载 / 易碎堆叠 / 运力不足（T33 补测项 #2）✅

> 口径：先在**窗口C 当时工作树版本**上预跑（主规划安排，用于提前暴露问题），
> 新增独立测试 `tests/test_edge_cases.py`；终稿已在快照 `9bb81f6` 上复核（4/4 全绿，见 1.3 表）。

| 场景 | 用例 | 实测表现（2026-10-11 19:30） |
|------|------|------------------------------|
| 运力不足（1 辆 V01 装 5700kg） | test_insufficient_fleet_reports_unassigned | `partial`；19 单未分配如实上报；11 服务 + 19 未分配 = 30（不丢单）；载重不超限 |
| 单订单超载（S01 放大至 5177.8kg） | test_overload_single_order_graceful | O0001 被拒（进 unassigned）；S01 不出现在任何 route；其余 29 单正常 |
| 易碎品堆叠·真实数据 | test_fragile_not_compressed_real_data | 166 件已放置易碎品，几何独立验证 **0 违规**（无被压、无相交） |
| 易碎品堆叠·压力场景 | test_fragile_pressure_never_stacked_illegally | 32 件大易碎箱单层放不下 → 部分合法拒装；**0 违规**；数量守恒 40/40 |

> 附带观测：本次预跑装载完成率 **3550/3656 件（97.1%**，unplaced 106）—— 较 19:10 状态看板所记
> 中间态（2346/3656）显著改善，窗口C 的 T12 攻关修复效果已在本次预跑中体现。

---

## 2. 边界 case 清单与实测记录

> 要求覆盖：订单超载 / 时间窗无法满足 / 易碎品堆叠冲突 / 空订单·单门店 / 车辆数不足未分配。
> 表中"实测输出"均为真实运行记录，非推测。

| # | 场景 | 测试方式 | 实际表现 | 状态 |
|---|------|----------|----------|------|
| 1 | 空订单（0 门店） | `python data/gen_data.py --stores 0` | **修复前**：`ZeroDivisionError` 崩溃（总重校准除零）→ **修复后**：优雅输出 `0 门店 / 1 节点 / 1×1 矩阵`，exit 0 | ✅ 已实测 |
| 2 | 单门店 | `python data/gen_data.py --stores 1` | 正常输出：10 SKU 行 / 990 件 / 总重 1609.7 kg（单车 V01 1700kg 可装下） | ✅ 已实测 |
| 3 | 空路由（无车出动） | pytest `test_case_c` | 全指标归零（vehicle_count=0、rates=0.0），不崩溃 | ✅ 已实测 |
| 4 | 时间窗不满足（晚点） | pytest `test_case_b`（S02 16:30 到，窗 12:00–16:00）；算法侧软窗由 `test_soft_window_and_window_disabled` 覆盖 | on_time_rate 正确计 1/3，晚点停靠被识别；软窗/禁用窗两种模式均有测试 | ✅ 已实测 |
| 5 | 重复停靠（异常输入） | pytest `test_case_d` | 同店只计一次时效，不重复计数 | ✅ 已实测 |
| 6 | 订单超载（单订单 5.2t > 单车 1.7t） | `tests/test_edge_cases.py::test_overload_single_order_graceful` | **优雅**：该单进 `unassigned_orders`、不硬塞任何车；其余 29 单正常服务；0 崩溃 | ✅ 已实测 |
| 7 | 易碎品堆叠冲突 | `test_fragile_not_compressed_real_data`（全链路 166 件易碎品几何验证）+ `test_fragile_pressure_never_stacked_illegally`（32 件单层放不下的压力场景） | **优雅**：0 几何违规（无压/无相交）；放不下的进 unplaced；数量守恒（40/40） | ✅ 已实测 |
| 8 | 车辆数不足导致订单未分配 | `test_insufficient_fleet_reports_unassigned`（仅 1 辆 V01） | **优雅**：`partial` + 19 单未分配如实上报；不丢单（11 服务 + 19 未分配 = 30）；载重不超限 | ✅ 已实测 |
| 9 | 渲染空输入（E 模块） | E 单测 `test_render_empty_inputs_do_not_crash` | 不抛异常，仍生成 route_map.png | ✅ 已实测 |
| 10 | HTML 缺图降级（E 模块） | E 单测 `test_build_html_missing_figures_graceful` | 输出"未生成"占位提示，不崩溃 | ✅ 已实测 |

---

## 3. 收尾检查（T33 定稿版）

- [x] 窗口E 端到端管线记录 → 见 1.4 节
- [x] 窗口C 单测运行记录（test_routing / packing / sequencing / baseline / experiments）→ 见 1.3 节
- [x] 补测边界 6/7/8（超载、易碎堆叠、运力不足）→ 见 1.5 节 + 第 2 节边界表
- [x] 全量快照定稿（终稿基点 `9bb81f6`：155 通过 + 1 跳过 + 0 失败）→ 见 1.3 节
- [ ] 数据泄漏/红线检查（README 数字须来自 experiments/results.md）—— **移交主规划 T42 合规自查执行**

---

## 4. 测试环境

| 项 | 值 |
|----|-----|
| Python（业务代码） | 3.13.14（`C:/Users/y1933/.workbuddy/binaries/python/versions/3.13.12/python.exe`），兼容 3.10+ |
| pytest 环境 | `C:/Users/y1933/.workbuddy/binaries/python/envs/default`（pytest 9.1.1） |
| 运行 pytest | `C:/Users/y1933/.workbuddy/binaries/python/envs/default/Scripts/python.exe -m pytest tests/ -v` |
| 快照测试方法 | `git worktree add <tmpdir> <commit>` 从提交版本导出干净快照后运行，与工作树隔离；终稿快照 = `9bb81f6`（运行建议：TMP/TEMP/TMPDIR 指向自管目录，规避沙箱清理拦截） |
| 数据集 | `data/`（seed 42）+ `data/seed7/`（seed 7），均为 30 门店场景 |
