# -*- coding: utf-8 -*-
"""智载 CityLoad —— 单页 HTML 报告生成器（T24 · 窗口E）

接口契约（docs/interfaces.md §2.9，签名冻结）::

    build_html(routes, metrics, guide_text, figure_paths, outpath) -> str

模板：``report/templates/report.html``（Python 标准库 ``string.Template`` 填充，
不引入 jinja2 等未声明依赖）。图片以**相对 outpath 的路径**引用；全页内联 CSS、
无外部 CDN、无脚本，可离线直接打开。

可选扩展（契约 v1.2 §3.4 正式约定，不改变函数签名）:

* ``metrics["comparison"]`` 为 dict 时，渲染“优化 vs 基准”对比表格，否则整节自动跳过。
  结构（由窗口C 的 run.py 负责组装）::

      "comparison": {
          "baseline":  {"vehicle_count": 6, "total_distance_km": 324.1, ...},
          "optimized": {"vehicle_count": 5, "total_distance_km": 269.6, ...}
      }

  指标键与 metrics.json §3.4 的 ``metrics`` 子对象一致；``optimized`` 缺省时回退为
  ``metrics["metrics"]`` 本身；``metrics["baseline"]`` 作为兼容旧键同样接受。

车辆分组约定：``figure_paths`` 中 ``packing_V{n}.png`` / ``step_V{n}_{i}.png`` 的 n 为
车辆序号（1-based，与 ``render_figures`` 一致）；第 n 辆车的指引文本取
``guide_text["guides"][n-1]``（run.py 应保持与 packing 相同的车辆顺序）。
"""

from __future__ import annotations

import html as _html
import os
import re
import time
from pathlib import Path
from string import Template
from typing import Any

_TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "report.html"

#: 对比表行配置：(指标键, 中文名, 数值格式, 是否“越低越优”)
_COMPARISON_ROWS = [
    ("vehicle_count", "车辆数（辆）", "{:.0f}", True),
    ("total_distance_km", "总里程（km）", "{:.1f}", True),
    ("total_cost", "总成本（元）", "¥{:.2f}", True),
    ("avg_weight_load_rate", "平均载重装载率", "{:.1%}", False),
    ("avg_volume_utilization", "平均体积利用率", "{:.1%}", False),
    ("on_time_rate", "时效达成率", "{:.1%}", False),
    ("unassigned_order_count", "未分配订单数", "{:.0f}", True),
]


# --------------------------------------------------------------------- 基础工具

def _esc(text: Any) -> str:
    """HTML 转义。"""
    return _html.escape(str(text))


def _fmt(value: Any, fmt: str = "{:.1f}", dash: str = "—") -> str:
    """容错格式化数值。"""
    if value is None:
        return dash
    try:
        return fmt.format(float(value))
    except (TypeError, ValueError):
        return dash


def _pct(value: Any) -> str:
    """0.4231 → '42.3%'。"""
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _url(path: str, out_dir: str) -> str:
    """图片引用 URL：优先相对 out_dir 的路径；跨盘等场景退化为 file:// URI。"""
    try:
        rel = os.path.relpath(path, out_dir)
        url = rel.replace(os.sep, "/")
    except ValueError:
        return Path(path).as_uri()
    if not url.isascii():  # 相对路径含中文时做百分号编码，兼容所有浏览器
        from urllib.parse import quote
        url = quote(url)
    return url


# --------------------------------------------------------------------- 图件分组

def _group_figures(figure_paths: list[str]) -> dict:
    """把 render_figures 的输出按类型归组：route_map / packing{n} / steps{n}。"""
    grouped: dict = {"route_map": None, "packing": {}, "steps": {}}
    for path in figure_paths or []:
        path = str(path)
        name = os.path.basename(path)
        m = re.fullmatch(r"packing_V(\d+)\.png", name, re.IGNORECASE)
        if m:
            grouped["packing"][int(m.group(1))] = path
            continue
        m = re.fullmatch(r"step_V(\d+)_(\d+)\.png", name, re.IGNORECASE)
        if m:
            grouped["steps"].setdefault(int(m.group(1)), []).append((int(m.group(2)), path))
            continue
        if name.lower() == "route_map.png":
            grouped["route_map"] = path
    for key in grouped["steps"]:
        grouped["steps"][key].sort(key=lambda item: item[0])
    return grouped


def _build_vehicle_groups(route_list: list[dict], guides: list[dict],
                          figures: dict, per_route: list[dict]) -> list[dict]:
    """组装每辆车的展示分组：3D 图 + 分步图 + 指引文本 + 单路线指标。"""
    n = max(
        [0]
        + list(figures["packing"].keys())
        + list(figures["steps"].keys())
        + [len(guides), len(route_list)]
    )
    per_route_by_id = {r.get("route_id"): r for r in per_route if isinstance(r, dict)}
    groups = []
    for i in range(1, n + 1):
        route = route_list[i - 1] if i - 1 < len(route_list) else None
        guide = guides[i - 1] if i - 1 < len(guides) else None
        instance = (guide or {}).get("vehicle_instance") or \
            ((route or {}).get("route_id", "") + "-1" if route else f"车辆{i}")
        route_id = (route or {}).get("route_id")
        detail = per_route_by_id.get(route_id) or route or {}
        vmeta_bits = []
        if detail.get("stop_count") is not None:
            vmeta_bits.append(f'{detail["stop_count"]} 个门店')
        if detail.get("distance_km") is not None:
            vmeta_bits.append(f'里程 {_fmt(detail.get("distance_km"), "{:.1f}")} km')
        if detail.get("cost") is not None:
            vmeta_bits.append(f'成本 ¥{_fmt(detail.get("cost"), "{:.2f}")}')
        if detail.get("load_rate_weight") is not None:
            vmeta_bits.append(f'载重装载率 {_pct(detail.get("load_rate_weight"))}')
        groups.append({
            "idx": i,
            "instance": instance,
            "vehicle_type": (route or {}).get("vehicle_type"),
            "vmeta": " · ".join(vmeta_bits),
            "guide_text": (guide or {}).get("text"),
            "packing_img": figures["packing"].get(i),
            "step_imgs": figures["steps"].get(i) or [],
        })
    return groups


# --------------------------------------------------------------------- 页面片段

def _meta_line(metrics: dict, figure_paths: list[str]) -> str:
    """头部副标题行。"""
    bits = ["场景：城市连锁药店配送（中心仓 → 门店）"]
    if metrics.get("seed") is not None:
        bits.append(f"seed={metrics['seed']}")
    bits.append(f"{len(figure_paths or [])} 张图件")
    bits.append(f"生成时间 {time.strftime('%Y-%m-%d %H:%M')}")
    return _esc(" ｜ ".join(bits))


def _scene_text(routes: dict, metrics: dict) -> str:
    """场景说明段落。"""
    m = metrics.get("metrics") or {}
    per_route = metrics.get("per_route") or []
    route_list = routes.get("routes") or []
    stops = sum(int(r.get("stop_count") or 0) for r in per_route if isinstance(r, dict))
    if not stops:
        stops = sum(len(r.get("stop_details") or []) for r in route_list)
    stops_text = str(stops) if stops else "若干"
    veh_text = _fmt(m.get("vehicle_count"), "{:.0f}", "若干")
    seed_text = metrics.get("seed", "—")
    n_routes = len(route_list)
    return (
        f"<p>场景：<b>单一中心仓 → {stops_text} 家连锁药店</b>的城市配送。"
        f"订单由固定种子（seed={seed_text}）的模拟数据生成器产出，全链路可复现。</p>"
        f"<p>求解流程：多约束路径规划（CVRPTW：载重 + 时间窗）→ 3D 装箱"
        f"（重不压轻、易碎置顶、堆叠层数约束）→ 按配送顺序倒序装载（后送先装）"
        f"→ 分步装卸指引与可视化报告。</p>"
        f"<p>本页方案：<b>{n_routes} 条配送路线 / {veh_text} 辆车次</b>。"
        f"装载结果见下方各车厢 3D 图与分步指引。</p>"
    )


def _perf_line(m: dict) -> str:
    """性能与附加指标小字行。"""
    bits = []
    if m.get("solve_time_sec") is not None:
        bits.append(f"求解耗时 {_fmt(m.get('solve_time_sec'), '{:.2f}')} s")
    if m.get("peak_memory_mb"):
        bits.append(f"峰值内存 {_fmt(m.get('peak_memory_mb'), '{:.1f}')} MB")
    if m.get("avg_volume_utilization") is not None:
        bits.append(f"平均体积利用率 {_pct(m.get('avg_volume_utilization'))}")
    if m.get("unassigned_order_count"):
        bits.append(f"未分配订单 {_fmt(m.get('unassigned_order_count'), '{:.0f}')} 个")
    if not bits:
        return ""
    return '<p class="perf">' + _esc(" · ".join(bits)) + "</p>"


def _kpi(value: str, label: str) -> str:
    return f'<div class="kpi"><div class="v">{value}</div><div class="l">{_esc(label)}</div></div>'


def _cards_html(m: dict) -> str:
    """五张核心指标卡片。"""
    cards = [
        _kpi(_fmt(m.get("vehicle_count"), "{:.0f}") + "<small> 辆</small>", "车辆数（车次）"),
        _kpi(_fmt(m.get("total_distance_km"), "{:.1f}") + "<small> km</small>", "总里程"),
        _kpi("¥" + _fmt(m.get("total_cost"), "{:.2f}"), "总成本"),
        _kpi(_pct(m.get("avg_weight_load_rate")), "平均载重装载率"),
        _kpi(_pct(m.get("on_time_rate")), "时效达成率"),
    ]
    return '<div class="kpis">' + "".join(cards) + "</div>"


def _warn_html(routes: dict) -> str:
    unassigned = routes.get("unassigned_orders") or []
    if not unassigned:
        return ""
    shown = "、".join(_esc(x) for x in unassigned[:10])
    more = f" 等 {len(unassigned)} 单" if len(unassigned) > 10 else ""
    return (
        f'<div class="warn">注意：有 {len(unassigned)} 个订单未分配（{shown}{more}），'
        f"详见 routes.json 的 unassigned_orders。</div>"
    )


def _routemap_html(path: str | None, out_dir: str) -> str:
    if not path:
        return '<div class="placeholder">路线图未生成</div>'
    url = _url(path, out_dir)
    return (
        f'<figure><img src="{_esc(url)}" alt="配送路线图">'
        f"<figcaption>配送路线图：不同颜色为不同车次路线，★ 为中心仓，"
        f"× 为未分配门店。</figcaption></figure>"
    )


def _packing_html(groups: list[dict], out_dir: str) -> str:
    blocks = []
    for g in groups:
        if not g["packing_img"]:
            continue
        url = _url(g["packing_img"], out_dir)
        blocks.append(
            f'<figure><img src="{_esc(url)}" alt="{_esc(g["instance"])} 3D 装载图" loading="lazy">'
            f"<figcaption>{_esc(g['instance'])} 车厢 3D 装载图</figcaption></figure>"
        )
    if not blocks:
        return '<div class="placeholder">3D 装载图未生成（等待 packing.json 与 render_figures 产物）</div>'
    return "".join(blocks)


def _guide_html(groups: list[dict], out_dir: str) -> str:
    blocks = []
    for g in groups:
        if not (g["guide_text"] or g["step_imgs"]):
            continue
        title = f'车辆 {g["idx"]} · {_esc(g["instance"])}'
        if g.get("vehicle_type"):
            title += f'<span class="tag">车型 {_esc(g["vehicle_type"])}</span>'
        parts = [f'<article class="vehicle"><h3>{title}</h3>']
        if g["vmeta"]:
            parts.append(f'<p class="vmeta">{_esc(g["vmeta"])}</p>')
        if g["guide_text"]:
            paras = [
                "<p>" + _esc(p).replace("\n", "<br>") + "</p>"
                for p in re.split(r"\n\s*\n", str(g["guide_text"]).strip()) if p.strip()
            ]
            parts.append('<div class="guide-text">' + "".join(paras) + "</div>")
        if g["step_imgs"]:
            figs = []
            for order, path in g["step_imgs"]:
                url = _url(path, out_dir)
                figs.append(
                    f'<figure><img src="{_esc(url)}" alt="分步装载示意（第 {order} 张）" loading="lazy">'
                    f"<figcaption>分步示意 {order}</figcaption></figure>"
                )
            parts.append('<div class="steps-grid">' + "".join(figs) + "</div>")
        parts.append("</article>")
        blocks.append("".join(parts))
    if not blocks:
        return '<div class="placeholder">分步装卸指引未生成（等待 guide_text.json 与 step_V*.png）</div>'
    return "".join(blocks)


def _comparison_html(metrics: dict) -> str:
    """可选第 6 节：优化 vs 基准对比表（metrics["comparison"] 存在时渲染）。"""
    comp = metrics.get("comparison") or metrics.get("baseline") or None
    if not isinstance(comp, dict):
        return ""
    baseline = comp.get("baseline")
    optimized = comp.get("optimized") or (metrics.get("metrics") or {})
    if not isinstance(baseline, dict) or not isinstance(optimized, dict):
        return ""

    rows = []
    for key, label, fmt, lower_better in _COMPARISON_ROWS:
        base_v = baseline.get(key)
        opt_v = optimized.get(key)
        if base_v is None and opt_v is None:
            continue
        change = "—"
        if isinstance(base_v, (int, float)) and isinstance(opt_v, (int, float)) and base_v != 0:
            delta = (opt_v - base_v) / abs(base_v)
            if opt_v == base_v:
                change = '<span class="better">＝ 持平</span>'
            else:
                good = (opt_v < base_v) if lower_better else (opt_v > base_v)
                cls = "better" if good else "worse"
                arrow = "↓" if opt_v < base_v else "↑"
                sign = "+" if delta > 0 else ""
                change = f'<span class="{cls}">{arrow} {sign}{delta * 100:.1f}%</span>'
        rows.append(
            f"<tr><td>{_esc(label)}</td>"
            f'<td class="num">{_fmt(base_v, fmt)}</td>'
            f'<td class="num">{_fmt(opt_v, fmt)}</td>'
            f'<td class="num">{change}</td></tr>'
        )
    if not rows:
        return ""
    thead = (
        "<thead><tr><th>指标</th><th class=\"num\">基准（贪心）</th>"
        "<th class=\"num\">优化方案</th><th class=\"num\">变化</th></tr></thead>"
    )
    note = ("注：变化 =（优化 − 基准）/ 基准；里程、成本、车辆数、未分配数为“降优”，"
            "装载率、时效为“升优”。数字来源：experiments 对比实验与 output/metrics.json。")
    return (
        '<section class="card"><h2>6 · 优化 vs 基准对比</h2>'
        f"<table>{thead}<tbody>{''.join(rows)}</tbody></table>"
        f'<p class="perf">{_esc(note)}</p></section>'
    )


def _notes_html(metrics: dict, routes: dict) -> str:
    m = metrics.get("metrics") or {}
    seed = metrics.get("seed", "—")
    items = [
        f"数据来源：模拟数据集（seed={seed}，单一中心仓 + 连锁药店场景），由 "
        f"data/gen_data.py 生成、data/validate.py 校验，字节级可复现；不含任何真实企业数据。",
        "求解与评估：路径规划为 OR-Tools CVRPTW；装箱为 3D 启发式（重不压轻、易碎置顶）；"
        "全部指标由 eval/metrics.py 计算，与本报告数字同源。",
        "图件生成：matplotlib 静态渲染（route_map / packing_V* / step_V*）；"
        "本页为离线单页 HTML（内联 CSS、无外部 CDN、无脚本）。",
    ]
    perf = []
    if m.get("solve_time_sec") is not None:
        perf.append(f"求解耗时 {_fmt(m.get('solve_time_sec'), '{:.2f}')} 秒")
    if m.get("peak_memory_mb"):
        perf.append(f"峰值内存 {_fmt(m.get('peak_memory_mb'), '{:.1f}')} MB")
    unassigned = routes.get("unassigned_orders") or []
    if unassigned:
        perf.append(f"未分配订单 {len(unassigned)} 个（含罚金，见 routes.json）")
    if perf:
        items.append("求解性能（本机实测）：" + "、".join(perf) + "。")

    limits = [
        "本报告为算法输出的静态快照，非实时调度系统；未接入实时路况与突发订单。",
        "时间窗遵守基于平均车速估算，未模拟随机延误；实际执行请预留缓冲。",
        "3D 装载图为自动装箱结果的示意，不替代现场装卸安全规范。",
        "当前范围未覆盖多仓协同、冷链温控、退货回收等衍生场景（见 README 限制章节）。",
    ]
    return (
        "<ul>" + "".join(f"<li>{_esc(i)}</li>" for i in items) + "</ul>"
        + "<p><b>限制说明</b></p><ul>"
        + "".join(f"<li>{_esc(l)}</li>" for l in limits) + "</ul>"
    )


def _footer_line() -> str:
    return "智载 CityLoad · 城市配送智能配载与调度系统 · AI 重构产业架构师大赛（TRACK 02）"


# --------------------------------------------------------------------- 主函数

def build_html(routes: dict, metrics: dict, guide_text: dict,
               figure_paths: list[str], outpath: str) -> str:
    """按契约 §2.9 生成单页 HTML 报告，返回生成的文件路径。

    参数:
        routes: routes.json 结构
        metrics: metrics.json 结构（可含可选键 "comparison"，见模块 docstring）
        guide_text: guide_text.json 结构
        figure_paths: render_figures 返回的图片路径列表
        outpath: 输出 HTML 路径（如 output/report.html）
    """
    outpath = os.path.abspath(outpath)
    out_dir = os.path.dirname(outpath)
    os.makedirs(out_dir, exist_ok=True)

    routes = routes or {}
    metrics = metrics or {}
    guide_text = guide_text or {}
    figure_paths = list(figure_paths or [])

    figures = _group_figures(figure_paths)
    route_list = [r for r in routes.get("routes") or [] if isinstance(r, dict)]
    guides = [g for g in guide_text.get("guides") or [] if isinstance(g, dict)]
    per_route = metrics.get("per_route") or []
    m = metrics.get("metrics") or {}

    groups = _build_vehicle_groups(route_list, guides, figures, per_route)
    comparison_html = _comparison_html(metrics)

    context = {
        "page_title": "智载 CityLoad —— 城市配送智能配载方案（单页报告）",
        "meta_line": _meta_line(metrics, figure_paths),
        "warn_html": _warn_html(routes),
        "scene_text": _scene_text(routes, metrics),
        "perf_line": _perf_line(m),
        "cards_html": _cards_html(m),
        "routemap_html": _routemap_html(figures["route_map"], out_dir),
        "packing_html": _packing_html(groups, out_dir),
        "guide_html": _guide_html(groups, out_dir),
        "comparison_html": comparison_html,
        "notes_no": "7" if comparison_html else "6",
        "notes_html": _notes_html(metrics, routes),
        "footer_line": _footer_line(),
    }

    template_text = _TEMPLATE_PATH.read_text(encoding="utf-8")
    page = Template(template_text).safe_substitute(context)
    Path(outpath).write_text(page, encoding="utf-8")
    return outpath
