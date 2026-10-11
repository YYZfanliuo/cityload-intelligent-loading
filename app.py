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

import re
import sys
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


def execute(seed_key: str, data_dir: str, comparison: bool) -> dict:
    """跑完整管线并返回展示数据（与语言无关）；产物写入 outputs/app-tmp/{seed_key}/。"""
    out_dir = APP_TMP / seed_key / ("comparison" if comparison else "single")
    result = pipeline.run_pipeline(data_dir, out_dir, baseline=False, comparison=comparison)

    figures = [Path(p) for p in result["figures"]]
    instances = [v.get("vehicle_instance", "") for v in result["packing"].get("vehicles", [])]

    def _instance(path: Path) -> str:
        m = re.fullmatch(r"(?:packing|step)_V(\d+)(?:_\d+)?\.png", path.name)
        if m and 1 <= int(m.group(1)) <= len(instances):
            return instances[int(m.group(1)) - 1]
        return path.stem

    route_map = next((p for p in figures if p.name == "route_map.png"), None)
    packing_figs = [(p, _instance(p)) for p in figures if p.name.startswith("packing_")]
    step_figs = [(p, _instance(p), int(re.search(r"_(\d+)\.png$", p.name).group(1)))
                 for p in figures if p.name.startswith("step_")]
    return {
        "seed": seed_key,
        "metrics": result["metrics"].get("metrics", {}),
        "comparison": result["metrics"].get("comparison"),
        "route_map": str(route_map) if route_map else None,
        "packing_figs": [(inst, str(p)) for p, inst in packing_figs],
        "step_figs": [(inst, shot, str(p)) for p, inst, shot in step_figs],
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

    if r["packing_figs"]:
        st.subheader(t("packing_title"))
        cols = st.columns(2)
        for i, (instance, path) in enumerate(r["packing_figs"]):
            with cols[i % 2]:
                st.image(path, caption=t("packing_caption", instance=instance), width="stretch")

    if r["guides"]:
        with st.expander(t("guide_title", n=len(r["guides"]))):
            for g in r["guides"]:
                st.markdown(f"**{g.get('vehicle_instance', '')}**")
                st.text(str(g.get("text", "")).strip())

    if r["step_figs"]:
        with st.expander(t("step_title", n=len(r["step_figs"]))):
            cols = st.columns(3)
            for i, (instance, shot, path) in enumerate(r["step_figs"]):
                with cols[i % 3]:
                    st.image(path, caption=t("step_caption", instance=instance, shot=shot),
                             width="stretch")


# ---------------------------------------------------------------- 主界面


def main() -> None:
    st.set_page_config(page_title="CityLoad · Interactive Demo", layout="wide")
    _lang_selector()

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
            except Exception as exc:  # 失败不崩界面，给出明确错误
                st.session_state["result"] = None
                st.error(f"{t('error_prefix')}{exc}")

    result = st.session_state.get("result")
    if result:
        _render_result(result)
    else:
        st.info(t("info_hint"))

    st.divider()
    st.caption(t("footer"))


if __name__ == "__main__":
    main()
