# 合规声明（T42）

> 智载 CityLoad · AI重构产业·架构师大赛 · TRACK 02 AI＋物流
> 生成时间：2026-10-11 21:40 · 主规划窗口

---

## 一、开源组件清单与许可证

| 组件 | 版本 | 用途 | 许可证 | 引入方式 |
|------|------|------|--------|----------|
| OR-Tools | 9.15.6755 | CVRPTW 路径求解（核心） | Apache License 2.0 | `requirements.txt` |
| pandas | 3.0 | 数据处理与统计 | BSD 3-Clause | `requirements.txt` |
| NumPy | 2.5 | 数值计算 | BSD 3-Clause | `requirements.txt` |
| matplotlib | 3.11 | 路线图 / 3D 装箱图渲染 | PSF-based（Matplotlib License） | `requirements.txt` |
| pytest | 9.1 | 测试框架 | MIT | `requirements.txt` |
| 字体（Noto Sans SC） | — | 报告页面字体 | SIL Open Font License 1.1 | 仅赛事页面参考，未入库 |

> 本仓库未使用任何许可证不明或来源不明的代码；全部第三方组件通过正式依赖声明引入，未复制粘贴任何外部代码。

## 二、数据合规声明

1. **数据来源**：本仓库全部业务数据（`data/orders.json`、`data/vehicles.json`、`data/network.json`、`data/rules.json` 及 `data/seed7/`）均由本仓库 `data/gen_data.py` **模拟生成**。
2. **无真实数据**：不含任何传化集团真实业务数据、赛事方数据或第三方数据；门店/道路/商品均为程序虚构。
3. **可复现**：固定随机种子（seed=42 / seed=7）下字节级可复现（已做 3 次 diff 验证）。
4. **数据使用边界**：若晋级后获得赛事方提供的真实业务数据，将严格按其数据使用规则与保密要求处理（不披露、不上传外部平台、不用于赛事外用途）。

## 三、LLM 使用环节标注

| 环节 | 文件 | 模型/方式 | 是否主流程必经 | 降级行为 |
|------|------|-----------|----------------|----------|
| 自然语言订单解析 | `agent/parser.py` | 通用大模型 API（兼容 OpenAI 协议，如 DeepSeek）；无 Key 时走规则+正则 | ❌ 否（默认旁路 `load_structured_fallback`） | LLM 不可用 → 规则解析 → 返回 `{"error":...}`，全程不抛异常 |
| 装卸指引文案润色 | `agent/explainer.py` `polish_guide_text`（可选函数） | 同上 | ❌ 否（模板生成永远兜底） | 润色失败/校验不过 → 回退模板原文 |

- 两处 LLM 调用均使用**环境变量**读取密钥（`CITYLOAD_LLM_API_KEY` / `DEEPSEEK_API_KEY` / `OPENAI_API_KEY`），密钥不入库。
- 解析结果经 JSON Schema 校验；润色结果经四重校验（步骤编号/门店编号/提醒完整性/长度区间），**模板永不失效**。

## 四、数据泄漏与安全红线检查（2026-10-11 21:40 执行）

| 检查项 | 方法 | 结果 |
|--------|------|------|
| 真实密钥硬编码 | `sk-` 长串模式全文扫描 | ✅ 零命中 |
| 密钥泄漏 | 敏感关键词扫描（api_key/secret/token 等） | ✅ 仅环境变量名引用，无真实值 |
| 凭据文件入库 | `.env`/credential 类文件检查 | ✅ 无此类文件被跟踪 |
| 异常文件 | 非源码/数据/文档类文件排查 | ✅ 仅 .gitignore，无异常 |
| 缓存垃圾 | `__pycache__`/`.pytest_cache` 等 | ✅ 已被 .gitignore 覆盖，未入库 |
| 真实业务数据 | 数据文件来源复核 | ✅ 全部为模拟生成 |

> 执行人：主规划窗口（本文件即为检查记录）。

## 五、知识产权与原创性声明

1. 全部代码（`solver/` `eval/` `agent/` `report/` `run.py` `experiments/` `tests/` 等）为本团队原创，或对开源组件的合法使用（仅通过依赖引入调用）。
2. 无任何从赛事方或其他来源获取的未授权代码、数据或模型权重。
3. 作品合规标注开源组件、模拟数据与人工操作环节（本作品无人工操作环节，全流程自动化）。
4. 参赛后提交作品的知识产权归属以赛事方规则为准。

## 六、评审访问与提交物

- 提交形式：GitHub 私有仓库 `YYZfanliuo/cityload-intelligent-loading`，邀请官方账号 **DsChHack** 为 Collaborator。
- 仓库内容清单：项目代码、README、运行说明、数据生成器、实验记录（`experiments/`）、测试记录（`docs/testing.md`）、双报告产物（`outputs/t25-final`、`outputs/t25-baseline`）。
- Demo 可用性：`report.html` 离线可开（图件随仓库提供）；可选交互界面见 T50（如已交付则附运行说明）。
- 网盘链接：无（全部内容在仓库内，无外链依赖）。

---

*本文件由主规划窗口维护，为 T42 合规自查交付物。*
