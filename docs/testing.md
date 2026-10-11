# 测试记录与边界 case 清单（T33）

> **状态**：进行中 —— 窗口B 负责汇总；算法（窗口C）与可视化（窗口E）部分待其代码就绪后补测。
> **最后更新**：2026-10-11 17:50（窗口B，含 T02b 第二组数据集自检记录）

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

---

## 2. 边界 case 清单与实测记录

> 要求覆盖：订单超载 / 时间窗无法满足 / 易碎品堆叠冲突 / 空订单·单门店 / 车辆数不足未分配。
> 表中"实测输出"均为真实运行记录，非推测。

| # | 场景 | 测试方式 | 实际表现 | 状态 |
|---|------|----------|----------|------|
| 1 | 空订单（0 门店） | `python data/gen_data.py --stores 0` | **修复前**：`ZeroDivisionError` 崩溃（总重校准除零）→ **修复后**：优雅输出 `0 门店 / 1 节点 / 1×1 矩阵`，exit 0 | ✅ 已实测 |
| 2 | 单门店 | `python data/gen_data.py --stores 1` | 正常输出：10 SKU 行 / 990 件 / 总重 1609.7 kg（单车 V01 1700kg 可装下） | ✅ 已实测 |
| 3 | 空路由（无车出动） | pytest `test_case_c` | 全指标归零（vehicle_count=0、rates=0.0），不崩溃 | ✅ 已实测 |
| 4 | 时间窗不满足（晚点） | pytest `test_case_b`（S02 16:30 到，窗 12:00–16:00） | on_time_rate 正确计 1/3，晚点停靠被识别 | ✅ 部分实测（算法侧软约束处理待补） |
| 5 | 重复停靠（异常输入） | pytest `test_case_d` | 同店只计一次时效，不重复计数 | ✅ 已实测 |
| 6 | 订单超载（单车载重 < 单订单重量） | 待窗口C solver 就绪后构造 2000kg 订单实测 | 待补 | ⏳ |
| 7 | 易碎品堆叠冲突 | 待窗口C packing 就绪后实测（数据已含 33 行 fragile、40 行不可堆叠） | 待补 | ⏳ |
| 8 | 车辆数不足导致订单未分配 | 待窗口C solver 就绪后实测 | 待补（指标侧计数已验：case_b） | ⏳ |

---

## 3. 待补计划（窗口B 汇总）

- [ ] 窗口C solver 就绪后：补测边界 6/7/8（超载、易碎堆叠、运力不足），记录真实输出与优雅性判定
- [ ] 窗口C 单测（test_routing.py / test_packing.py）运行记录并入本文件
- [ ] 窗口E 端到端 `run.py` 全流程跑通记录并入本文件
- [ ] 数据泄漏/红线检查：README 数字须来自 experiments/results.md（主规划核对）

---

## 4. 测试环境

| 项 | 值 |
|----|-----|
| Python（业务代码） | 3.13.14（`C:/Users/y1933/.workbuddy/binaries/python/versions/3.13.12/python.exe`），兼容 3.10+ |
| pytest 环境 | `C:/Users/y1933/.workbuddy/binaries/python/envs/default`（pytest 9.1.1） |
| 运行 pytest | `C:/Users/y1933/.workbuddy/binaries/python/envs/default/Scripts/python.exe -m pytest tests/ -v` |
| 数据集 | `data/`（seed 42，30 门店场景；可换 seed 复现） |
