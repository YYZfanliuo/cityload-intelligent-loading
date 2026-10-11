# -*- coding: utf-8 -*-
"""tests/test_explainer.py —— T22 单测（窗口D）

验收对应（派发单）：
- 输出符合 3.5 节 guide_text.json 结构
- 步骤编号连续、门店顺序与 delivery_order 一致（装载顺序 = delivery_order 倒序）
- blocks_warning 检测 → 生成提醒文案
- 不依赖网络即可运行（纯模板，测试全程无网络调用）

另预留：output/loading_plan.json + output/routes.json 就绪后自动激活真实链路集成测试。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import agent.explainer as explainer_mod
from agent.explainer import gen_guide_text, polish_guide_text

ROOT = Path(__file__).resolve().parents[1]
ORDERS_PATH = ROOT / "data" / "orders.json"
LOADING_PLAN_PATH = ROOT / "output" / "loading_plan.json"
ROUTES_PATH = ROOT / "output" / "routes.json"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """单测一律禁用真实 LLM 调用并清空相关环境变量：保证离线、可复现。"""
    monkeypatch.setenv("CITYLOAD_DISABLE_LLM", "1")
    for k in ("CITYLOAD_LLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------------------
# mock 数据（照 interfaces.md 3.3 / 3.1 节结构构造）
# ---------------------------------------------------------------------------

MOCK_PLAN = {
    "status": "ok",
    "plans": [
        {
            "vehicle_instance": "R01-1",
            "delivery_order": ["S03", "S01", "S07"],
            "loading_steps": [
                {
                    "step": 1,
                    "action": "装",
                    "store_id": "S07",
                    "items": [{"sku": "SKU0001", "qty": 3}],
                    "note": "最后送达，最先装入，靠车头位置",
                },
                {
                    "step": 2,
                    "action": "装",
                    "store_id": "S01",
                    "items": [{"sku": "SKU0002", "qty": 5}],
                },
                {
                    "step": 3,
                    "action": "装",
                    "store_id": "S03",
                    "items": [{"sku": "SKU0003", "qty": 2}],
                },
            ],
            "blocks_warning": ["S03 的货物与 S01 存在堆叠冲突，请复核装载顺序"],
        }
    ],
}

MOCK_ROUTES = {
    "status": "ok",
    "routes": [
        {
            "route_id": "R01",
            "vehicle_type": "V01",
            "sequence": ["WH01", "S03", "S01", "S07", "WH01"],
            "stop_details": [
                {"node": "S03", "arrive": "08:12", "start_service": "08:12", "end_service": "08:27", "load_kg": 220.5},
                {"node": "S01", "arrive": "08:40", "start_service": "08:40", "end_service": "08:52", "load_kg": 180.0},
                {"node": "S07", "arrive": "09:10", "start_service": "09:10", "end_service": "09:22", "load_kg": 150.0},
            ],
            "total_distance_km": 43.2,
            "total_duration_min": 138,
            "total_load_kg": 620.3,
            "load_rate_weight": 0.41,
            "cost": 271.2,
        }
    ],
    "unassigned_orders": [],
    "summary": {"vehicle_count": 1, "total_distance_km": 43.2, "total_cost": 271.2},
}


def _steps_in(text: str) -> list[int]:
    """提取 text 中的步骤编号序列。"""
    return [int(n) for n in re.findall(r"第 (\d+) 步", text)]


def _store_name(store_id: str) -> str | None:
    """从真实 orders.json 读取门店名（用于动态断言，避免写死数据）。"""
    data = json.loads(ORDERS_PATH.read_text(encoding="utf-8"))
    for o in data.get("orders", []):
        if o.get("store_id") == store_id:
            return o.get("store_name")
    return None


# ---------------------------------------------------------------------------
# 基础模板输出
# ---------------------------------------------------------------------------

def test_basic_output_shape():
    out = gen_guide_text(MOCK_PLAN, MOCK_ROUTES)
    assert out["status"] == "ok"
    assert isinstance(out["guides"], list) and len(out["guides"]) == 1
    g = out["guides"][0]
    assert g["vehicle_instance"] == "R01-1"
    assert isinstance(g["text"], str) and g["text"]


def test_step_numbering_continuous():
    out = gen_guide_text(MOCK_PLAN, MOCK_ROUTES)
    text = out["guides"][0]["text"]
    assert _steps_in(text) == [1, 2, 3]  # 编号连续


def test_store_order_matches_reversed_delivery():
    """装载顺序必须与 delivery_order 倒序一致（后送先装）。"""
    out = gen_guide_text(MOCK_PLAN, MOCK_ROUTES)
    text = out["guides"][0]["text"]
    p07, p01, p03 = text.find("S07"), text.find("S01"), text.find("S03")
    assert p07 >= 0 and p01 >= 0 and p03 >= 0
    assert p07 < p01 < p03  # S07（最后送达）→ S01 → S03（最先送达）


def test_position_desc_and_tails():
    out = gen_guide_text(MOCK_PLAN, MOCK_ROUTES)
    text = out["guides"][0]["text"]
    # 第一步：最内侧 + 后送先装说明
    assert "装入车厢最内侧靠车头位置" in text
    assert "该门店最后送达，故最先装载" in text
    # 中间步：前批外侧
    assert "紧靠前批货物的外侧位置" in text
    # 最后一步：车尾门口 + 最先送达 + 到达时间（来自 routes.stop_details）
    assert "车厢门口（车尾）位置" in text
    assert "该门店最先送达（预计 08:12 到达），故最后装载" in text


def test_store_name_lookup():
    """门店名从 data/orders.json 补全：S07 → 实际店名。"""
    name = _store_name("S07")
    if not name:
        pytest.skip("orders.json 中未找到 S07（数据环境差异）")
    out = gen_guide_text(MOCK_PLAN, MOCK_ROUTES)
    text = out["guides"][0]["text"]
    assert f"S07（{name}）" in text


def test_qty_total_in_sentence():
    out = gen_guide_text(MOCK_PLAN, MOCK_ROUTES)
    text = out["guides"][0]["text"]
    assert "的 3 件货物" in text
    assert "的 5 件货物" in text
    assert "的 2 件货物" in text
    assert "） 的" not in text  # 中文括号与"的"之间不留多余空格


# ---------------------------------------------------------------------------
# blocks_warning 提醒
# ---------------------------------------------------------------------------

def test_blocks_warning_reminder():
    out = gen_guide_text(MOCK_PLAN, MOCK_ROUTES)
    text = out["guides"][0]["text"]
    assert "装载提醒（共 1 条）：" in text  # 汇总句
    assert "1. S03 的货物与 S01 存在堆叠冲突，请复核装载顺序。" in text  # 逐条编号换行


def test_warning_multi_entries_numbered():
    """多条警告：汇总句条数正确、逐条编号 1..N 连续、每条独立一行。"""
    plan = json.loads(json.dumps(MOCK_PLAN))
    plan["plans"][0]["blocks_warning"] = ["风险甲", "风险乙", "风险丙"]
    out = gen_guide_text(plan, MOCK_ROUTES)
    text = out["guides"][0]["text"]
    assert "装载提醒（共 3 条）：" in text
    lines = text.splitlines()
    warn_lines = [ln for ln in lines if re.match(r"^\d+\. ", ln)]
    assert [ln.split(".", 1)[0] for ln in warn_lines] == ["1", "2", "3"]
    assert warn_lines[0] == "1. 风险甲。"
    assert warn_lines[2] == "3. 风险丙。"


def test_no_warning_no_reminder():
    plan = json.loads(json.dumps(MOCK_PLAN))
    plan["plans"][0]["blocks_warning"] = []
    out = gen_guide_text(plan, MOCK_ROUTES)
    assert "装载提醒" not in out["guides"][0]["text"]


def test_warning_dict_form():
    """警告元素为 dict 时也能转成可读文案。"""
    plan = json.loads(json.dumps(MOCK_PLAN))
    plan["plans"][0]["blocks_warning"] = [{"message": "堆叠冲突：S01 ↔ S03"}]
    out = gen_guide_text(plan, MOCK_ROUTES)
    assert "堆叠冲突：S01 ↔ S03" in out["guides"][0]["text"]


# ---------------------------------------------------------------------------
# 多车 / 单步 / 边界
# ---------------------------------------------------------------------------

def test_single_step_plan():
    plan = {
        "status": "ok",
        "plans": [
            {
                "vehicle_instance": "R09-1",
                "delivery_order": ["S01"],
                "loading_steps": [{"step": 1, "action": "装", "store_id": "S01", "items": [{"sku": "X", "qty": 5}]}],
                "blocks_warning": [],
            }
        ],
    }
    out = gen_guide_text(plan, {})
    assert out["status"] == "ok"
    text = out["guides"][0]["text"]
    assert _steps_in(text) == [1]
    assert "装入车厢内" in text


def test_multiple_vehicles():
    plan = json.loads(json.dumps(MOCK_PLAN))
    plan["plans"].append(
        {
            "vehicle_instance": "R02-1",
            "delivery_order": ["S09", "S05"],
            "loading_steps": [
                {"step": 1, "action": "装", "store_id": "S05", "items": [{"sku": "A", "qty": 1}]},
                {"step": 2, "action": "装", "store_id": "S09", "items": [{"sku": "B", "qty": 2}]},
            ],
            "blocks_warning": [],
        }
    )
    out = gen_guide_text(plan, MOCK_ROUTES)  # routes 里没有 R02 → 不应出错
    assert out["status"] == "ok"
    assert len(out["guides"]) == 2
    assert out["guides"][0]["vehicle_instance"] == "R01-1"
    assert out["guides"][1]["vehicle_instance"] == "R02-1"
    assert _steps_in(out["guides"][1]["text"]) == [1, 2]


def test_empty_plans_ok():
    out = gen_guide_text({"status": "ok", "plans": []}, MOCK_ROUTES)
    assert out["status"] == "ok"
    assert out["guides"] == []


def test_invalid_inputs_never_raise():
    cases = [
        (None, None),
        ({}, {}),
        ({"plans": "not-a-list"}, {}),
        ({"plans": [None, 123, "x"]}, {}),
        ({"plans": [{"vehicle_instance": "R99-1", "loading_steps": []}]}, {}),
        ({"plans": [{"vehicle_instance": "R99-1", "loading_steps": [{"step": 1}]}]}, {}),  # 缺 store_id
        ({"plans": [{"loading_steps": [{"store_id": "S01", "items": []}]}]}, {"routes": "bad"}),
    ]
    for plan, rt in cases:
        out = gen_guide_text(plan, rt)
        assert isinstance(out, dict), f"输入 {plan!r} 未返回 dict"
        assert out.get("status") in {"ok", "error"}
        assert isinstance(out.get("guides"), list)


# ---------------------------------------------------------------------------
# 真实链路集成（等窗口C 的 T13 / T25 产出后自动激活）
# ---------------------------------------------------------------------------

def _discover_pipeline_sets() -> list:
    """自动发现真实链路产物数据集。

    支持两种路径约定：
    - 契约路径：output/loading_plan.json + output/routes.json
    - 联调路径：outputs/{seed}_loading_plan.json + outputs/{seed}_routes.json（多套数据）
    """
    sets = []
    contract_plan = ROOT / "output" / "loading_plan.json"
    contract_routes = ROOT / "output" / "routes.json"
    if contract_plan.exists() and contract_routes.exists():
        sets.append(("output", contract_plan, contract_routes))
    outs = ROOT / "outputs"
    if outs.is_dir():
        for plan_path in sorted(outs.glob("*_loading_plan.json")):
            seed = plan_path.name[: -len("_loading_plan.json")]
            routes_path = outs / f"{seed}_routes.json"
            if routes_path.exists():
                sets.append((seed, plan_path, routes_path))
    return sets


def _check_pipeline_set(name: str, plan_path: Path, routes_path: Path) -> None:
    """对一套真实数据做全量校验：结构 / 编号连续 / 门店顺序=delivery_order 倒序。"""
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    routes = json.loads(routes_path.read_text(encoding="utf-8"))
    out = gen_guide_text(plan, routes)

    assert out["status"] == "ok", f"{name}: status 非 ok"
    assert len(out["guides"]) == len(plan["plans"]), f"{name}: guides 数量 {len(out['guides'])} != plans {len(plan['plans'])}"

    for plan_i, guide in zip(plan["plans"], out["guides"]):
        vi = plan_i.get("vehicle_instance", "")
        text = guide["text"]
        assert guide["vehicle_instance"] == vi, f"{name}: vehicle_instance 不一致"
        body = text.split("装载提醒")[0]  # 校验范围排除提醒区（其中也含门店编号）

        idx = [int(n) for n in re.findall(r"第 (\d+) 步", body)]
        assert idx, f"{name}/{vi}: 无装载步骤"
        assert idx == list(range(1, len(idx) + 1)), f"{name}/{vi}: 步骤编号不连续 {idx}"

        rev = [s for s in reversed(plan_i.get("delivery_order") or []) if isinstance(s, str)]
        positions = [p for p in (body.find(s) for s in rev) if p >= 0]
        assert positions == sorted(positions), f"{name}/{vi}: 门店装载顺序与 delivery_order 倒序不一致"


def test_real_pipeline_integration():
    """真实链路集成测试：自动发现 output/ 与 outputs/ 下的全部数据集逐一校验。"""
    pipeline_sets = _discover_pipeline_sets()
    if not pipeline_sets:
        pytest.skip("等待窗口C 产出 output(s)/*_loading_plan.json + *_routes.json 后自动激活")
    for name, plan_path, routes_path in pipeline_sets:
        _check_pipeline_set(name, plan_path, routes_path)


# ---------------------------------------------------------------------------
# 可选增强：LLM 润色（全部 mock，离线确定性）
# ---------------------------------------------------------------------------

POLISH_ORIGINAL = (
    "第 1 步：将 S07（连锁药店07）的 3 件货物装入车厢最内侧靠车头位置，该门店最后送达，故最先装载。\n"
    "第 2 步：将 S01（连锁药店01）的 5 件货物装入紧靠前批货物的外侧位置。\n"
    "装载提醒：S03 的货物与 S01 存在堆叠冲突，请复核装载顺序。"
)


def test_polish_unavailable_falls_back():
    """无 LLM（离线）时回退模板原文（模板始终是兜底）。"""
    out = polish_guide_text(POLISH_ORIGINAL)
    assert out["source"] == "template_fallback"
    assert out["text"] == POLISH_ORIGINAL
    assert out["detail"] == "llm_unavailable"


def test_polish_success_with_mock(monkeypatch):
    """mock 合法润色结果：编号/门店/数量不变 → 采用润色版。"""
    monkeypatch.setenv("CITYLOAD_DISABLE_LLM", "0")
    monkeypatch.setenv("CITYLOAD_LLM_API_KEY", "dummy-key")
    polished = (
        "第 1 步：请将 S07（连锁药店07）的 3 件货物稳妥装入车厢最内侧、紧靠车头的位置——该门店最后送达，须最先装载。\n"
        "第 2 步：随后将 S01（连锁药店01）的 5 件货物装入紧靠前批货物外侧的位置。\n"
        "装载提醒：S03 的货物与 S01 存在堆叠冲突，请复核装载顺序。"
    )
    monkeypatch.setattr(explainer_mod, "_call_polish_llm", lambda text, cfg: polished)
    out = polish_guide_text(POLISH_ORIGINAL)
    assert out["source"] == "llm"
    assert out["text"] == polished


def test_polish_validation_rejects_lost_step(monkeypatch):
    """润色丢失步骤编号 → 校验拦截 → 回退模板原文。"""
    monkeypatch.setenv("CITYLOAD_DISABLE_LLM", "0")
    monkeypatch.setenv("CITYLOAD_LLM_API_KEY", "dummy-key")
    bad = "第 1 步：将 S07（连锁药店07）的 3 件货物装入车厢最内侧靠车头位置。"  # 丢了第 2 步与提醒
    monkeypatch.setattr(explainer_mod, "_call_polish_llm", lambda text, cfg: bad)
    out = polish_guide_text(POLISH_ORIGINAL)
    assert out["source"] == "template_fallback"
    assert "validation_failed" in out["detail"]
    assert out["text"] == POLISH_ORIGINAL


def test_polish_validation_rejects_lost_store(monkeypatch):
    """润色丢失门店编号 → 校验拦截 → 回退。"""
    monkeypatch.setenv("CITYLOAD_DISABLE_LLM", "0")
    monkeypatch.setenv("CITYLOAD_LLM_API_KEY", "dummy-key")
    bad = POLISH_ORIGINAL.replace("S01", "该门店")
    monkeypatch.setattr(explainer_mod, "_call_polish_llm", lambda text, cfg: bad)
    out = polish_guide_text(POLISH_ORIGINAL)
    assert out["source"] == "template_fallback"
    assert "门店编号丢失" in out["detail"]


def test_polish_call_error_falls_back(monkeypatch):
    monkeypatch.setenv("CITYLOAD_DISABLE_LLM", "0")
    monkeypatch.setenv("CITYLOAD_LLM_API_KEY", "dummy-key")

    def _boom(text, cfg):
        raise RuntimeError("network down")

    monkeypatch.setattr(explainer_mod, "_call_polish_llm", _boom)
    out = polish_guide_text(POLISH_ORIGINAL)
    assert out["source"] == "template_fallback"
    assert "call_failed" in out["detail"]
    assert out["text"] == POLISH_ORIGINAL


def test_polish_reminder_count_mismatch_rejected(monkeypatch):
    """润色篡改"共 N 条"汇总 → 校验拦截 → 回退模板原文。"""
    monkeypatch.setenv("CITYLOAD_DISABLE_LLM", "0")
    monkeypatch.setenv("CITYLOAD_LLM_API_KEY", "dummy-key")
    original = "第 1 步：将 S01 的 3 件货物装入车厢内。\n装载提醒（共 2 条）：\n1. 风险甲。\n2. 风险乙。"
    bad = "第 1 步：将 S01 的 3 件货物装入车厢内。\n装载提醒（共 3 条）：\n1. 风险甲。\n2. 风险乙。\n3. 风险丙。"
    monkeypatch.setattr(explainer_mod, "_call_polish_llm", lambda text, cfg: bad)
    out = polish_guide_text(original)
    assert out["source"] == "template_fallback"
    assert "汇总结数不一致" in out["detail"]


def test_polish_invalid_input():
    for bad in ("", "   ", None, 123):
        out = polish_guide_text(bad)  # type: ignore[arg-type]
        assert out["source"] == "error"
