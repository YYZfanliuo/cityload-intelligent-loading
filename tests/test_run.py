"""主管线 mock 接线、计数守恒与输出验证。"""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import run


def _data(tmp_path):
    """创建仅用于 mock 管线的输入。"""
    data = tmp_path / "data"
    data.mkdir()
    orders = {"orders": [{"order_id": "O1", "items": [{"qty": 3}]},
                         {"order_id": "O2", "items": [{"qty": 4}]}]}
    for name, value in (("orders", orders), ("vehicles", {}), ("network", {}), ("rules", {})):
        (data / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
    return data, orders


def test_mock_wiring_outputs_performance_and_mutation(tmp_path, monkeypatch):
    """所有求解均 mock；验证调用顺序及原生性能指标保留。"""
    data, orders = _data(tmp_path)
    routes = {"routes": [], "unassigned_orders": ["O2"], "solve_time_sec": 12.34, "peak_memory_mb": 222.5}
    packing = {"vehicles": [{"vehicle_instance": "R01-1", "placed": [{"position_cm": {"x": 0}}], "unplaced": [{"qty": 2}]}]}
    plan = {"plans": [{"vehicle_instance": "R01-1", "blocks_warning": ["真实阻挡"]}]}
    routing = Mock(return_value=routes)
    packer = Mock(return_value=packing)

    def sequence(p, r, rules):
        """模拟排序的就地坐标更新。"""
        assert p is packing and r is routes
        p["vehicles"][0]["placed"][0]["position_cm"]["x"] = 99
        return plan

    explainer = Mock(return_value={"status": "ok", "guides": []})
    evaluator = Mock(return_value={"metrics": {"solve_time_sec": 12.34, "peak_memory_mb": 222.5}})

    def render(r, p, lp, network, outdir):
        """检查落盘数据已由排序更新，并输出 mock PNG 文件。"""
        assert json.loads((Path(outdir).parent / "packing.json").read_text(encoding="utf-8"))["vehicles"][0]["placed"][0]["position_cm"]["x"] == 99
        Path(outdir).mkdir()
        png = Path(outdir) / "route_map.png"
        png.write_bytes(b"mock-png")
        return [str(png)]

    def html(r, m, guide, figures, outpath):
        """输出测试报告，不调用真实地图渲染。"""
        assert "未装载 2 件" in guide["guides"][0]["text"]
        assert "真实阻挡" in guide["guides"][0]["text"]
        Path(outpath).write_text("测试报告", encoding="utf-8")
        return outpath

    for name, value in (("solve_routing", routing), ("solve_packing", packer),
                        ("sequence_loading", sequence), ("gen_guide_text", explainer),
                        ("compute_metrics", evaluator), ("render_figures", render), ("build_html", html)):
        monkeypatch.setattr(run, name, value)
    out = tmp_path / "out"
    result = run.run_pipeline(data, out)
    explainer.assert_called_once_with(plan, routes)
    assert routing.call_args.args[0] == orders
    assert packer.call_args.args[0] is routes
    eval_input = evaluator.call_args.args[0]
    assert eval_input["solve_time_sec"] == 12.34
    assert eval_input["peak_memory_mb"] == 222.5
    assert eval_input["packing"] is packing
    m = result["metrics"]["metrics"]
    assert (m["total_item_count"], m["placed_item_count"], m["unplaced_item_count"]) == (7, 1, 6)
    assert "comparison" not in result["metrics"]
    for name in ("routes.json", "packing.json", "loading_plan.json", "metrics.json", "guide_text.json", "report.html", "figures/route_map.png"):
        assert (out / name).is_file()


def test_counts_qty_empty_and_invalid():
    """聚合 qty、空输入和求解遗漏均不静默误报。"""
    assert run._item_counts({"orders": [{"order_id": "O1", "items": [{"qty": 3}]}]}, {"vehicles": [{"placed": [{}, {}, {}], "unplaced": []}]}, {}) == (3, 3, 0)
    assert run._item_counts({}, {}, {}) == (0, 0, 0)
    with pytest.raises(ValueError, match="不守恒"):
        run._item_counts({"orders": [{"order_id": "O1", "items": [{"qty": 1}]}]}, {}, {})
