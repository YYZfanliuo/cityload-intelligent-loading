# -*- coding: utf-8 -*-
"""智载 CityLoad —— 交互式演示界面（T50 / T50b 双语版 · 窗口E）

启动（一条命令）::

    pip install -r requirements.txt -r requirements-app.txt
    streamlit run app.py

功能：侧边栏切换语言（中文 / English，默认中文）→ 选择数据集（seed42 / seed7）
→ 点击「运行智能配载」→ 指标卡 + 路线图 + 各车厢 3D 装载图（可选：对比表 + 分步指引）。

双边界（T50b）：界面骨架文案（标题/标签/按钮/指标名/提示语）中英双语；
图表 PNG 内文字与分步指引正文由渲染层生成、保持中文，界面附固定双语说明。

硬约束（派发单 T50/T50b）:

* **零侵入** —— 仅 import 复用现有模块；改动仅限本文件与 tests/test_app_t50.py；
* **产物隔离** —— 全部产物写入 ``outputs/app-tmp/{seed}/``，不覆盖
  ``outputs/t25-final/`` 与 ``outputs/t25-baseline/``；
* 核心链路位于 ``execute()``（无 Streamlit 依赖、与语言无关，可无头自测且缓存跨语言共享）。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run as pipeline  # noqa: E402  仅复用，不修改

# ---------------------------------------------------------------- 配置

DATASETS = {
    # 选项值保持语言无关（seed42 / seed7），说明文案由 dataset_help 承载（双语切换安全）
    "seed42": {"dir": ROOT / "data"},
    "seed7": {"dir": ROOT / "data" / "seed7"},
}
APP_TMP = ROOT / "outputs" / "app-tmp"

#: 扩展档案存储目录（T50e：新增门店 / 商品；会话持久，不动 data/ 原始数据集）
CATALOG_EXT = APP_TMP / "catalog-ext"

#: 自然语言下单内置示例（解析器当前支持中文输入；按钮文案即示例文本）
#: 门店均为当前数据集门店库内门店（连锁药店01–30），可完整体验 T50d 端到端串联
NL_EXAMPLES = [
    "明天上午给连锁药店07送 5 箱感冒灵，12 点前到",
    "后天上午 9 点到 11 点给连锁药店01送 3 箱布洛芬和 2 盒维生素C",
    "明早 8 点给连锁药店22送 20 盒板蓝根颗粒",
]

#: T50f：多单独立场景内置富示例（6 单、多家门店，可直接解析并建独立小场景）
MULTI_EXAMPLES = [
    "明天上午给连锁药店03送 4 箱感冒灵，11 点前到",
    "明天上午给连锁药店12送 3 箱布洛芬和 2 盒维生素C",
    "明天下午给连锁药店18送 6 盒板蓝根颗粒，15 点前到",
    "明天给连锁药店25送 5 箱创可贴",
    "明天上午给连锁药店09送 8 盒藿香正气水",
    "明天下午 14 点到 17 点给连锁药店27送 4 箱口罩",
]

#: 本会话 LLM 配置对应的环境变量（仅运行时注入，用后清理）
_LLM_ENV_KEYS = ("CITYLOAD_LLM_API_KEY", "CITYLOAD_LLM_BASE_URL", "CITYLOAD_LLM_MODEL")

#: 界面骨架文案（T50b：全面双语）
STRINGS = {
    "zh": {
        "app_title": "智载 CityLoad —— 城市配送智能配载交互演示",
        "app_caption": "中心仓 → 30 家连锁药店 ｜ CVRPTW 路径规划 + 3D 装箱 + 分步装卸指引（T31b 可复现口径）",
        "lang_label": "语言 / Language",
        "dataset_label": "选择数据集",
        "dataset_help": "seed42：主数据集（中心仓 → 30 门店）；seed7：第二组验证数据集（30 门店）",
        "comparison_label": "对比贪心基线",
        "comparison_help": "勾选后同时求解贪心基线并展示真实对比表（约多耗时几秒）",
        "run_button": "运行智能配载",
        "spinner": "正在求解与渲染图件（约 10～30 秒）...",
        "error_prefix": "运行失败：",
        "info_hint": "请选择数据集后点击「运行智能配载」。首次运行约 10～30 秒，之后同参数将复用缓存。",
        "result_meta": "数据集：{seed} ｜ 图件 {n} 张 ｜ 产物目录：{out}",
        "kpi_vehicles": "车辆数",
        "kpi_distance": "总里程",
        "kpi_cost": "运输费",
        "kpi_fill": "货物装下率",
        "kpi_ontime": "时效达成率",
        "unit_vehicles": " 辆",
        "warn_unplaced": "有 {unplaced} 件货物因装载约束未能装入车厢（已装 {placed}/{total} 件，详见产物报告）。",
        "warn_unassigned": "有 {n} 个订单未分配。",
        "comparison_title": "优化 vs 基准对比",
        "th_metric": "指标",
        "th_baseline": "基准（贪心）",
        "th_optimized": "优化方案",
        "th_change": "变化",
        "row_vehicles": "车辆数（辆）",
        "row_distance": "总里程（km）",
        "row_cost": "运输费（元）",
        "row_load_rate": "平均载重装载率",
        "row_ontime": "时效达成率",
        "row_unassigned": "未分配订单数",
        "row_fill_rate": "货物装下率",
        "route_map_title": "配送路线图",
        "packing_title": "各车厢 3D 装载图",
        "packing_caption": "{instance} 车厢 3D 装载图",
        "guide_title": "分步装卸指引（{n} 辆车，文字版）",
        "step_title": "分步装载示意（{n} 张，每车最多 5 张）",
        "step_caption": "{instance} · 分步示意 {shot}",
        "llm_title": "LLM 增强（可选）",
        "llm_key_notice": "API Key 仅保存在本次会话内存中，不写入磁盘、不回显。",
        "llm_api_key": "API Key",
        "llm_key_placeholder": "sk-…（留空 = 不启用）",
        "llm_advanced": "高级选项（Base URL / 模型）",
        "llm_base_url": "Base URL",
        "llm_base_url_ph": "https://api.deepseek.com（默认）",
        "llm_model": "模型名",
        "llm_model_ph": "deepseek-chat（默认）",
        "llm_enabled_hint": "已启用 LLM 增强：调用前临时注入运行时环境，用后立即清理。",
        "llm_disabled_hint": "未启用 LLM：自然语言解析走离线规则引擎，指引走模板文本。",
        "llm_env_hint": "检测到环境变量 {var}，未填 Key 时将自动使用它。",
        "nl_title": "自然语言下单（LLM 增强，可选）",
        "nl_lang_note": "解析器当前面向中文输入；点击下方示例可直接体验。",
        "nl_example": "示例",
        "nl_label": "下单需求（自然语言）",
        "nl_parse_btn": "解析需求",
        "nl_parsing": "正在解析（LLM 调用可能需要几秒）…",
        "nl_empty_hint": "请输入需求文本，或点击上方任一示例。",
        "nl_source_llm": "解析来源：LLM（模型 {model}）",
        "nl_source_rule": "解析来源：规则引擎（未启用 LLM 或已自动降级）",
        "nl_source_fallback": "解析来源：规则引擎（LLM 调用不可用，已自动降级：{detail}）",
        "nl_error": "解析失败：{detail}",
        "nl_missing": "自然语言未涵盖的字段（待补）：{fields}",
        "nl_result_title": "结构化解析结果",
        "nl_col_store": "门店",
        "nl_col_window": "时间窗",
        "nl_col_service": "服务时长（分钟）",
        "nl_raw": "原始 JSON",
        "polish_toggle": "润色装卸指引（LLM）",
        "polish_help": "启用后对每辆车的指引文本做 LLM 润色（失败自动回退模板文本）",
        "polish_no_key": "未启用 LLM，已跳过润色：下方仍为模板文本。",
        "polish_running": "正在润色指引（每条一次 LLM 调用）…",
        "polish_source_llm": "来源：LLM 润色",
        "polish_source_fallback": "来源：模板回退（{detail}）",
        "merge_run_btn": "将此订单加入场景并运行智能配载",
        "merge_disabled_hint": "未解析出可用的货物明细，无法加入场景。",
        "merge_spinner": "正在合并场景并重新配载（约 10～30 秒）…",
        "merge_note": "说明：新订单与其目标门店的既有订单合并为一个站点后重跑配载；"
                      "临时数据集写入 outputs/app-tmp/merged-scene/，不改动 data/ 与已入库产物。",
        "merge_ok": "新增订单 {order} 已并入「{store}（{sid}）」并重跑配载：由 {route} 配送"
                    "（车型 {veh}），送达次序 {seq}/{total}，预计 {arrive} 到达。",
        "merge_no_route": "该门店在本次新方案中未被分配到任何车次（详见 routes.json 未分配列表）。",
        "merge_store_not_found": "该门店（{name}）不在当前配送网络内，暂无法加入场景——不编造坐标。",
        "merge_failed": "合并或重跑失败：{detail}",
        "merge_metrics_title": "合并后新方案指标",
        "merge_route_title": "合并后路线图",
        "catalog_title": "数据档案（门店 / 商品）",
        "catalog_note": "可查看当前档案并新增门店 / 商品（MVP 仅支持新增；编辑 / 删除为后续项）。"
                        "扩展档案保存于 outputs/app-tmp/catalog-ext/，不改动原始数据集。"
                        "新门店与新商品将参与「并入场景」与「独立场景」的匹配。",
        "catalog_stores_title": "门店档案",
        "catalog_skus_title": "商品档案",
        "catalog_source_tag": "来源",
        "catalog_base": "基础",
        "catalog_ext": "扩展",
        "catalog_search": "商品搜索（名称 / SKU）",
        "catalog_add_store": "新增门店",
        "catalog_store_name": "门店名称（下单时用此名称，如“新康药房”）",
        "catalog_lat": "纬度",
        "catalog_lng": "经度",
        "catalog_tw_start": "时间窗起（HH:MM）",
        "catalog_tw_end": "时间窗止（HH:MM）",
        "catalog_service": "卸货服务时长（分钟）",
        "catalog_save_store": "保存门店",
        "catalog_add_sku": "新增商品",
        "catalog_sku_name": "商品名称（下单时用此名称）",
        "catalog_weight": "单件重量（kg）",
        "catalog_dims": "尺寸 长 / 宽 / 高（cm）",
        "catalog_fragile": "易碎",
        "catalog_stackable": "可堆叠",
        "catalog_layers": "层数上限",
        "catalog_save_sku": "保存商品",
        "catalog_saved": "已保存：{id}（{name}）",
        "catalog_invalid": "输入无效：{detail}",
        "catalog_sku_missing": "商品「{name}」缺少物理参数，无法加入场景——请先在「数据档案」中新增该商品。",
        "merge_mode_note": "新订单将并入当前数据集的 30 家门店场景，整体重新配载——便于观察新订单如何被安排进既有配送方案。",
        "merge_compare": "方案对比：原方案 {ov} 车 / {op} 件 → 新方案 {nv} 车 / {np} 件（{delta} 件）；里程 {od} → {nd} km。",
        "nl_tab_single": "单条插单（并入现有场景）",
        "nl_tab_multi": "多单独立场景（新建）",
        "multi_note": "与「单条插单」的区别：此处把解析出的多张订单构建为一个独立小场景（仅含这些门店），"
                      "从零规划路线与装载，不受 30 店大场景影响。门店坐标取自基础 / 扩展档案，路网现场重算。",
        "multi_label": "多行下单需求（每行一单，最多 12 行）",
        "multi_fill": "填入内置示例（6 单）",
        "multi_run": "解析全部并构建独立场景",
        "multi_spinner": "正在解析并构建独立场景（约 10～30 秒）…",
        "multi_result_title": "独立场景方案",
        "multi_assign_title": "订单 → 车次分配",
        "multi_col_order": "订单号",
        "multi_col_store": "门店",
        "multi_col_route": "车次",
        "multi_col_seq": "送达次序",
        "multi_col_arrive": "预计到达",
        "multi_errors": "以下行未能处理：",
        "multi_err_parse": "第 {idx} 行：解析失败（{detail}）",
        "multi_err_store": "第 {idx} 行：门店「{name}」不在配送网络内",
        "multi_err_sku": "第 {idx} 行：商品「{name}」缺少物理参数（请先在数据档案建档）",
        "multi_no_valid": "没有可用的订单行：请检查输入后重试。",
        "multi_empty": "请输入至少一行下单需求。",
        "multi_too_many": "行数过多（{n} 行）：MVP 最多支持 12 行。",
        "multi_ok": "独立场景已生成：{n} 单 / {m} 家门店 / {v} 辆车。",
        "footer": "运行产物默认写入 outputs/app-tmp/（隔离目录），不覆盖已入库的 t25-final / t25-baseline。"
                  "命令行等效入口：python run.py --data data/ --out outputs/t25-final --comparison",
    },
    "en": {
        "app_title": "CityLoad — Intelligent Urban Delivery Loading Demo",
        "app_caption": "Central warehouse → 30 pharmacy chain stores | CVRPTW routing + 3D packing "
                       "+ step-by-step loading guide (T31b reproducible)",
        "lang_label": "语言 / Language",
        "dataset_label": "Select dataset",
        "dataset_help": "seed42: primary dataset (central warehouse → 30 stores); "
                        "seed7: validation dataset (30 stores)",
        "comparison_label": "Compare with greedy baseline",
        "comparison_help": "Also solve the greedy baseline and show a real comparison table (a few extra seconds)",
        "run_button": "Run Loading Optimization",
        "spinner": "Solving and rendering figures (about 10–30 s)...",
        "error_prefix": "Run failed: ",
        "info_hint": "Select a dataset and click “Run Loading Optimization”. "
                     "The first run takes about 10–30 s; identical runs reuse the cache.",
        "result_meta": "Dataset: {seed} | {n} figures | Artifacts: {out}",
        "kpi_vehicles": "Vehicles",
        "kpi_distance": "Total Distance",
        "kpi_cost": "Transport Cost",
        "kpi_fill": "Item Fill Rate",
        "kpi_ontime": "On-time Rate",
        "unit_vehicles": "",
        "warn_unplaced": "{unplaced} item(s) could not be loaded due to packing constraints "
                         "({placed}/{total} placed; see artifacts).",
        "warn_unassigned": "{n} order(s) were left unassigned.",
        "comparison_title": "Optimized vs Baseline",
        "th_metric": "Metric",
        "th_baseline": "Baseline (greedy)",
        "th_optimized": "Optimized",
        "th_change": "Change",
        "row_vehicles": "Vehicles",
        "row_distance": "Total Distance (km)",
        "row_cost": "Transport Cost (CNY)",
        "row_load_rate": "Avg Load Rate",
        "row_ontime": "On-time Rate",
        "row_unassigned": "Unassigned Orders",
        "row_fill_rate": "Item Fill Rate",
        "route_map_title": "Delivery Route Map",
        "packing_title": "3D Loading Layout per Vehicle",
        "packing_caption": "{instance} — 3D cargo layout",
        "guide_title": "Step-by-step Loading Guide ({n} vehicles, text)",
        "step_title": "Loading Step Illustrations ({n} figures, ≤5 per vehicle)",
        "step_caption": "{instance} · step {shot}",
        "llm_title": "LLM Enhancement (optional)",
        "llm_key_notice": "Your API key is kept only in this session's memory — never written to disk, never echoed.",
        "llm_api_key": "API Key",
        "llm_key_placeholder": "sk-… (leave empty = disabled)",
        "llm_advanced": "Advanced (Base URL / model)",
        "llm_base_url": "Base URL",
        "llm_base_url_ph": "https://api.deepseek.com (default)",
        "llm_model": "Model",
        "llm_model_ph": "deepseek-chat (default)",
        "llm_enabled_hint": "LLM enhancement enabled: the key is injected at run time and cleaned up right after.",
        "llm_disabled_hint": "LLM disabled: parsing uses the offline rule engine; guides use template text.",
        "llm_env_hint": "Environment variable {var} detected; it is used automatically when no key is entered.",
        "nl_title": "Natural-language Ordering (optional LLM)",
        "nl_lang_note": "The parser currently accepts Chinese input; click an example below to try it.",
        "nl_example": "Example",
        "nl_label": "Order request (natural language)",
        "nl_parse_btn": "Parse request",
        "nl_parsing": "Parsing (an LLM call may take a few seconds)...",
        "nl_empty_hint": "Type a request, or click one of the examples above.",
        "nl_source_llm": "Parsed by: LLM (model {model})",
        "nl_source_rule": "Parsed by: rule engine (LLM disabled or fell back)",
        "nl_source_fallback": "Parsed by: rule engine (LLM unavailable, fell back: {detail})",
        "nl_error": "Parse failed: {detail}",
        "nl_missing": "Fields not covered by the text: {fields}",
        "nl_result_title": "Structured result",
        "nl_col_store": "Store",
        "nl_col_window": "Time window",
        "nl_col_service": "Service (min)",
        "nl_raw": "Raw JSON",
        "polish_toggle": "Polish loading guide (LLM)",
        "polish_help": "Run an LLM polish on each vehicle's guide text (falls back to the template on failure)",
        "polish_no_key": "LLM disabled; polishing skipped — template text is shown below.",
        "polish_running": "Polishing guide (one LLM call per vehicle)...",
        "polish_source_llm": "Source: LLM polish",
        "polish_source_fallback": "Source: template fallback ({detail})",
        "merge_run_btn": "Run Loading Optimization with This Order",
        "merge_disabled_hint": "No usable item details parsed; cannot merge into the scene.",
        "merge_spinner": "Merging scene and re-optimizing (about 10–30 s)...",
        "merge_note": "Note: the new order is merged into the target store's existing order and the scene "
                      "is re-optimized; the temp dataset goes to outputs/app-tmp/merged-scene/ without "
                      "touching data/ or committed artifacts.",
        "merge_ok": "New order {order} merged into {store} ({sid}) and re-optimized: served by {route} "
                    "(vehicle {veh}), stop {seq}/{total}, ETA {arrive}.",
        "merge_no_route": "The store was not assigned to any route in the new plan (see the unassigned list).",
        "merge_store_not_found": "Store \"{name}\" is not in the current delivery network; cannot merge "
                                 "— no coordinates invented.",
        "merge_failed": "Merge / re-optimization failed: {detail}",
        "merge_metrics_title": "Metrics after merge",
        "merge_route_title": "Route map after merge",
        "catalog_title": "Data Catalog (stores / SKUs)",
        "catalog_note": "Review the current catalog and add new stores / SKUs below (MVP: add only; "
                        "edit/delete coming later). The extended catalog lives in "
                        "outputs/app-tmp/catalog-ext/ and never modifies the original datasets. "
                        "New stores and SKUs participate in merge and standalone-scene matching.",
        "catalog_stores_title": "Store catalog",
        "catalog_skus_title": "SKU catalog",
        "catalog_source_tag": "Source",
        "catalog_base": "base",
        "catalog_ext": "extended",
        "catalog_search": "Search SKUs (name / SKU)",
        "catalog_add_store": "Add store",
        "catalog_store_name": "Store name (use it in orders, e.g. \"Xinkang Pharmacy\")",
        "catalog_lat": "Latitude",
        "catalog_lng": "Longitude",
        "catalog_tw_start": "Time window start (HH:MM)",
        "catalog_tw_end": "Time window end (HH:MM)",
        "catalog_service": "Service minutes",
        "catalog_save_store": "Save store",
        "catalog_add_sku": "Add SKU",
        "catalog_sku_name": "SKU name (use it in orders)",
        "catalog_weight": "Unit weight (kg)",
        "catalog_dims": "Dimensions L / W / H (cm)",
        "catalog_fragile": "Fragile",
        "catalog_stackable": "Stackable",
        "catalog_layers": "Max stack layers",
        "catalog_save_sku": "Save SKU",
        "catalog_saved": "Saved: {id} ({name})",
        "catalog_invalid": "Invalid input: {detail}",
        "catalog_sku_missing": "Item \"{name}\" has no physical parameters and cannot join the scene "
                               "— please add it in the Data Catalog first.",
        "merge_mode_note": "The new order joins the current 30-store dataset scene and the whole plan is "
                           "re-optimized — making it easy to see how the order fits into the existing routes.",
        "merge_compare": "Plan comparison: {ov} vehicles / {op} items → {nv} vehicles / {np} items "
                         "({delta} items); distance {od} → {nd} km.",
        "nl_tab_single": "Single order (merge into existing scene)",
        "nl_tab_multi": "Multiple orders (standalone scene)",
        "multi_note": "Unlike \"single order\", here the parsed orders build a standalone mini-scene "
                      "(only these stores) — routed and packed from scratch, independent of the 30-store "
                      "scene. Store coordinates come from the base/extended catalog; the network is rebuilt.",
        "multi_label": "Order requests, one per line (up to 12 lines)",
        "multi_fill": "Fill built-in example (6 orders)",
        "multi_run": "Parse all and build standalone scene",
        "multi_spinner": "Parsing and building the standalone scene (about 10–30 s)...",
        "multi_result_title": "Standalone scene plan",
        "multi_assign_title": "Order → route assignment",
        "multi_col_order": "Order ID",
        "multi_col_store": "Store",
        "multi_col_route": "Route",
        "multi_col_seq": "Stop",
        "multi_col_arrive": "ETA",
        "multi_errors": "Lines that could not be processed:",
        "multi_err_parse": "Line {idx}: parse failed ({detail})",
        "multi_err_store": "Line {idx}: store \"{name}\" is not in the delivery network",
        "multi_err_sku": "Line {idx}: item \"{name}\" lacks physical parameters (add it in the catalog)",
        "multi_no_valid": "No usable order lines — please check the input.",
        "multi_empty": "Please enter at least one order line.",
        "multi_too_many": "Too many lines ({n}): the MVP supports up to 12.",
        "multi_ok": "Standalone scene built: {n} orders / {m} stores / {v} vehicles.",
        "footer": "Artifacts are written to outputs/app-tmp/ (isolated); t25-final / t25-baseline "
                  "are never overwritten. CLI equivalent: "
                  "python run.py --data data/ --out outputs/t25-final --comparison",
    },
}

#: 图表/指引语言边界说明（固定双语展示，不随语言切换）
LANG_NOTICE = "图表与指引正文为中文（渲染层生成） / Figures & guide text remain in Chinese"

#: st.image 的图注在 AppTest 中需为 str；caption 组装时使用


def t(key: str, **kw) -> str:
    """按当前会话语言取界面文案（默认中文）；空字符串是合法值，不触发回退。"""
    lang = st.session_state.get("lang", "zh")
    table = STRINGS.get(lang, STRINGS["zh"])
    text = table[key] if key in table else STRINGS["zh"][key]
    return text.format(**kw) if kw else text


# ---------------------------------------------------------------- 核心逻辑（无 Streamlit 依赖）


def _group_figures(figures: list[Path], packing: dict) -> dict:
    """把管线图件路径按类型分组（T50g 抽取，主流程与 NL 流程共用）。

    返回 ``{"route_map": Path|None, "packing_figs": [(Path, instance)],
    "step_figs": [(Path, instance, shot)]}``；
    文件名中的 ``V{n}`` 按 packing["vehicles"] 出现序号映射为真实车辆编号（如 R04-1）。
    """
    instances = [v.get("vehicle_instance", "") for v in (packing or {}).get("vehicles", [])]

    def _instance(path: Path) -> str:
        m = re.fullmatch(r"(?:packing|step)_V(\d+)(?:_\d+)?\.png", path.name)
        if m and 1 <= int(m.group(1)) <= len(instances):
            return instances[int(m.group(1)) - 1]
        return path.stem

    route_map = next((p for p in figures if p.name == "route_map.png"), None)
    packing_figs = [(p, _instance(p)) for p in figures if p.name.startswith("packing_")]
    step_figs = [(p, _instance(p), int(re.search(r"_(\d+)\.png$", p.name).group(1)))
                 for p in figures if p.name.startswith("step_")]
    return {"route_map": route_map, "packing_figs": packing_figs, "step_figs": step_figs}


def execute(seed_key: str, data_dir: str, comparison: bool) -> dict:
    """跑完整管线并返回展示数据（与语言无关）；产物写入 outputs/app-tmp/{seed_key}/。"""
    out_dir = APP_TMP / seed_key / ("comparison" if comparison else "single")
    result = pipeline.run_pipeline(data_dir, out_dir, baseline=False, comparison=comparison)

    figures = [Path(p) for p in result["figures"]]
    groups = _group_figures(figures, result["packing"])
    return {
        "seed": seed_key,
        "metrics": result["metrics"].get("metrics", {}),
        "comparison": result["metrics"].get("comparison"),
        "route_map": str(groups["route_map"]) if groups["route_map"] else None,
        "packing_figs": [(inst, str(p)) for p, inst in groups["packing_figs"]],
        "step_figs": [(inst, shot, str(p)) for p, inst, shot in groups["step_figs"]],
        "guides": result["guide_text"].get("guides", []),
        "out_dir": str(out_dir),
        "figure_count": len(figures),
    }


@st.cache_data(show_spinner=False)
def run_cached(seed_key: str, data_dir: str, comparison: bool) -> dict:
    """带缓存的执行入口（与语言无关，缓存跨语言共享；同参数二次点击秒开）。"""
    return execute(seed_key, data_dir, comparison)


# ---------------------------------------------------------------- UI 组件


def _lang_selector() -> str:
    """侧边栏语言选择器（默认中文；选择状态存 session_state）。"""
    choice = st.sidebar.radio(t("lang_label"), ("中文", "English"),
                              key="lang_choice", horizontal=True)
    lang = "zh" if choice == "中文" else "en"
    st.session_state["lang"] = lang
    return lang


# ---------------------------------------------------------------- LLM 增强（可选）

@contextmanager
def _llm_env():
    """把本会话的 LLM 配置临时注入环境变量，退出时恢复原值。

    安全红线（T50c）：Key 仅存于 session_state 内存；注入仅覆盖本次调用窗口，
    用后立即恢复；全程不写磁盘、不打印、不回显明文。
    """
    mapping = {
        "CITYLOAD_LLM_API_KEY": st.session_state.get("llm_api_key", ""),
        "CITYLOAD_LLM_BASE_URL": st.session_state.get("llm_base_url", ""),
        "CITYLOAD_LLM_MODEL": st.session_state.get("llm_model", ""),
    }
    inject = {k: str(v).strip() for k, v in mapping.items() if str(v or "").strip()}
    previous = {k: os.environ.get(k) for k in inject}
    os.environ.update(inject)
    try:
        yield
    finally:
        for key, old in previous.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def _has_session_key() -> bool:
    """用户是否在侧边栏填写了 API Key。"""
    return bool(str(st.session_state.get("llm_api_key", "") or "").strip())


def _ambient_llm_var() -> str:
    """未填 Key 时，检测环境中已有的可用 Key 变量名（只报告变量名，不读取值）。"""
    if _has_session_key():
        return ""
    for name in ("CITYLOAD_LLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        if os.environ.get(name):
            return name
    return ""


def _llm_panel() -> None:
    """侧边栏 LLM 增强面板：Key（password）+ 高级选项（Base URL / 模型）。"""
    with st.sidebar.expander(t("llm_title"), expanded=False):
        st.caption(t("llm_key_notice"))
        st.text_input(t("llm_api_key"), type="password", key="llm_api_key",
                      placeholder=t("llm_key_placeholder"))
        if _has_session_key():
            st.success(t("llm_enabled_hint"))
        else:
            st.info(t("llm_disabled_hint"))
            ambient = _ambient_llm_var()
            if ambient:
                st.caption(t("llm_env_hint", var=ambient))
    with st.sidebar.expander(t("llm_advanced"), expanded=False):
        st.text_input(t("llm_base_url"), key="llm_base_url", placeholder=t("llm_base_url_ph"))
        st.text_input(t("llm_model"), key="llm_model", placeholder=t("llm_model_ph"))


def _kpi_row(m: dict) -> None:
    placed, total = m.get("placed_item_count"), m.get("total_item_count")
    rate = f"{placed / total * 100:.1f}%" if total and placed is not None else "—"
    cols = st.columns(5)
    cols[0].metric(t("kpi_vehicles"), f"{m.get('vehicle_count', '—')}{t('unit_vehicles')}")
    cols[1].metric(t("kpi_distance"), f"{m.get('total_distance_km', '—')} km")
    cols[2].metric(t("kpi_cost"), f"¥{m.get('total_cost', '—')}")
    cols[3].metric(t("kpi_fill"), rate)
    cols[4].metric(t("kpi_ontime"), f"{(m.get('on_time_rate') or 0) * 100:.1f}%")


def _comparison_rows(comp: dict) -> list[dict]:
    b, o = comp.get("baseline", {}), comp.get("optimized", {})

    def plain(v: float) -> str:
        return f"{v:.0f}"

    def km(v: float) -> str:
        return f"{v:.1f}"

    def money(v: float) -> str:
        return f"¥{v:.2f}"

    def pct(v: float) -> str:
        return f"{v * 100:.1f}%"

    rows = []

    def add(name: str, key: str, fmt, lower_better: bool) -> None:
        bv, ov = b.get(key), o.get(key)
        if bv is None or ov is None:
            return
        delta = (ov - bv) / abs(bv) if bv else None
        arrow = "↓" if ov < bv else ("↑" if ov > bv else "＝")
        rows.append({
            t("th_metric"): name,
            t("th_baseline"): fmt(bv),
            t("th_optimized"): fmt(ov),
            t("th_change"): f"{arrow} {delta * 100:+.1f}%" if delta is not None else "—",
        })

    add(t("row_vehicles"), "vehicle_count", plain, True)
    add(t("row_distance"), "total_distance_km", km, True)
    add(t("row_cost"), "total_cost", money, True)
    add(t("row_load_rate"), "avg_weight_load_rate", pct, False)
    add(t("row_ontime"), "on_time_rate", pct, False)
    add(t("row_unassigned"), "unassigned_order_count", plain, True)

    tb, pb = b.get("total_item_count"), b.get("placed_item_count")
    to, po = o.get("total_item_count"), o.get("placed_item_count")
    if all(v is not None for v in (tb, pb, to, po)) and tb and to:
        rows.append({
            t("th_metric"): t("row_fill_rate"),
            t("th_baseline"): f"{pb / tb * 100:.1f}% ({pb}/{tb})",
            t("th_optimized"): f"{po / to * 100:.1f}% ({po}/{to})",
            t("th_change"): f"↑ {(po / to - pb / tb) * 100:+.1f}pp",
        })
    return rows


def _render_packing_gallery(packing_figs: list) -> None:
    """车厢 3D 装载图区（两列网格）。主界面与 NL 流程（插单/多单）共用。"""
    if not packing_figs:
        return
    st.subheader(t("packing_title"))
    cols = st.columns(2)
    for i, (instance, path) in enumerate(packing_figs):
        with cols[i % 2]:
            st.image(path, caption=t("packing_caption", instance=instance), width="stretch")


def _render_step_gallery(step_figs: list) -> None:
    """分步装载示意图区（三列网格，置于折叠面板内）。主界面与 NL 流程共用。"""
    if not step_figs:
        return
    with st.expander(t("step_title", n=len(step_figs))):
        cols = st.columns(3)
        for i, (instance, shot, path) in enumerate(step_figs):
            with cols[i % 3]:
                st.image(path, caption=t("step_caption", instance=instance, shot=shot),
                         width="stretch")


def _render_result(r: dict) -> None:
    m = r["metrics"]
    st.caption(t("result_meta", seed=r["seed"], n=r["figure_count"], out=r["out_dir"]))
    _kpi_row(m)

    if m.get("unplaced_item_count"):
        st.warning(t("warn_unplaced", unplaced=m["unplaced_item_count"],
                     placed=m.get("placed_item_count"), total=m.get("total_item_count")))
    if m.get("unassigned_order_count"):
        st.warning(t("warn_unassigned", n=m["unassigned_order_count"]))

    if r.get("comparison"):
        st.subheader(t("comparison_title"))
        st.dataframe(_comparison_rows(r["comparison"]), width="stretch", hide_index=True)

    if r["route_map"]:
        st.subheader(t("route_map_title"))
        st.image(r["route_map"], width="stretch")

    _render_packing_gallery(r["packing_figs"])

    if r["guides"]:
        with st.expander(t("guide_title", n=len(r["guides"]))):
            _render_guides(r["guides"])

    _render_step_gallery(r["step_figs"])


# ---------------------------------------------------------------- 指引润色 / 自然语言下单


def _render_guides(guides: list) -> None:
    """分步装卸指引展示；可选 LLM 润色（有 Key 才执行；结果按文本指纹缓存）。"""
    has_llm = _has_session_key() or bool(_ambient_llm_var())
    polish_on = st.toggle(t("polish_toggle"), key="polish_toggle", help=t("polish_help"))
    if polish_on and not has_llm:
        st.info(t("polish_no_key"))
        polish_on = False
    cache = st.session_state.setdefault("polish_cache", {})
    for g in guides:
        instance = g.get("vehicle_instance", "")
        raw = str(g.get("text", "")).strip()
        st.markdown(f"**{instance}**")
        if not polish_on:
            st.text(raw)
            continue
        fingerprint = hashlib.md5(
            (raw + str(st.session_state.get("llm_model", ""))).encode("utf-8")).hexdigest()
        if fingerprint not in cache:
            from agent.explainer import polish_guide_text  # 延迟导入
            with st.spinner(t("polish_running")):
                with _llm_env():
                    cache[fingerprint] = polish_guide_text(raw)
        out = cache[fingerprint]
        st.text(str(out.get("text", raw)))
        if out.get("source") == "llm":
            st.caption(t("polish_source_llm"))
        else:
            st.caption(t("polish_source_fallback", detail=str(out.get("detail", "—"))[:120]))


def _render_parsed_order(result: dict) -> None:
    """友好展示 parse_requirement 的结构化结果（概览 + 明细 + 原始 JSON）。"""
    window = result.get("time_window")
    window_txt = "–".join(str(x) for x in window) if isinstance(window, (list, tuple)) and window else "—"
    st.dataframe([{
        t("nl_col_store"): result.get("store_name") or "—",
        t("nl_col_window"): window_txt,
        t("nl_col_service"): result.get("service_minutes", "—"),
    }], width="stretch", hide_index=True)
    items = result.get("items") or []
    if items:
        st.dataframe(items, width="stretch", hide_index=True)
    with st.expander(t("nl_raw")):
        st.json(result, expanded=False)


def _nl_parse_only(text: str) -> None:
    """执行解析并把结果写入 session_state（跨 rerun 保持，供展示与 T50d 串联复用）。"""
    text = str(text or "").strip()
    if not text:
        st.warning(t("nl_empty_hint"))
        return
    from agent.parser import parse_requirement  # 延迟导入，保持 app 启动轻量

    with st.spinner(t("nl_parsing")):
        with _llm_env():  # 仅在本调用窗口注入 Key，用后恢复
            st.session_state["nl_result"] = parse_requirement(text)


def _render_nl_result(result: dict, seed_key: str) -> None:
    """展示解析结果（来源/结构）与 T50d「并入场景 → 重跑配载」串联入口。"""
    if result.get("error"):
        st.error(t("nl_error", detail=result.get("detail") or result.get("error")))
        return

    meta = result.get("parse_meta") or {}
    source = str(meta.get("source") or "rule")
    model = str(st.session_state.get("llm_model") or "") or "deepseek-chat"
    if source == "llm":
        st.success(t("nl_source_llm", model=model))
    elif source == "rule_fallback":
        st.info(t("nl_source_fallback", detail=str(meta.get("llm_error") or "—")[:120]))
    else:
        st.info(t("nl_source_rule"))
    missing = result.get("missing_fields") or []
    if missing:
        st.caption(t("nl_missing", fields="、".join(str(m) for m in missing)))
    st.markdown(f"**{t('nl_result_title')}**")
    _render_parsed_order(result)

    # ---- T50d/T50f：将订单并入场景并重跑配载 ----
    st.divider()
    items = result.get("items") or []
    if not items:
        st.button(t("merge_run_btn"), key="merge_run", disabled=True)
        st.caption(t("merge_disabled_hint"))
    else:
        run_merge = st.button(t("merge_run_btn"), type="primary", key="merge_run")
        st.caption(t("merge_mode_note"))  # T50f：说明插单语义（并入 30 店场景重排）
        if run_merge:
            with st.spinner(t("merge_spinner")):
                try:
                    base_metrics = {}
                    if st.session_state.get("result_seed") == seed_key:
                        base_metrics = (st.session_state.get("result") or {}).get("metrics") or {}
                    if not base_metrics:  # 未跑过主方案：先取原场景指标（缓存命中则秒回）
                        base = run_cached(seed_key, str(DATASETS[seed_key]["dir"]), False)
                        st.session_state["result"] = base
                        st.session_state["result_seed"] = seed_key
                        base_metrics = base["metrics"]
                    merged = _merge_and_run(result, seed_key)
                    merged["base_metrics"] = base_metrics  # T50f 方案对比（重跑前指标）
                    st.session_state["merged_result"] = merged
                except Exception as exc:  # 管线异常：明确提示，不崩界面
                    st.session_state["merged_result"] = {
                        "error": "pipeline", "detail": f"{type(exc).__name__}: {exc}"}

    merged = st.session_state.get("merged_result")
    if merged and merged.get("seed") not in (None, seed_key):
        merged = None  # 数据集已切换：旧合并结果不再适用
    if merged:
        _render_merged_result(merged)


# ---------------------------------------------------------------- 扩展档案（T50e）


def _load_ext_stores() -> list:
    """读取扩展门店档案（outputs/app-tmp/catalog-ext/stores.json；会话持久）。"""
    path = CATALOG_EXT / "stores.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("stores") or []
    except Exception:
        return []


def _save_ext_store(store: dict) -> str:
    """追加保存一家扩展门店，返回分配的编号（C##）。"""
    stores = _load_ext_stores()
    existing = {s.get("store_id") for s in stores}
    n = 1
    while f"C{n:02d}" in existing:
        n += 1
    store = dict(store)
    store["store_id"] = f"C{n:02d}"
    stores.append(store)
    CATALOG_EXT.mkdir(parents=True, exist_ok=True)
    (CATALOG_EXT / "stores.json").write_text(
        json.dumps({"stores": stores}, ensure_ascii=False, indent=2), encoding="utf-8")
    return store["store_id"]


def _load_ext_skus() -> list:
    """读取扩展商品档案（outputs/app-tmp/catalog-ext/skus.json）。"""
    path = CATALOG_EXT / "skus.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("skus") or []
    except Exception:
        return []


def _save_ext_sku(sku: dict) -> str:
    """追加保存一条扩展商品，返回分配的编号（CSKU###）。"""
    skus = _load_ext_skus()
    existing = {s.get("sku") for s in skus}
    n = 1
    while f"CSKU{n:03d}" in existing:
        n += 1
    sku = dict(sku)
    sku["sku"] = f"CSKU{n:03d}"
    skus.append(sku)
    CATALOG_EXT.mkdir(parents=True, exist_ok=True)
    (CATALOG_EXT / "skus.json").write_text(
        json.dumps({"skus": skus}, ensure_ascii=False, indent=2), encoding="utf-8")
    return sku["sku"]


def _collect_base_skus(order_list: list) -> list:
    """从基础订单聚合商品档案（distinct SKU，含物理参数）。"""
    seen = {}
    for o in order_list:
        for it in o.get("items") or []:
            sku = it.get("sku")
            if sku and sku not in seen:
                seen[sku] = {k: it.get(k) for k in
                             ("sku", "name", "weight_kg", "length_cm", "width_cm", "height_cm",
                              "fragile", "stackable", "max_stack_layers")}
    return sorted(seen.values(), key=lambda x: str(x["sku"]))


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Haversine 距离（口径同 data/gen_data.py：R=6371.0088 km）。"""
    import math

    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return 2.0 * 6371.0088 * math.asin(math.sqrt(a))


def _build_network(nodes: list) -> dict:
    """由节点表重建路网：对称距离矩阵 + 30 km/h 时长矩阵（口径同 data/gen_data.py）。"""
    n = len(nodes)
    dist = [[0.0] * n for _ in range(n)]
    for a in range(n):
        for b in range(a + 1, n):
            d = round(_haversine_km(nodes[a]["lat"], nodes[a]["lng"],
                                    nodes[b]["lat"], nodes[b]["lng"]), 4)
            dist[a][b] = dist[b][a] = d
    dur = [[round(d / 30.0 * 60.0, 2) for d in row] for row in dist]
    return {"nodes": [dict(nd) for nd in nodes], "node_order": [nd["id"] for nd in nodes],
            "distance_matrix_km": dist, "duration_matrix_min": dur}


def _time_hhmm_ok(value: str) -> bool:
    return bool(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(value or "").strip()))


# ---------------------------------------------------------------- T50d 合并与重跑


def _match_store(parsed: dict, order_list: list, ext_stores: list | None = None) -> dict | None:
    """在当前门店库中反查门店（不编造坐标），失败返回 None。

    匹配顺序 = **基础档案 + 扩展档案**（T50e）：
    ① store_id 精确；② 门店名精确（归一空格）；③ 「连锁药店+尾号」提取（规则解析器
    可能截断店名尾号，故同时扫描 raw_text 原文）；④ 店名中的纯数字编号；
    ⑤ 扩展档案：名称精确 / 原文包含扩展店名。
    """
    sid = str(parsed.get("store_id") or "").strip().upper()
    by_id = {o["store_id"]: o for o in order_list if o.get("store_id")}
    if sid in by_id:
        return by_id[sid]

    norm = lambda s: re.sub(r"\s+", "", str(s or ""))  # noqa: E731
    name = norm(parsed.get("store_name"))
    for o in order_list:
        if name and norm(o.get("store_name")) == name:
            return o

    meta = parsed.get("parse_meta") or {}
    raw = norm(meta.get("raw_text"))
    for src in (name, raw):
        m = re.search(r"连锁药店\s*0*(\d{1,2})", src)
        if m:
            cand = f"S{int(m.group(1)):02d}"
            if cand in by_id:
                return by_id[cand]

    m = re.search(r"(\d{1,2})", name)
    if m:
        cand = f"S{int(m.group(1)):02d}"
        if cand in by_id:
            return by_id[cand]

    # ---- 扩展档案（T50e）：名称精确 / 原文包含扩展店名 ----
    for st_ in ext_stores or []:
        ext_name = norm(st_.get("store_name"))
        if ext_name and (ext_name == name or ext_name in raw):
            return st_
    return None


def _enrich_items_with_ext(parsed: dict) -> None:
    """用扩展商品档案补全 items 物理参数（T50e；仅填补缺失项，不覆盖已有参数）。"""
    ext = _load_ext_skus()
    if not ext:
        return

    def norm(s):
        return re.sub(r"\s+", "", str(s or "")).lower()

    for it in parsed.get("items") or []:
        if it.get("weight_kg") and it.get("length_cm") and it.get("width_cm") \
                and it.get("height_cm"):
            continue
        item_name = norm(it.get("name"))
        if not item_name:
            continue
        for sku in ext:
            sku_name = norm(sku.get("name"))
            if sku_name and (sku_name in item_name or item_name in sku_name):
                it["sku"] = sku.get("sku")
                for key in ("weight_kg", "length_cm", "width_cm", "height_cm",
                            "fragile", "stackable", "max_stack_layers"):
                    it[key] = sku.get(key)
                break


def _next_merge_order_id(order_list: list) -> str:
    """生成不与场景冲突的新订单号（OMERGE-001 起）。"""
    existing = {str(o.get("order_id")) for o in order_list}
    n = 1
    while f"OMERGE-{n:03d}" in existing:
        n += 1
    return f"OMERGE-{n:03d}"


def _merge_and_run(parsed: dict, seed_key: str) -> dict:
    """T50d/T50e：把解析订单并入当前场景（临时目录），重跑配载并返回展示信息。

    合并规则（为保持“每门店一单”的路由契约）：
    * 基础门店 → 新订单并入其目标门店的既有订单（items 追加、time_window 取新值、
      service_minutes 相加、merged_orders 留痕）；
    * 扩展门店（T50e 档案新增，有坐标）→ 追加为场景新订单，并用 Haversine 重建
      路网（加节点 + 重算距离/时长矩阵，口径同 data/gen_data.py）。
    坐标一律取自档案，绝不编造；产物仅写 outputs/app-tmp/merged-scene/。
    """
    data_dir = Path(DATASETS[seed_key]["dir"])
    orders = json.loads((data_dir / "orders.json").read_text(encoding="utf-8"))
    network = json.loads((data_dir / "network.json").read_text(encoding="utf-8"))

    _enrich_items_with_ext(parsed)  # T50e：扩展商品档案补全物理参数
    store = _match_store(parsed, orders.get("orders") or [], _load_ext_stores())
    if store is None:
        return {"error": "store_not_found", "store_name": parsed.get("store_name")}

    # 物理参数齐全性校验（不编造参数；缺失时提示先建档）
    for it in parsed.get("items") or []:
        if not (it.get("weight_kg") and it.get("length_cm") and it.get("width_cm")
                and it.get("height_cm")):
            return {"error": "sku_missing", "name": it.get("name")}

    target_sid = store["store_id"]
    merged = copy.deepcopy(orders)
    new_order_id = _next_merge_order_id(merged["orders"])
    new_items = _normalize_items(parsed.get("items") or [])

    is_extension = target_sid not in {o.get("store_id") for o in merged["orders"]}
    if is_extension:
        # 扩展门店：追加新订单 + 重建路网（含新节点）
        merged["orders"].append({
            "order_id": new_order_id,
            "store_id": target_sid,
            "store_name": store.get("store_name"),
            "lat": float(store["lat"]),
            "lng": float(store["lng"]),
            "time_window": list(parsed.get("time_window")
                                or store.get("time_window") or ["08:00", "18:00"]),
            "service_minutes": int(store.get("service_minutes")
                                   or parsed.get("service_minutes") or 15),
            "items": new_items,
        })
        nodes = [dict(nd) for nd in network.get("nodes") or []]
        nodes.append({"id": target_sid, "lat": float(store["lat"]), "lng": float(store["lng"])})
        network = _build_network(nodes)
    else:
        # 基础门店：并入既有订单
        target = next(o for o in merged["orders"] if o.get("store_id") == target_sid)
        target["items"] = list(target.get("items") or []) + new_items
        if parsed.get("time_window"):
            target["time_window"] = list(parsed["time_window"])
        try:
            target["service_minutes"] = int(target.get("service_minutes") or 0) + \
                int(parsed.get("service_minutes") or 0)
        except (TypeError, ValueError):
            pass
        target.setdefault("merged_orders", []).append(new_order_id)  # 留痕（下游忽略）

    # ---- 写临时数据集（隔离目录）----
    dataset_dir = APP_TMP / "merged-scene" / seed_key / "data"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    (dataset_dir / "orders.json").write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    (dataset_dir / "network.json").write_text(
        json.dumps(network, ensure_ascii=False, indent=2), encoding="utf-8")
    for name in ("vehicles.json", "rules.json"):
        (dataset_dir / name).write_text((data_dir / name).read_text(encoding="utf-8"),
                                        encoding="utf-8")

    # ---- 重跑配载（复用既有管线；产物写 merged-scene/out/）----
    out_dir = APP_TMP / "merged-scene" / seed_key / "out"
    result = pipeline.run_pipeline(str(dataset_dir), out_dir, baseline=False, comparison=False)

    route_info = None
    for route in result["routes"].get("routes") or []:
        seq = [s for s in (route.get("sequence") or []) if s != "WH01"]
        if target_sid in seq:
            stop = next((d for d in (route.get("stop_details") or [])
                         if d.get("node") == target_sid), {})
            route_info = {
                "route_id": route.get("route_id"),
                "vehicle_type": route.get("vehicle_type"),
                "seq": seq.index(target_sid) + 1,
                "total": len(seq),
                "arrive": stop.get("arrive"),
            }
            break

    figures = [Path(p) for p in result["figures"]]
    groups = _group_figures(figures, result["packing"])
    return {
        "error": None,
        "seed": seed_key,
        "store": store,
        "new_order_id": new_order_id,
        "route_info": route_info,
        "metrics": result["metrics"].get("metrics") or {},
        "route_map": str(groups["route_map"]) if groups["route_map"] else None,
        "packing_figs": [(inst, str(p)) for p, inst in groups["packing_figs"]],
        "step_figs": [(inst, shot, str(p)) for p, inst, shot in groups["step_figs"]],
        "out_dir": str(out_dir),
    }


def _render_merged_result(m: dict) -> None:
    """展示 T50d 合并重跑结果：该门店的车次分配 + 新方案指标 + 路线图。"""
    if m.get("error"):
        if m.get("error") == "store_not_found":
            st.warning(t("merge_store_not_found", name=str(m.get("store_name") or "—")))
        elif m.get("error") == "sku_missing":
            st.warning(t("catalog_sku_missing", name=str(m.get("name") or "—")))
        else:
            st.error(t("merge_failed", detail=str(m.get("detail") or m.get("error"))))
        return

    store, info = m["store"], m.get("route_info")
    if info:
        st.success(t("merge_ok", order=m["new_order_id"],
                     store=store.get("store_name") or "—", sid=store.get("store_id") or "—",
                     route=info.get("route_id") or "—", veh=info.get("vehicle_type") or "—",
                     seq=info.get("seq"), total=info.get("total"),
                     arrive=info.get("arrive") or "—"))
    else:
        st.warning(t("merge_no_route"))

    # T50f：方案对比（数据从重跑前后的两个 metrics 读取，不硬编码）
    base_m = m.get("base_metrics") or {}
    new_m = m.get("metrics") or {}
    ov, nv = base_m.get("vehicle_count"), new_m.get("vehicle_count")
    op, np_ = base_m.get("placed_item_count"), new_m.get("placed_item_count")
    if None not in (ov, nv, op, np_):
        delta = int(np_) - int(op)
        st.info(t("merge_compare",
                  ov=f"{ov:.0f}", op=f"{op}", nv=f"{nv:.0f}", np=f"{np_}",
                  delta=f"+{delta}" if delta >= 0 else str(delta),
                  od=f"{(base_m.get('total_distance_km') or 0):.1f}",
                  nd=f"{(new_m.get('total_distance_km') or 0):.1f}"))
    st.caption(t("merge_note"))

    mm = m.get("metrics") or {}
    st.markdown(f"**{t('merge_metrics_title')}**")
    placed, total = mm.get("placed_item_count"), mm.get("total_item_count")
    cols = st.columns(4)
    cols[0].metric(t("kpi_vehicles"), f"{mm.get('vehicle_count', '—')}{t('unit_vehicles')}")
    cols[1].metric(t("kpi_distance"), f"{mm.get('total_distance_km', '—')} km")
    cols[2].metric(t("kpi_cost"), f"¥{mm.get('total_cost', '—')}")
    cols[3].metric(t("kpi_fill"),
                   f"{placed / total * 100:.1f}%" if total and placed is not None else "—")
    if m.get("route_map"):
        st.markdown(f"**{t('merge_route_title')}**")
        st.image(m["route_map"], width="stretch")

    # T50g：补展示车厢 3D 装载图 + 分步装载图；新订单所在车辆的图优先排列
    packing_figs = list(m.get("packing_figs") or [])
    step_figs = list(m.get("step_figs") or [])
    rid = str((info or {}).get("route_id") or "")
    if rid:
        packing_figs.sort(key=lambda x: 0 if str(x[0]).startswith(rid) else 1)
        step_figs.sort(key=lambda x: 0 if str(x[0]).startswith(rid) else 1)
    _render_packing_gallery(packing_figs)
    _render_step_gallery(step_figs)


# ---------------------------------------------------------------- 数据档案 UI（T50e）


def _render_catalog_section(seed_key: str) -> None:
    """T50e：门店 / 商品档案展示 + 新增表单（扩展档案持久于 outputs/app-tmp/catalog-ext/）。"""
    st.caption(t("catalog_note"))
    notice = st.session_state.pop("cat_notice", None)
    if notice:
        st.success(notice)

    data_dir = Path(DATASETS[seed_key]["dir"])
    base_orders = json.loads((data_dir / "orders.json").read_text(encoding="utf-8"))["orders"]
    ext_stores = _load_ext_stores()
    ext_skus = _load_ext_skus()

    # ---- 门店档案 ----
    st.markdown(f"**{t('catalog_stores_title')}**")

    def _store_row(o: dict, tag: str) -> dict:
        return {
            "ID": o.get("store_id"),
            t("nl_col_store"): o.get("store_name"),
            t("catalog_lat"): o.get("lat"),
            t("catalog_lng"): o.get("lng"),
            t("nl_col_window"): "–".join(str(x) for x in (o.get("time_window") or [])) or "—",
            t("nl_col_service"): o.get("service_minutes"),
            t("catalog_source_tag"): tag,
        }

    store_rows = [_store_row(o, t("catalog_base")) for o in base_orders]
    store_rows += [_store_row(s, t("catalog_ext")) for s in ext_stores]
    st.dataframe(store_rows, width="stretch", hide_index=True)

    with st.expander(t("catalog_add_store"), expanded=False):
        s_name = st.text_input(t("catalog_store_name"), key="cat_store_name")
        c1, c2, c3 = st.columns(3)
        s_lat = c1.number_input(t("catalog_lat"), min_value=-90.0, max_value=90.0,
                                value=30.3000, step=0.001, format="%.4f", key="cat_store_lat")
        s_lng = c2.number_input(t("catalog_lng"), min_value=-180.0, max_value=180.0,
                                value=120.1500, step=0.001, format="%.4f", key="cat_store_lng")
        s_service = c3.number_input(t("catalog_service"), min_value=1, max_value=120,
                                    value=15, key="cat_store_service")
        c4, c5 = st.columns(2)
        s_tw1 = c4.text_input(t("catalog_tw_start"), value="09:00", key="cat_store_tw1")
        s_tw2 = c5.text_input(t("catalog_tw_end"), value="17:00", key="cat_store_tw2")
        if st.button(t("catalog_save_store"), key="cat_store_save"):
            detail = ""
            if not str(s_name).strip():
                detail = t("catalog_store_name")
            elif not _time_hhmm_ok(s_tw1) or not _time_hhmm_ok(s_tw2):
                detail = "HH:MM"
            elif str(s_tw1).strip() >= str(s_tw2).strip():
                detail = f"{s_tw1} >= {s_tw2}"
            if detail:
                st.error(t("catalog_invalid", detail=detail))
            else:
                new_id = _save_ext_store({
                    "store_name": str(s_name).strip(),
                    "lat": float(s_lat), "lng": float(s_lng),
                    "time_window": [str(s_tw1).strip(), str(s_tw2).strip()],
                    "service_minutes": int(s_service),
                })
                st.session_state["cat_notice"] = t("catalog_saved", id=new_id,
                                                   name=str(s_name).strip())
                st.rerun()

    # ---- 商品档案 ----
    st.markdown(f"**{t('catalog_skus_title')}**")
    query = str(st.text_input(t("catalog_search"), key="cat_sku_query") or "").strip().lower()

    def _sku_row(s: dict, tag: str) -> dict:
        return {
            "SKU": s.get("sku"),
            t("catalog_sku_name"): s.get("name"),
            t("catalog_weight"): s.get("weight_kg"),
            t("catalog_dims"): "×".join(str(s.get(k)) for k in
                                        ("length_cm", "width_cm", "height_cm")),
            t("catalog_fragile"): "✓" if s.get("fragile") else "",
            t("catalog_stackable"): "✓" if s.get("stackable") else "",
            t("catalog_layers"): s.get("max_stack_layers"),
            t("catalog_source_tag"): tag,
        }

    sku_rows = [_sku_row(s, t("catalog_base")) for s in _collect_base_skus(base_orders)]
    sku_rows += [_sku_row(s, t("catalog_ext")) for s in ext_skus]
    if query:
        sku_rows = [r for r in sku_rows
                    if query in str(r["SKU"]).lower()
                    or query in str(r[t("catalog_sku_name")]).lower()]
    st.dataframe(sku_rows, width="stretch", hide_index=True)

    with st.expander(t("catalog_add_sku"), expanded=False):
        k_name = st.text_input(t("catalog_sku_name"), key="cat_sku_name_input")
        c1, c2 = st.columns([1, 3])
        k_weight = c1.number_input(t("catalog_weight"), min_value=0.01, max_value=100.0,
                                   value=1.0, step=0.1, key="cat_sku_weight")
        with c2:
            st.caption(t("catalog_dims"))
            d1, d2, d3 = st.columns(3)
            k_l = d1.number_input("L", min_value=1.0, max_value=200.0, value=20.0,
                                  key="cat_sku_l")
            k_w = d2.number_input("W", min_value=1.0, max_value=200.0, value=15.0,
                                  key="cat_sku_w")
            k_h = d3.number_input("H", min_value=1.0, max_value=200.0, value=10.0,
                                  key="cat_sku_h")
        c3, c4, c5 = st.columns(3)
        k_fragile = c3.checkbox(t("catalog_fragile"), key="cat_sku_fragile")
        k_stackable = c4.checkbox(t("catalog_stackable"), value=True, key="cat_sku_stackable")
        k_layers = c5.number_input(t("catalog_layers"), min_value=1, max_value=20, value=3,
                                   key="cat_sku_layers")
        if st.button(t("catalog_save_sku"), key="cat_sku_save"):
            if not str(k_name).strip():
                st.error(t("catalog_invalid", detail=t("catalog_sku_name")))
            else:
                new_id = _save_ext_sku({
                    "name": str(k_name).strip(), "weight_kg": float(k_weight),
                    "length_cm": float(k_l), "width_cm": float(k_w), "height_cm": float(k_h),
                    "fragile": bool(k_fragile), "stackable": bool(k_stackable),
                    "max_stack_layers": int(k_layers),
                })
                st.session_state["cat_notice"] = t("catalog_saved", id=new_id,
                                                   name=str(k_name).strip())
                st.rerun()


# ---------------------------------------------------------------- 多单独立场景（T50f）


def _normalize_items(raw_items: list) -> list:
    """把解析出的 items 规范化为订单 item 结构（供并入 / 独立场景共用）。"""
    out = []
    for it in raw_items or []:
        out.append({
            "sku": it.get("sku"),
            "name": it.get("name"),
            "qty": int(it.get("qty") or 1),
            "weight_kg": it.get("weight_kg"),
            "length_cm": it.get("length_cm"),
            "width_cm": it.get("width_cm"),
            "height_cm": it.get("height_cm"),
            "fragile": bool(it.get("fragile")),
            "stackable": bool(it.get("stackable", True)),
            "max_stack_layers": int(it.get("max_stack_layers") or 6),
        })
    return out


def _build_independent_scene(text: str, seed_key: str) -> dict:
    """T50f：多行订单（每行一单）→ 独立小场景（中心仓 + 涉及门店）→ 完整配载。

    门店坐标取自基础 / 扩展档案；路网用 Haversine 现场重建（口径同 data/gen_data.py）；
    vehicles / rules 沿用当前数据集配置；产物仅写 outputs/app-tmp/multi-scene/。
    """
    from agent.parser import parse_requirement  # 延迟导入

    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    if not lines:
        return {"error": "empty"}
    if len(lines) > 12:
        return {"error": "too_many", "count": len(lines)}

    data_dir = Path(DATASETS[seed_key]["dir"])
    base_orders = json.loads((data_dir / "orders.json").read_text(encoding="utf-8"))["orders"]
    base_network = json.loads((data_dir / "network.json").read_text(encoding="utf-8"))
    wh_node = next((nd for nd in base_network.get("nodes") or [] if nd["id"] == "WH01"), None)
    ext_stores = _load_ext_stores()

    parsed_lines, errors = [], []
    for idx, line in enumerate(lines, start=1):
        with _llm_env():
            r = parse_requirement(line)
        _enrich_items_with_ext(r)
        if r.get("error"):
            errors.append(t("multi_err_parse", idx=idx,
                            detail=str(r.get("detail") or r.get("error"))[:50]))
            continue
        store = _match_store(r, base_orders, ext_stores)
        if store is None:
            errors.append(t("multi_err_store", idx=idx,
                            name=str(r.get("store_name") or line[:20])))
            continue
        missing = [it.get("name") for it in (r.get("items") or [])
                   if not (it.get("weight_kg") and it.get("length_cm")
                           and it.get("width_cm") and it.get("height_cm"))]
        if missing:
            errors.append(t("multi_err_sku", idx=idx, name=str(missing[0])))
            continue
        parsed_lines.append((idx, r, store))

    if not parsed_lines:
        return {"error": "no_valid", "errors": errors}

    # ---- 组装订单（同门店多行合并为一单）----
    orders_by_sid: dict = {}
    for idx, r, store in parsed_lines:
        sid = store["store_id"]
        items = _normalize_items(r.get("items") or [])
        if sid not in orders_by_sid:
            orders_by_sid[sid] = {
                "order_id": f"NL-{len(orders_by_sid) + 1:02d}",
                "store_id": sid,
                "store_name": store.get("store_name"),
                "lat": float(store["lat"]), "lng": float(store["lng"]),
                "time_window": list(r.get("time_window")
                                    or store.get("time_window") or ["08:00", "18:00"]),
                "service_minutes": int(store.get("service_minutes")
                                       or r.get("service_minutes") or 15),
                "items": items,
            }
        else:
            entry = orders_by_sid[sid]
            entry["items"] += items
            if r.get("service_minutes"):
                entry["service_minutes"] += int(r["service_minutes"])
    orders_list = list(orders_by_sid.values())

    # ---- 现场重建路网（WH + 涉及门店；Haversine，口径同 gen_data.py）----
    nodes = [dict(wh_node)] if wh_node else [{"id": "WH01", "lat": 30.2741, "lng": 120.1551}]
    for o in orders_list:
        nodes.append({"id": o["store_id"], "lat": o["lat"], "lng": o["lng"]})
    network = _build_network(nodes)

    scene_dir = APP_TMP / "multi-scene" / seed_key
    dataset_dir = scene_dir / "data"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    (dataset_dir / "orders.json").write_text(
        json.dumps({"meta": {"scenario": "nl_multi"}, "orders": orders_list},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    (dataset_dir / "network.json").write_text(
        json.dumps(network, ensure_ascii=False, indent=2), encoding="utf-8")
    for name in ("vehicles.json", "rules.json"):
        (dataset_dir / name).write_text((data_dir / name).read_text(encoding="utf-8"),
                                        encoding="utf-8")

    out_dir = scene_dir / "out"
    result = pipeline.run_pipeline(str(dataset_dir), out_dir, baseline=False, comparison=False)

    route_of = {}
    for route in result["routes"].get("routes") or []:
        seq = [s for s in (route.get("sequence") or []) if s != "WH01"]
        stop_map = {d.get("node"): d for d in (route.get("stop_details") or [])}
        for o in orders_list:
            sid = o["store_id"]
            if sid in seq:
                route_of[sid] = {
                    "route_id": route.get("route_id"),
                    "vehicle_type": route.get("vehicle_type"),
                    "seq": seq.index(sid) + 1,
                    "total": len(seq),
                    "arrive": (stop_map.get(sid) or {}).get("arrive"),
                }
    figures = [Path(p) for p in result["figures"]]
    groups = _group_figures(figures, result["packing"])
    return {
        "error": None,
        "seed": seed_key,
        "errors": errors,
        "orders": orders_list,
        "route_of": route_of,
        "metrics": result["metrics"].get("metrics") or {},
        "route_map": str(groups["route_map"]) if groups["route_map"] else None,
        "packing_figs": [(inst, str(p)) for p, inst in groups["packing_figs"]],
        "step_figs": [(inst, shot, str(p)) for p, inst, shot in groups["step_figs"]],
        "out_dir": str(out_dir),
    }


def _render_multi_tab(seed_key: str) -> None:
    """T50f：多单独立场景模式的 UI（文本输入 → 构建 → 展示）。"""
    st.caption(t("multi_note"))
    if st.button(t("multi_fill"), key="multi_fill"):
        st.session_state["multi_text"] = "\n".join(MULTI_EXAMPLES)
    multi_text = st.text_area(t("multi_label"), key="multi_text", height=150,
                              placeholder=MULTI_EXAMPLES[0])
    if st.button(t("multi_run"), type="primary", key="multi_build"):
        with st.spinner(t("multi_spinner")):
            try:
                st.session_state["multi_result"] = _build_independent_scene(multi_text, seed_key)
            except Exception as exc:  # 管线异常：明确提示，不崩界面
                st.session_state["multi_result"] = {
                    "error": "pipeline", "detail": f"{type(exc).__name__}: {exc}"}
    mr = st.session_state.get("multi_result")
    if mr and mr.get("seed") not in (None, seed_key):
        mr = None  # 数据集已切换：旧场景结果不再适用
    if mr:
        _render_multi_result(mr)


def _render_multi_result(mr: dict) -> None:
    """展示独立场景结果：错误清单 + 方案指标 + 订单→车次分配 + 路线图。"""
    err = mr.get("error")
    if err == "empty":
        st.warning(t("multi_empty"))
        return
    if err == "too_many":
        st.warning(t("multi_too_many", n=mr.get("count")))
        return
    if err == "no_valid":
        st.warning(t("multi_no_valid"))
        for e in mr.get("errors") or []:
            st.caption(f"- {e}")
        return
    if err:
        st.error(t("merge_failed", detail=str(mr.get("detail") or err)))
        return

    if mr.get("errors"):
        st.warning(t("multi_errors"))
        for e in mr["errors"]:
            st.caption(f"- {e}")

    mm = mr["metrics"]
    st.success(t("multi_ok", n=len(mr["orders"]),
                 m=len({o["store_id"] for o in mr["orders"]}),
                 v=mm.get("vehicle_count", "—")))
    placed, total = mm.get("placed_item_count"), mm.get("total_item_count")
    cols = st.columns(4)
    cols[0].metric(t("kpi_vehicles"), f"{mm.get('vehicle_count', '—')}{t('unit_vehicles')}")
    cols[1].metric(t("kpi_distance"), f"{mm.get('total_distance_km', '—')} km")
    cols[2].metric(t("kpi_cost"), f"¥{mm.get('total_cost', '—')}")
    cols[3].metric(t("kpi_fill"),
                   f"{placed / total * 100:.1f}%" if total and placed is not None else "—")

    st.markdown(f"**{t('multi_assign_title')}**")
    rows = []
    for o in mr["orders"]:
        info = mr["route_of"].get(o["store_id"]) or {}
        rows.append({
            t("multi_col_order"): o["order_id"],
            t("multi_col_store"): o["store_name"],
            t("multi_col_route"): info.get("route_id", "—"),
            t("multi_col_seq"): f'{info.get("seq", "—")}/{info.get("total", "—")}',
            t("multi_col_arrive"): info.get("arrive") or "—",
        })
    st.dataframe(rows, width="stretch", hide_index=True)

    if mr.get("route_map"):
        st.markdown(f"**{t('merge_route_title')}**")
        st.image(mr["route_map"], width="stretch")

    # T50g：补展示车厢 3D 装载图 + 分步装载图（新样式）
    _render_packing_gallery(list(mr.get("packing_figs") or []))
    _render_step_gallery(list(mr.get("step_figs") or []))


# ---------------------------------------------------------------- 主界面


def main() -> None:
    st.set_page_config(page_title="CityLoad · Interactive Demo", layout="wide")
    _lang_selector()
    _llm_panel()

    st.title(t("app_title"))
    st.caption(t("app_caption"))
    st.caption(LANG_NOTICE)

    col_seed, col_cmp, col_btn = st.columns([2.2, 1.2, 1.0], vertical_alignment="bottom")
    with col_seed:
        # 选项值语言无关（seed42/seed7）+ 稳定 key：语言切换时保留所选数据集
        seed_key = st.selectbox(
            t("dataset_label"),
            options=list(DATASETS.keys()),
            help=t("dataset_help"),
            key="dataset_key",
        )
    with col_cmp:
        comparison = st.checkbox(t("comparison_label"), value=True, help=t("comparison_help"))
    with col_btn:
        run_clicked = st.button(t("run_button"), type="primary", width="stretch")

    if run_clicked:
        with st.spinner(t("spinner")):
            try:
                st.session_state["result"] = run_cached(seed_key, str(DATASETS[seed_key]["dir"]),
                                                        comparison)
                st.session_state["result_seed"] = seed_key
            except Exception as exc:  # 失败不崩界面，给出明确错误
                st.session_state["result"] = None
                st.error(f"{t('error_prefix')}{exc}")

    result = st.session_state.get("result")
    if result:
        _render_result(result)
    else:
        st.info(t("info_hint"))

    st.divider()
    with st.expander(t("catalog_title"), expanded=False):
        _render_catalog_section(seed_key)

    with st.expander(t("nl_title"), expanded=False):
        tab_single, tab_multi = st.tabs([t("nl_tab_single"), t("nl_tab_multi")])
        with tab_single:
            st.caption(t("nl_lang_note"))
            to_parse = None
            example_cols = st.columns(len(NL_EXAMPLES))
            for i, example in enumerate(NL_EXAMPLES):
                if example_cols[i].button(f'{t("nl_example")} {i + 1}', key=f"nl_example_{i}",
                                          help=example):
                    st.session_state["nl_text"] = example   # 填入文本框（供查看/编辑）
                    to_parse = example                       # 并直接解析
            text = st.text_area(t("nl_label"), key="nl_text", height=90,
                                placeholder=NL_EXAMPLES[0])
            if st.button(t("nl_parse_btn"), type="primary", key="nl_parse"):
                to_parse = text
            if to_parse is not None:
                st.session_state.pop("merged_result", None)  # 新解析：清除上一次合并结果
                _nl_parse_only(to_parse)
            parsed = st.session_state.get("nl_result")
            if parsed is not None:
                _render_nl_result(parsed, seed_key)
        with tab_multi:
            _render_multi_tab(seed_key)

    st.divider()
    st.caption(t("footer"))


if __name__ == "__main__":
    main()
