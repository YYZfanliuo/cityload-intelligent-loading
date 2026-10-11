# -*- coding: utf-8 -*-
"""tests/test_parser.py —— T21 单测（窗口D）

验收对应：
- 5 条自然语言样例解析正确率 ≥ 80%（离线规则路径，禁用 LLM 保证可复现）
- 乱码 / 超长 / 非法类型输入 → 返回 error 而非崩溃
- 无 API Key 时全流程不中断（解析 + 旁路读取均正常）
- LLM 失败自动回退规则解析；严格模式（CITYLOAD_LLM_STRICT=1）返回 llm_unavailable
- load_structured_fallback 正常 / 异常路径
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import agent.parser as parser_mod
from agent.parser import load_structured_fallback, parse_requirement

ROOT = Path(__file__).resolve().parents[1]
ORDERS_PATH = ROOT / "data" / "orders.json"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """单测一律禁用真实 LLM 调用并清空相关环境变量：保证离线、可复现。"""
    monkeypatch.setenv("CITYLOAD_DISABLE_LLM", "1")
    for k in (
        "CITYLOAD_LLM_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "CITYLOAD_LLM_STRICT",
        "CITYLOAD_LLM_BASE_URL",
        "CITYLOAD_LLM_MODEL",
    ):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------------------
# 5 条自然语言样例（含派发单原例）
# ---------------------------------------------------------------------------

SAMPLES = [
    {
        "name": "样例1 派发单原例（易碎+时间窗）",
        "text": "明天上午给杭州萧山的康健药房送 5 箱感冒灵和 3 箱板蓝根，12 点前必须到，药品易碎要轻放",
        "store_kw": "康健药房",
        "items": [("感冒灵", 5), ("板蓝根", 3)],
        "window": ["08:00", "12:00"],
        "extra": lambda out: all(it["fragile"] for it in out["items"]),
        "extra_desc": "全部货物 fragile=True",
    },
    {
        "name": "样例2 下午时间段+数量干扰词",
        "text": "后天下午 2 点到 4 点给滨江区的仁和大药房送 10 盒布洛芬缓释胶囊和 6 盒维C银翘片，货比较重注意承重",
        "store_kw": "仁和大药房",
        "items": [("布洛芬", 10), ("维C银翘片", 6)],
        "window": ["14:00", "16:00"],
    },
    {
        "name": "样例3 早上X点前+易碎勿压",
        "text": "请明天早上 9 点前把 20 个医用外科口罩和 15 瓶碘伏消毒液送到拱墅区的老百姓大药房，易碎勿压",
        "store_kw": "老百姓大药房",
        "items": [("医用外科口罩", 20), ("碘伏消毒液", 15)],
        "window": ["08:00", "09:00"],
        "extra": lambda out: all(it["fragile"] for it in out["items"]),
        "extra_desc": "全部货物 fragile=True",
    },
    {
        "name": "样例4 名称+数量语序+不可堆叠",
        "text": "萧山区北干街道的华东医药连锁店需要补货：板蓝根颗粒 30 箱、阿莫西林胶囊 12 箱、六味地黄丸 8 箱，中午前送到，全部不可堆叠",
        "store_kw": "华东医药连锁店",
        "items": [("板蓝根", 30), ("阿莫西林", 12), ("六味地黄丸", 8)],
        "window": ["08:00", "12:00"],
        "extra": lambda out: all((not it["stackable"]) and it["max_stack_layers"] == 1 for it in out["items"]),
        "extra_desc": "全部 stackable=False 且 max_stack_layers=1",
    },
    {
        "name": "样例5 卸货时长+干扰句",
        "text": "后天下午把 4 托洗手液和 9 箱抽纸送到临平区的人民药店，卸货大约需要 25 分钟，请走地面层勿走高架",
        "store_kw": "人民药店",
        "items": [("洗手液", 4), ("抽纸", 9)],
        "window": ["14:00", "18:00"],
        "extra": lambda out: out["service_minutes"] == 25,
        "extra_desc": "service_minutes=25",
    },
]


def _check_sample(sample: dict, out: dict) -> list[str]:
    """逐项检查一条样例的解析结果，返回失败原因列表（空 = 通过）。"""
    if "error" in out:
        return [f"返回 error: {out.get('error')} / {out.get('detail', '')}"]
    fails: list[str] = []
    if sample["store_kw"] not in (out.get("store_name") or ""):
        fails.append(f"store_name={out.get('store_name')!r} 不含 {sample['store_kw']!r}")
    got = [(it["name"], it["qty"]) for it in out.get("items", [])]
    if len(got) != len(sample["items"]):
        fails.append(f"items 数量 {len(got)} != 期望 {len(sample['items'])}；实际 {got}")
    else:
        for (gn, gq), (kw, eq) in zip(got, sample["items"]):
            if kw not in gn:
                fails.append(f"item 名称 {gn!r} 不含关键词 {kw!r}")
            if gq != eq:
                fails.append(f"item {gn!r} 数量 {gq} != 期望 {eq}")
    if out.get("time_window") != sample["window"]:
        fails.append(f"time_window={out.get('time_window')} != 期望 {sample['window']}")
    if sample.get("extra") and not sample["extra"](out):
        fails.append(f"额外断言未过：{sample.get('extra_desc')}")
    return fails


def test_five_samples_accuracy():
    """5 条自然语言样例解析正确率 ≥ 80%（逐条附实际输出，pytest -s 可直接查看）。"""
    passed = 0
    lines: list[str] = []
    for i, sample in enumerate(SAMPLES, 1):
        out = parse_requirement(sample["text"])
        fails = _check_sample(sample, out)
        status = "PASS" if not fails else "FAIL"
        if not fails:
            passed += 1
        lines.append(f"\n[样例{i}] {status} —— {sample['name']}")
        lines.append(f"  输入: {sample['text']}")
        for f in fails:
            lines.append(f"  └─ ✗ {f}")
        lines.append("  实际输出:")
        for ln in json.dumps(out, ensure_ascii=False, indent=2).splitlines():
            lines.append("    " + ln)
    report = "\n".join(lines)
    rate = passed / len(SAMPLES)
    print(f"\n===== 5 条自然语言样例（正确 {passed}/{len(SAMPLES)} = {rate:.0%}） =====")
    print(report)
    print(f"===== 正确率 {passed}/{len(SAMPLES)} = {rate:.0%} =====")
    assert passed >= 4, f"正确率 {rate:.0%} 低于 80% 验收线：\n{report}"


# ---------------------------------------------------------------------------
# 异常输入：返回 error 而非崩溃
# ---------------------------------------------------------------------------

def test_garbage_input_returns_error():
    out = parse_requirement("￥%……&*（）——+{}【】「」、？≈≠ 乱码乱码乱码")
    assert isinstance(out, dict) and "error" in out


def test_overlong_input_returns_error():
    long_text = "给杭州康健药房送 5 箱感冒灵，" * 300
    assert len(long_text) > 2000
    out = parse_requirement(long_text)
    assert out.get("error") == "invalid_input"


def test_invalid_input_types():
    for bad in (None, 123, [], {}, True):
        out = parse_requirement(bad)  # type: ignore[arg-type]
        assert isinstance(out, dict) and "error" in out
    for empty in ("", "   ", "\n\t "):
        out = parse_requirement(empty)
        assert out.get("error") == "invalid_input"


def test_weird_inputs_never_raise():
    weird = [
        "🙂🚚 emoji 测试",
        "aaa bbb ccc ddd",
        "送",
        "1234567890",
        "送 0 箱感冒灵",              # 数量无效 → 应返回 error
        "明天 25 点给门店送 3 箱东西",  # 非法时间 → 不应崩溃
    ]
    for w in weird:
        out = parse_requirement(w)
        assert isinstance(out, dict), f"输入 {w!r} 未返回 dict"
        assert ("error" in out) or ("items" in out), f"输入 {w!r} 返回结构异常: {out}"
    # 数量为 0 的货物应被拒绝（无有效物品）
    assert "error" in parse_requirement("送 0 箱感冒灵")


# ---------------------------------------------------------------------------
# 无 API Key / 严格模式 / LLM 降级
# ---------------------------------------------------------------------------

def test_no_api_key_full_flow():
    """无 API Key 时全流程不中断：解析走规则路径 + 旁路读取正常。"""
    out = parse_requirement("明天上午给康健药房送 2 箱感冒灵")
    assert "error" not in out, out
    assert out["parse_meta"]["source"] == "rule"
    assert len(out["items"]) == 1 and out["items"][0]["qty"] == 2

    data = load_structured_fallback(str(ORDERS_PATH))
    assert "error" not in data
    assert isinstance(data.get("orders"), list) and len(data["orders"]) >= 10
    assert "meta" in data


def test_strict_mode_without_key_returns_llm_unavailable(monkeypatch):
    monkeypatch.setenv("CITYLOAD_LLM_STRICT", "1")
    out = parse_requirement("给康健药房送 5 箱感冒灵")
    assert out.get("error") == "llm_unavailable"


def test_llm_failure_falls_back_to_rule(monkeypatch):
    """LLM 调用报错 → 默认模式自动回退规则解析，流程不中断。"""
    monkeypatch.setenv("CITYLOAD_DISABLE_LLM", "0")
    monkeypatch.setenv("CITYLOAD_LLM_API_KEY", "dummy-key")

    def _boom(text, cfg):
        raise RuntimeError("network down")

    monkeypatch.setattr(parser_mod, "_call_llm", _boom)
    out = parse_requirement("给康健药房送 5 箱感冒灵")
    assert "error" not in out
    assert out["parse_meta"]["source"] == "rule_fallback"
    assert "RuntimeError" in out["parse_meta"].get("llm_error", "")


def test_llm_invalid_json_falls_back(monkeypatch):
    monkeypatch.setenv("CITYLOAD_DISABLE_LLM", "0")
    monkeypatch.setenv("CITYLOAD_LLM_API_KEY", "dummy-key")
    monkeypatch.setattr(parser_mod, "_call_llm", lambda text, cfg: "抱歉，我无法解析这段文本。")
    out = parse_requirement("给康健药房送 5 箱感冒灵")
    assert "error" not in out
    assert out["parse_meta"]["source"] == "rule_fallback"
    assert "json_extract" in out["parse_meta"].get("llm_error", "")


def test_llm_bad_schema_falls_back(monkeypatch):
    monkeypatch.setenv("CITYLOAD_DISABLE_LLM", "0")
    monkeypatch.setenv("CITYLOAD_LLM_API_KEY", "dummy-key")
    bad = json.dumps(
        {"store_name": "康健药房", "items": [{"name": "感冒灵", "qty": "很多"}]},
        ensure_ascii=False,
    )
    monkeypatch.setattr(parser_mod, "_call_llm", lambda text, cfg: bad)
    out = parse_requirement("给康健药房送 5 箱感冒灵")
    assert "error" not in out
    assert out["parse_meta"]["source"] == "rule_fallback"
    assert "schema_invalid" in out["parse_meta"].get("llm_error", "")


def test_llm_success_path_with_mock(monkeypatch):
    """mock 一个合法 LLM 回复：验证 LLM 链路（围栏提取 → 规范化 → 校验 → source=llm）。"""
    monkeypatch.setenv("CITYLOAD_DISABLE_LLM", "0")
    monkeypatch.setenv("CITYLOAD_LLM_API_KEY", "dummy-key")
    reply = json.dumps(
        {
            "order_id": None,
            "store_id": None,
            "store_name": "康健药房",
            "lat": None,
            "lng": None,
            "time_window": ["8:00", "12:00"],  # 故意不规范 → 验证规范化
            "service_minutes": None,
            "items": [
                {
                    "sku": None,
                    "name": "感冒灵",
                    "qty": "5",  # 故意用字符串 → 验证宽容转换
                    "weight_kg": None,
                    "length_cm": None,
                    "width_cm": None,
                    "height_cm": None,
                    "fragile": True,
                    "stackable": True,
                    "max_stack_layers": 6,
                }
            ],
        },
        ensure_ascii=False,
    )
    monkeypatch.setattr(parser_mod, "_call_llm", lambda text, cfg: "```json\n" + reply + "\n```")
    out = parse_requirement("随便一段话")
    assert "error" not in out, out
    assert out["parse_meta"]["source"] == "llm"
    assert out["time_window"] == ["08:00", "12:00"]
    assert out["items"][0]["qty"] == 5
    assert out["items"][0]["fragile"] is True
    # LLM 路径同样走 SKU 库增强："感冒灵" → SKU0002 且补全物理参数
    assert out["items"][0]["sku"] == "SKU0002"
    assert out["items"][0]["weight_kg"] is not None


# ---------------------------------------------------------------------------
# 旁路读取 / 输出结构
# ---------------------------------------------------------------------------

def test_load_structured_fallback_ok():
    raw = json.loads(ORDERS_PATH.read_text(encoding="utf-8"))
    data = load_structured_fallback(str(ORDERS_PATH))
    assert data == raw  # 旁路读取与直接读取完全一致


def test_load_structured_fallback_errors(tmp_path):
    out = load_structured_fallback(str(tmp_path / "not_exist.json"))
    assert out.get("error") == "file_not_found"

    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    out = load_structured_fallback(str(bad))
    assert out.get("error") == "invalid_json"

    wrong = tmp_path / "wrong.json"
    wrong.write_text('{"foo": []}', encoding="utf-8")
    out = load_structured_fallback(str(wrong))
    assert out.get("error") == "invalid_structure"


def test_rule_output_shape():
    """规则路径输出符合订单结构约定（含 missing_fields / parse_meta 标注）。"""
    out = parse_requirement("明天上午给康健药房送 5 箱感冒灵和 3 箱板蓝根，12 点前必须到")
    for k in ("order_id", "store_id", "store_name", "lat", "lng", "time_window", "service_minutes", "items"):
        assert k in out, f"缺少字段 {k}"
    assert out["service_minutes"] == 15  # 未提及 → 默认值
    assert out["missing_fields"][:4] == ["order_id", "store_id", "lat", "lng"]
    assert out["parse_meta"]["source"] == "rule"
