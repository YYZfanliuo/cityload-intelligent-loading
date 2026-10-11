# 智载 CityLoad —— 多约束城市配送智能配载与调度系统

> AI 重构产业·架构师大赛 · TRACK 02 AI＋物流 · 城市配送智能配载
> README v0.1（骨架版，正文由主规划窗口在 P4 阶段填充）

---

## 一、团队信息

> 占位：团队名称、成员与分工、联系方式（提交前由主规划窗口补全）。

## 二、业务场景

> 占位：复刻赛题"中心仓 → 30 家连锁药店"城市配送场景，简述业务痛点（多约束装载、时效要求、装载率低）与本系统定位。

## 三、核心方案

> 占位：一句话概括"路径优化（CVRPTW）+ 3D 装箱 + 按交付顺序装载"的协同求解方案，以及贪心 baseline 对比设计。

## 四、技术架构

> 占位：模块清单（solver / eval / agent / report）与数据流图（见 `docs/interfaces.md` 第 0 节），技术栈 Python 3.10+ + OR-Tools + matplotlib。

## 五、数据说明

> 占位：模拟数据生成器（种子固定、可复现）与数据字段说明，链接 `data/schema.md`；声明数据为模拟生成，不含真实业务数据。

## 六、运行方式

> 占位：环境安装与一键运行命令如下（pipeline 联调后由主规划窗口核对更新）：

```bash
pip install -r requirements.txt

# 一键跑通全流程（求解 → 装箱 → 装载排序 → 指标 → 图表 → HTML 报告）
python run.py --data data/ --out output/

# 可选：同时运行贪心 baseline 对比
python run.py --data data/ --out output/ --baseline
```

## 七、实验结果

> 占位：优化方案 vs 贪心 baseline 的对比表（装载率、总里程、用车数、时效达成率、综合成本），数据来源 `experiments/results.md`（17:00 后冻结，README 数字必须与其一致）。

## 八、应用价值

> 占位：面向连锁零售/医药配送企业降本增效的量化价值估算与推广路径。

## 九、当前限制与后续规划

> 占位：如实说明未做项（实时动态重调度、100+ 门店实测、生产级工程化），给出规模扩展思路。

## 十、开源组件与合规声明

> 占位：开源组件清单与许可证（OR-Tools、pandas、matplotlib 等）、模拟数据声明、LLM 使用环节标注，详见 `docs/compliance.md`。
