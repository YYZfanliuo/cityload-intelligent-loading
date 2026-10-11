# -*- coding: utf-8 -*-
"""智载 CityLoad —— 交互式演示界面（T50 · 窗口E）

启动（一条命令）::

    pip install -r requirements.txt -r requirements-app.txt
    streamlit run app.py

功能：选择数据集（seed42 / seed7）→ 点击「运行智能配载」→
指标卡 + 路线图 + 各车厢 3D 装载图（可选：贪心基线对比表 + 分步装卸指引）。

硬约束（派发单 T50）:

* **零侵入** —— 仅 import 复用现有模块（run.py / solver / report / agent / eval），不修改任何现有代码；
* **产物隔离** —— 全部运行产物写入 ``outputs/app-tmp/{seed}/``，绝不覆盖
  ``outputs/t25-final/`` 与 ``outputs/t25-baseline/``；
* 核心链路逻辑位于 ``execute()``（无 Streamlit 依赖，可无头自测）。
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
    "seed42（主数据集 · 30 门店）": ROOT / "data",
    "seed7（第二组验证 · 30 门店）": ROOT / "data" / "seed7",
}
APP_TMP = ROOT / "outputs" / "app-tmp"


# ---------------------------------------------------------------- 核心逻辑（无 UI 依赖）


def execute(seed_key: str, data_dir: str, comparison: bool) -> dict:
    """跑完整管线并返回展示所需数据；产物写入 outputs/app-tmp/{seed_key}/。"""
    out_dir = APP_TMP / seed_key / ("comparison" if comparison else "single")
    result = pipeline.run_pipeline(data_dir, out_dir, baseline=False, comparison=comparison)

    figures = [Path(p) for p in result["figures"]]
    instances = [v.get("vehicle_instance", "") for v in result["packing"].get("vehicles", [])]

    def _label(path: Path) -> str:
        m = re.fullmatch(r"packing_V(\d+)\.png", path.name)
        if m and 1 <= int(m.group(1)) <= len(instances):
            return instances[int(m.group(1)) - 1]
        return path.stem

    route_map = next((p for p in figures if p.name == "route_map.png"), None)
    return {
        "seed": seed_key,
        "metrics": result["metrics"].get("metrics", {}),
        "comparison": result["metrics"].get("comparison"),
        "route_map": str(route_map) if route_map else None,
        "packing_figs": [(f"{_label(p)} 车厢 3D 装载图", str(p))
                         for p in figures if p.name.startswith("packing_")],
        "step_figs": [(f"{_label(p)} · 分步示意 {p.stem.rsplit('_', 1)[-1]}", str(p))
                      for p in figures if p.name.startswith("step_")],
        "guides": result["guide_text"].get("guides", []),
        "out_dir": str(out_dir),
        "figure_count": len(figures),
    }


@st.cache_data(show_spinner=False)
def run_cached(seed_key: str, data_dir: str, comparison: bool) -> dict:
    """带缓存的执行入口（同一参数第二次点击秒开）。"""
    return execute(seed_key, data_dir, comparison)


# ---------------------------------------------------------------- UI 组件


def _kpi_row(m: dict) -> None:
    placed, total = m.get("placed_item_count"), m.get("total_item_count")
    rate = f"{placed / total * 100:.1f}%" if total and placed is not None else "—"
    cols = st.columns(5)
    cols[0].metric("车辆数", f"{m.get('vehicle_count', '—')} 辆")
    cols[1].metric("总里程", f"{m.get('total_distance_km', '—')} km")
    cols[2].metric("运输费", f"¥{m.get('total_cost', '—')}")
    cols[3].metric("货物装下率", rate)
    cols[4].metric("时效达成率", f"{(m.get('on_time_rate') or 0) * 100:.1f}%")


def _comparison_rows(comp: dict) -> list[dict]:
    b, o = comp.get("baseline", {}), comp.get("optimized", {})

    def pct(v: float) -> str:
        return f"{v * 100:.1f}%"

    def plain(v: float) -> str:
        return f"{v:.0f}"

    def km(v: float) -> str:
        return f"{v:.1f}"

    def money(v: float) -> str:
        return f"¥{v:.2f}"

    rows = []

    def add(name: str, key: str, fmt, lower_better: bool) -> None:
        bv, ov = b.get(key), o.get(key)
        if bv is None or ov is None:
            return
        delta = (ov - bv) / abs(bv) if bv else None
        arrow = "↓" if ov < bv else ("↑" if ov > bv else "＝")
        rows.append({
            "指标": name,
            "基准（贪心）": fmt(bv),
            "优化方案": fmt(ov),
            "变化": f"{arrow} {delta * 100:+.1f}%" if delta is not None else "—",
        })

    add("车辆数（辆）", "vehicle_count", plain, True)
    add("总里程（km）", "total_distance_km", km, True)
    add("运输费（元）", "total_cost", money, True)
    add("平均载重装载率", "avg_weight_load_rate", pct, False)
    add("时效达成率", "on_time_rate", pct, False)
    add("未分配订单数", "unassigned_order_count", plain, True)

    tb, pb = b.get("total_item_count"), b.get("placed_item_count")
    to, po = o.get("total_item_count"), o.get("placed_item_count")
    if all(v is not None for v in (tb, pb, to, po)) and tb and to:
        rows.append({
            "指标": "货物装下率",
            "基准（贪心）": f"{pb / tb * 100:.1f}%（{pb}/{tb}）",
            "优化方案": f"{po / to * 100:.1f}%（{po}/{to}）",
            "变化": f"↑ {(po / to - pb / tb) * 100:+.1f}pp",
        })
    return rows


def _render_result(r: dict) -> None:
    m = r["metrics"]
    st.caption(f"数据集：{r['seed']} ｜ 图件 {r['figure_count']} 张 ｜ 产物目录：{r['out_dir']}")
    _kpi_row(m)

    if m.get("unplaced_item_count"):
        st.warning(f"有 {m['unplaced_item_count']} 件货物因装载约束未能装入车厢"
                   f"（已装 {m.get('placed_item_count')}/{m.get('total_item_count')} 件，详见产物报告）。")
    if m.get("unassigned_order_count"):
        st.warning(f"有 {m['unassigned_order_count']} 个订单未分配。")

    if r.get("comparison"):
        st.subheader("优化 vs 基准对比")
        st.dataframe(_comparison_rows(r["comparison"]), width="stretch", hide_index=True)

    if r["route_map"]:
        st.subheader("配送路线图")
        st.image(r["route_map"], width="stretch")

    if r["packing_figs"]:
        st.subheader("各车厢 3D 装载图")
        cols = st.columns(2)
        for i, (caption, path) in enumerate(r["packing_figs"]):
            with cols[i % 2]:
                st.image(path, caption=caption, width="stretch")

    if r["guides"]:
        with st.expander(f"分步装卸指引（{len(r['guides'])} 辆车，文字版）"):
            for g in r["guides"]:
                st.markdown(f"**{g.get('vehicle_instance', '')}**")
                st.text(str(g.get("text", "")).strip())

    if r["step_figs"]:
        with st.expander(f"分步装载示意（{len(r['step_figs'])} 张，每车最多 5 张）"):
            cols = st.columns(3)
            for i, (caption, path) in enumerate(r["step_figs"]):
                with cols[i % 3]:
                    st.image(path, caption=caption, width="stretch")


# ---------------------------------------------------------------- 主界面


def main() -> None:
    st.set_page_config(page_title="智载 CityLoad · 交互演示", layout="wide")
    st.title("智载 CityLoad —— 城市配送智能配载交互演示")
    st.caption("中心仓 → 30 家连锁药店 ｜ CVRPTW 路径规划 + 3D 装箱 + 分步装卸指引（T31b 可复现口径）")

    col_seed, col_cmp, col_btn = st.columns([2.2, 1.2, 1.0], vertical_alignment="bottom")
    with col_seed:
        seed_label = st.selectbox("选择数据集", list(DATASETS.keys()))
    with col_cmp:
        comparison = st.checkbox("对比贪心基线", value=True,
                                 help="勾选后同时求解贪心基线并展示真实对比表（约多耗时几秒）")
    with col_btn:
        run_clicked = st.button("运行智能配载", type="primary", width="stretch")

    if run_clicked:
        seed_key = "seed42" if seed_label.startswith("seed42") else "seed7"
        with st.spinner("正在求解与渲染图件（约 10～30 秒）..."):
            try:
                st.session_state["result"] = run_cached(seed_key, str(DATASETS[seed_label]), comparison)
            except Exception as exc:  # 失败不崩界面，给出明确错误
                st.session_state["result"] = None
                st.error(f"运行失败：{exc}")

    result = st.session_state.get("result")
    if result:
        _render_result(result)
    else:
        st.info("请选择数据集后点击「运行智能配载」。首次运行约 10～30 秒，之后同参数将复用缓存。")

    st.divider()
    st.caption("运行产物默认写入 outputs/app-tmp/（隔离目录），不覆盖已入库的 t25-final / t25-baseline。"
               "命令行等效入口：python run.py --data data/ --out outputs/t25-final --comparison")


if __name__ == "__main__":
    main()
