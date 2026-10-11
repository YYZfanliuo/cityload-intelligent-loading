# 派发单 · 窗口D（智能层）

> **窗口定位**：LLM 需求解析 + 装卸指引文案生成。**进场时间 10/11 18:00**。
> **主规划窗口（WorkBuddy）** 是你的对接人。
> ⚠️ 你不在关键路径上，但有一条硬规则：**你的模块失败不得阻塞主流程**（必须有旁路）。

---

## 你的任务清单

### T21 · LLM 订单需求解析【截止 10/11 22:00】

**输入**：`docs/interfaces.md` 第 2.6 节（函数签名）、1.1 节（orders 结构）
**交付物**：
1. `agent/parser.py`（实现 `parse_requirement` 与 `load_structured_fallback`）
2. `agent/prompts/parse_requirement.txt`（提示词模板）
3. `tests/test_parser.py`

**功能要求**：
- 输入自然语言，例如：
  > "明天上午给杭州萧山的康健药房送 5 箱感冒灵和 3 箱板蓝根，12 点前必须到，药品易碎要轻放"
- 输出符合 `orders.json` 中单个 order 的结构（门店信息可留空或标注待补）
- **必须做 JSON schema 校验**：解析结果字段缺失/类型错误时，返回 `{"error": "..."}`，**绝不抛异常**
- 若 LLM 不可用（无 API Key / 超时 / 报错），`parse_requirement` 应捕获异常并返回 `{"error": "llm_unavailable"}`

**旁路（关键）**：`load_structured_fallback(path)` 直接读取 `data/orders.json`。主流程默认走旁路，LLM 解析作为**可选增强演示**。

**验收标准**：
- 5 条自然语言样例解析正确率 ≥ 80%（写在单测里，附实际输出）
- 故意输入乱码/超长文本，返回 error 而非崩溃
- 无 API Key 时全流程不中断

**LLM 接入说明**：优先使用环境变量中的 API Key（如 `OPENAI_API_KEY` / 兼容 OpenAI 协议的国产模型）。若无法接入，则实现为**规则+正则的轻量解析器**，并在 README 中如实说明；不要为了接 LLM 而拖延。

---

### T22 · 模板化装卸指引文案生成【截止 10/11 23:00】

**输入**：`docs/interfaces.md` 2.7 节、3.3 节（loading_plan.json）、3.1 节（routes.json）
**交付物**：`agent/explainer.py`（实现 `gen_guide_text`）

**功能要求**：
- **纯模板生成**（不依赖 LLM，保证稳定），把结构化的 `loading_steps` 转成人类可读的分步指引
- 输出示例：
  > "第 1 步：将 S07（连锁药店07）的 3 件货物装入车厢最内侧靠车头位置，该门店最后送达，故最先装载。"
  > "第 2 步：将 S01 的 5 件货物紧随其后装入，注意其上方不可堆叠易碎品。"
- 若检测到 `blocks_warning`，额外生成提醒文案

**可选增强**：若时间允许，用 LLM 对模板文本润色，**但必须保留模板作为兜底**。

**验收标准**：
- 输出符合 3.5 节 `guide_text.json` 结构
- 步骤编号连续、门店顺序与 `delivery_order` 一致
- 不依赖网络即可运行

---

## 交付物清单

| 任务 | 文件 | 截止 |
|------|------|------|
| T21 | `agent/parser.py`、`agent/prompts/parse_requirement.txt`、`tests/test_parser.py` | 10/11 22:00 |
| T22 | `agent/explainer.py` | 10/11 23:00 |

## 协作规则

1. 进场前先读 `docs/interfaces.md` 与 `项目开发任务规划.md`
2. 代码提交 `dev` 分支开 PR
3. **红线：不得让 LLM 调用成为主流程的必经环节**
4. 完成后回报格式：`【T21 完成】5 条样例解析正确 4 条，附输出；无 Key 时返回 error 正常`

## 参考文件

- `项目开发任务规划.md`
- `docs/interfaces.md`（**必读**）
- `AI重构产业架构师大赛-赛题整理.md`（赛道二"智能装卸指导：生成3D装载布局及分步骤装卸指引"——你的 T22 正对应此评审关注点）
