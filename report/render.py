# -*- coding: utf-8 -*-
"""智载 CityLoad —— 图件渲染模块（T23 · 窗口E）

接口契约（docs/interfaces.md §2.8，签名冻结）::

    render_figures(routes, packing, loading_plan, network, outdir) -> list[str]

生成图件（PNG，dpi=100，宽度 10 inch）:

* ``route_map.png``      —— 中心仓 + 门店散点；各车次路线用不同颜色连线并标注路线编号
* ``packing_V{n}.png``   —— 第 n 辆车厢的 3D 装载图（``Axes3D`` + ``bar3d``，按门店着色，半透明箱体）
* ``step_V{n}_{i}.png``  —— 第 n 辆车的第 i 张分步装载示意（每车最多 5 张，按 loading_steps 累进）

约定:

* 坐标系（契约 §3.2）：原点在车厢**左下后角**；x 沿车长（向车头），y 沿车宽，z 垂直向上。
* 命名中 ``V{n}`` 的 n 为车辆在 ``packing["vehicles"]`` 中的出现序号（1-based），
  与 HTML 报告（``build_html``）中的车辆分组编号保持一致。
* 车厢尺寸优先读 packing 条目中的 ``cargo_length_cm`` / ``cargo_width_cm`` /
  ``cargo_height_cm``（若求解器提供），否则按车型查内置兜底表，最后按已装货物包围盒推断。
"""

from __future__ import annotations

import math
import os
from typing import Any

import matplotlib

matplotlib.use("Agg")  # 无界面环境（CLI/CI）渲染后端

import matplotlib.patheffects as path_effects
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

# 中文显示（Windows 常见字体 + 跨平台兜底）
plt.rcParams["font.sans-serif"] = [
    "Microsoft YaHei",
    "SimHei",
    "Noto Sans CJK SC",
    "WenQuanYi Micro Hei",
    "Arial Unicode MS",
]
plt.rcParams["axes.unicode_minus"] = False

#: 中心仓在路网中的约定编号（契约 §1.3，node_order 首位）
WAREHOUSE_ID = "WH01"

#: 车型车厢尺寸兜底表（与 data/vehicles.json 同步；packing 条目自带 cargo 字段时优先）
_DEFAULT_CARGO_DIMS: dict[str, tuple[float, float, float]] = {
    "V01": (420.0, 200.0, 200.0),  # 4.2 米厢式货车
    "V02": (340.0, 180.0, 180.0),  # 依维柯
}

#: 图件统一参数
_FIG_DPI = 100
_FIG_W = 10.0          # inch，宽度
_MAX_STEP_SHOTS = 5    # 每车分步图最多张数（契约 3–5 张）


# --------------------------------------------------------------------- 工具

def _num(value: Any, default: float = 0.0) -> float:
    """容错取数：转 float，失败返回 default。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt(value: Any, digits: int = 1, dash: str = "—") -> str:
    """容错格式化：None / 非法值显示 dash。"""
    if value is None:
        return dash
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return dash


def _pos_size(placed: dict) -> tuple[float, float, float, float, float, float]:
    """从 placed 条目提取 (x, y, z, l, w, h)，缺字段按 0 处理。"""
    pos = placed.get("position_cm") or {}
    size = placed.get("size_placed_cm") or {}
    return (
        _num(pos.get("x")), _num(pos.get("y")), _num(pos.get("z")),
        _num(size.get("l")), _num(size.get("w")), _num(size.get("h")),
    )


def _palette_colors() -> list:
    """tab20/tab20b/tab20c 三轮叠加（60 色），保证 30 门店不重色。"""
    colors: list = []
    for name in ("tab20", "tab20b", "tab20c"):
        cmap = matplotlib.colormaps[name]
        listed = getattr(cmap, "colors", None)
        if listed:
            colors.extend(listed)
        else:  # 理论上 tab* 均为 ListedColormap，此处仅兜底
            colors.extend(cmap(i / 19.0) for i in range(20))
    return colors


def _collect_store_ids(routes: dict, packing: dict) -> list[str]:
    """收集所有出现的门店编号（以 S 开头；排除仓库）。"""
    ids: set[str] = set()
    for route in routes.get("routes") or []:
        for node in route.get("sequence") or []:
            if isinstance(node, str) and node.startswith("S"):
                ids.add(node)
    for veh in packing.get("vehicles") or []:
        for p in veh.get("placed") or []:
            sid = p.get("store_id")
            if isinstance(sid, str) and sid.startswith("S"):
                ids.add(sid)
    return sorted(ids)


def _build_store_palette(store_ids: list[str]) -> dict[str, Any]:
    """门店 → 颜色映射（按编号排序固定分配，渲染结果可复现）。"""
    colors = _palette_colors()
    return {sid: colors[i % len(colors)] for i, sid in enumerate(store_ids)}


def _save(fig, path: str) -> str:
    """保存并关闭图，返回路径。"""
    fig.savefig(path, dpi=_FIG_DPI, bbox_inches="tight")
    plt.close(fig)
    return path


# --------------------------------------------------------------------- 路线图

def _render_route_map(routes: dict, network: dict, outdir: str, store_palette) -> str:
    """中心仓 + 门店散点；各车次路线彩色连线 + 编号标注。"""
    nodes: dict[str, tuple[float, float]] = {}
    for n in network.get("nodes") or []:
        try:
            nodes[str(n["id"])] = (_num(n["lng"]), _num(n["lat"]))
        except KeyError:
            continue

    route_list = routes.get("routes") or []
    served: set[str] = set()
    for route in route_list:
        served.update(s for s in route.get("sequence") or [] if s in nodes and s != WAREHOUSE_ID)
    store_nodes = {k: v for k, v in nodes.items() if k != WAREHOUSE_ID}
    unserved = [k for k in store_nodes if k not in served]

    fig, ax = plt.subplots(figsize=(_FIG_W, 8), dpi=_FIG_DPI)

    # 门店底点（浅灰）
    if store_nodes:
        ax.scatter(
            [v[0] for v in store_nodes.values()],
            [v[1] for v in store_nodes.values()],
            s=42, c="#c9ced6", edgecolors="#6b7280", linewidths=0.8, zorder=2,
            label=f"门店（共 {len(store_nodes)} 家）",
        )
    # 未分配门店
    if unserved:
        ax.scatter(
            [store_nodes[k][0] for k in unserved],
            [store_nodes[k][1] for k in unserved],
            s=70, c="#d62728", marker="x", linewidths=1.6, zorder=4,
            label=f"未分配（{len(unserved)} 家）",
        )

    # 各车次路线
    cmap = matplotlib.colormaps["tab10"]
    for i, route in enumerate(route_list):
        color = cmap(i % 10)
        seq = [s for s in route.get("sequence") or [] if s in nodes]
        if len(seq) < 2:
            continue
        xs = [nodes[s][0] for s in seq]
        ys = [nodes[s][1] for s in seq]
        n_stops = len(route.get("stop_details") or []) or max(len(seq) - 2, 0)
        label = (
            f"{route.get('route_id', '?')}｜{n_stops} 店｜"
            f"{_fmt(route.get('total_distance_km'), 1)} km"
        )
        ax.plot(xs, ys, "-", color=color, lw=1.9, alpha=0.9, zorder=3, label=label)
        if len(seq) > 2:
            ax.scatter(xs[1:-1], ys[1:-1], s=30, c=[color], zorder=3,
                       edgecolors="white", linewidths=0.8)
        # 路线编号标注（取路线中位门店，白描边防遮挡）
        mid = seq[len(seq) // 2]
        ax.annotate(
            str(route.get("route_id", "")), xy=nodes[mid], xytext=(8, 8),
            textcoords="offset points", fontsize=10, fontweight="bold", color=color,
            zorder=6, path_effects=[path_effects.withStroke(linewidth=2.8, foreground="white")],
        )

    # 中心仓
    wh = nodes.get(WAREHOUSE_ID)
    if wh:
        ax.scatter([wh[0]], [wh[1]], marker="*", s=320, c="#d62728",
                   edgecolors="#7f1d1d", linewidths=1.0, zorder=7, label="中心仓 WH01")

    if nodes:
        lat0 = sum(v[1] for v in nodes.values()) / len(nodes)
        ax.set_aspect(1.0 / max(math.cos(math.radians(lat0)), 0.2))

    ax.set_title(
        f"智载 CityLoad · 配送路线图（{len(served)} 家门店 / {len(route_list)} 车次）",
        fontsize=14,
    )
    ax.set_xlabel("经度")
    ax.set_ylabel("纬度")
    ax.grid(True, linestyle="--", alpha=0.3)
    handles, _labels = ax.get_legend_handles_labels()
    if handles:  # 图例置于坐标轴下方横排，避免遮挡地图节点；空数据时跳过
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=4,
                  fontsize=8, framealpha=0.92)

    return _save(fig, os.path.join(outdir, "route_map.png"))


# --------------------------------------------------------------------- 3D 装载

def _setup_3d_axes(ax, L: float, W: float, H: float) -> None:
    """统一 3D 坐标轴风格（按真实车厢比例）。"""
    ax.set_xlim(0, L)
    ax.set_ylim(0, W)
    ax.set_zlim(0, H)
    ax.set_xlabel("x · 车长 (cm) 车尾 → 车头", fontsize=9)
    ax.set_ylabel("y · 车宽 (cm)", fontsize=9)
    ax.set_zlabel("z · 高 (cm)", fontsize=9)
    ax.tick_params(labelsize=8)
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.set_pane_color((0.96, 0.97, 0.99, 1.0))
    try:
        ax.set_box_aspect((L, W, max(H, 1e-6)))
    except AttributeError:  # pragma: no cover - 老版本 matplotlib 兜底
        pass
    ax.view_init(elev=24, azim=-62)


def _draw_cargo_outline(ax, L: float, W: float, H: float) -> None:
    """车厢线框（12 条边）+ 半透明底面。"""
    floor = Poly3DCollection(
        [[(0, 0, -0.5), (L, 0, -0.5), (L, W, -0.5), (0, W, -0.5)]],
        facecolors="#e2e8f0", edgecolors="none", alpha=0.9,
    )
    ax.add_collection3d(floor)
    corners = [
        (0, 0, 0), (L, 0, 0), (L, W, 0), (0, W, 0),
        (0, 0, H), (L, 0, H), (L, W, H), (0, W, H),
    ]
    edges = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
             (0, 4), (1, 5), (2, 6), (3, 7)]
    for a, b in edges:
        ax.plot(
            [corners[a][0], corners[b][0]],
            [corners[a][1], corners[b][1]],
            [corners[a][2], corners[b][2]],
            color="#94a3b8", lw=0.9, alpha=0.9,
        )


def _draw_placed(ax, placed: list[dict], palette) -> list:
    """按门店分组、数组化 bar3d 绘制所有货物；返回图例 handles。"""
    groups: dict[str, list[dict]] = {}
    for p in placed:
        key = p.get("store_id") or "?"
        groups.setdefault(key, []).append(p)
    handles = []
    for sid in sorted(groups):
        entries = [_pos_size(p) for p in groups[sid]]
        entries = [e for e in entries if e[3] > 0 and e[4] > 0 and e[5] > 0]
        if not entries:
            continue
        xs, ys, zs, dxs, dys, dzs = map(list, zip(*entries))
        color = palette.get(sid, "#8c8c8c")
        ax.bar3d(xs, ys, zs, dxs, dys, dzs, color=color, alpha=0.55,
                 edgecolor="#334155", linewidth=0.25, shade=True)
        handles.append(Patch(facecolor=color, edgecolor="#334155",
                             label=f"{sid}（{len(entries)} 件）"))
    return handles


def _cargo_dims_for(veh: dict) -> tuple[float, float, float]:
    """获取车厢内部尺寸：packing 字段 → 车型兜底表 → 已装货物包围盒推断。"""
    L, W, H = veh.get("cargo_length_cm"), veh.get("cargo_width_cm"), veh.get("cargo_height_cm")
    if L and W and H:
        return float(L), float(W), float(H)
    dims = _DEFAULT_CARGO_DIMS.get(str(veh.get("vehicle_type")))
    if dims:
        return dims
    entries = [_pos_size(p) for p in veh.get("placed") or []]
    entries = [e for e in entries if e[3] > 0 and e[4] > 0 and e[5] > 0]
    if entries:
        return (
            max(e[0] + e[3] for e in entries) * 1.06,
            max(e[1] + e[4] for e in entries) * 1.06,
            max(e[2] + e[5] for e in entries) * 1.06,
        )
    return _DEFAULT_CARGO_DIMS["V01"]


def _packing_stats_lines(veh: dict) -> list[str]:
    """3D 图左上角统计行：体积/载重利用率；货物未全部装载时追加“未装载 N 件”警示行。

    契约 v1.3：packing.unplaced 非空时须如实披露；为空时自动隐藏警示行。
    """
    lines: list[str] = []
    main: list[str] = []
    vol = veh.get("volume_utilization")
    wt = veh.get("weight_utilization")
    if isinstance(vol, (int, float)):
        main.append(f"体积利用率 {vol * 100:.0f}%")
    if isinstance(wt, (int, float)):
        main.append(f"载重利用率 {wt * 100:.0f}%")
    if main:
        lines.append(" · ".join(main))
    unplaced_count = len(veh.get("unplaced") or [])
    if unplaced_count:
        lines.append(f"未装载 {unplaced_count} 件")
    return lines


def _render_packing(veh: dict, cargo, palette, outdir: str, idx: int) -> str:
    """单车厢 3D 装载总图。"""
    L, W, H = cargo
    placed = list(veh.get("placed") or [])
    fig = plt.figure(figsize=(_FIG_W, 7), dpi=_FIG_DPI)
    ax = fig.add_subplot(111, projection="3d")
    _setup_3d_axes(ax, L, W, H)
    _draw_cargo_outline(ax, L, W, H)
    handles = _draw_placed(ax, placed, palette)

    instance = veh.get("vehicle_instance") or f"V{idx:02d}"
    ax.set_title(
        f"{instance} 车厢 3D 装载图（车型 {veh.get('vehicle_type', '—')}）· 已装 {len(placed)} 件",
        fontsize=13,
    )
    stats_lines = _packing_stats_lines(veh)
    if stats_lines:
        ax.text2D(0.02, 0.95, stats_lines[0], transform=ax.transAxes,
                  fontsize=10, color="#334155")
    if len(stats_lines) > 1:  # unplaced>0 时的红色警示行（=0 自动隐藏）
        ax.text2D(0.02, 0.90, stats_lines[1], transform=ax.transAxes,
                  fontsize=10, color="#b91c1c", fontweight="bold")
    if handles:
        ax.legend(handles=handles, loc="upper right", fontsize=7.5, framealpha=0.92,
                  title="门店", title_fontsize=8)

    return _save(fig, os.path.join(outdir, f"packing_V{idx:02d}.png"))


# --------------------------------------------------------------------- 分步装载

def _select_step_indices(total: int, max_shots: int = _MAX_STEP_SHOTS) -> list[int]:
    """在 total 个装载步骤中均匀抽取不超过 max_shots 张的步骤下标（含最后一步）。"""
    if total <= max_shots:
        return list(range(total))
    return sorted({round(i * (total - 1) / (max_shots - 1)) for i in range(max_shots)})


def _render_steps(veh: dict, plan: dict, cargo, palette, outdir: str, idx: int) -> list[str]:
    """单车分步装载图：按 loading_steps 累进显示已装货物。"""
    L, W, H = cargo
    placed = list(veh.get("placed") or [])
    steps = list(plan.get("loading_steps") or [])
    if not steps or not placed:
        return []

    # 累计每个 step 后的 (store_id, sku) → 已装入配额
    counter: dict[tuple, int] = {}
    cumulative: list[dict] = []
    for st in steps:
        for it in st.get("items") or []:
            key = (st.get("store_id"), it.get("sku"))
            counter[key] = counter.get(key, 0) + int(_num(it.get("qty")))
        cumulative.append(dict(counter))

    outputs = []
    for shot, si in enumerate(_select_step_indices(len(steps)), start=1):
        quota = cumulative[si]
        used: dict[tuple, int] = {}
        shown: list[dict] = []
        for p in placed:
            key = (p.get("store_id"), p.get("sku"))
            used[key] = used.get(key, 0) + 1
            if used[key] <= quota.get(key, 0):
                shown.append(p)

        st = steps[si]
        fig = plt.figure(figsize=(_FIG_W, 7), dpi=_FIG_DPI)
        ax = fig.add_subplot(111, projection="3d")
        _setup_3d_axes(ax, L, W, H)
        _draw_cargo_outline(ax, L, W, H)
        _draw_placed(ax, shown, palette)

        step_no = st.get("step", si + 1)
        ax.set_title(
            f"{veh.get('vehicle_instance', '')} 分步装载 · 第 {step_no}/{len(steps)} 步："
            f"装入 {st.get('store_id', '?')}",
            fontsize=12.5,
        )
        line2 = f"已装 {len(shown)} 件"
        note = st.get("note")
        if note:
            line2 += f" · {note}"
        ax.text2D(0.02, 0.94, line2, transform=ax.transAxes, fontsize=9.5, color="#475569")

        outputs.append(_save(fig, os.path.join(outdir, f"step_V{idx:02d}_{shot}.png")))
    return outputs


# --------------------------------------------------------------------- 主函数

def render_figures(routes: dict, packing: dict, loading_plan: dict, network: dict, outdir: str) -> list[str]:
    """按契约 §2.8 渲染全部图件，返回生成的文件路径列表（绝对路径）。

    参数:
        routes: routes.json 结构
        packing: packing.json 结构
        loading_plan: loading_plan.json 结构
        network: network.json 结构
        outdir: 图片输出目录（不存在则创建）
    """
    outdir = os.path.abspath(outdir)
    os.makedirs(outdir, exist_ok=True)

    routes = routes or {}
    packing = packing or {}
    loading_plan = loading_plan or {}
    network = network or {}

    store_palette = _build_store_palette(_collect_store_ids(routes, packing))
    outputs: list[str] = []

    outputs.append(_render_route_map(routes, network, outdir, store_palette))

    plans_by_instance = {
        p.get("vehicle_instance"): p for p in loading_plan.get("plans") or []
        if p.get("vehicle_instance")
    }
    for idx, veh in enumerate(packing.get("vehicles") or [], start=1):
        cargo = _cargo_dims_for(veh)
        outputs.append(_render_packing(veh, cargo, store_palette, outdir, idx))
        plan = plans_by_instance.get(veh.get("vehicle_instance"))
        if plan:
            outputs.extend(_render_steps(veh, plan, cargo, store_palette, outdir, idx))

    return outputs
