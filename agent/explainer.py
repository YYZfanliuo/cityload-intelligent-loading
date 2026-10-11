# -*- coding: utf-8 -*-
"""agent/explainer.py —— T22 模板化装卸指引文案生成（窗口D）

接口（docs/interfaces.md v1.0 第 2.7 节，签名已冻结，不得更改）：
    gen_guide_text(loading_plan: dict, routes: dict) -> dict
        返回 guide_text.json 结构（见 3.5）。纯模板生成，不依赖 LLM、不联网。

设计说明
--------
把 loading_plan.json 的结构化 loading_steps 转成人类可读的分步装车指引：

    第 1 步：将 S07（连锁药店07）的 3 件货物装入车厢最内侧靠车头位置，该门店最后送达，故最先装载。
    第 2 步：将 S01（连锁药店01）的 5 件货物装入紧靠前批货物的外侧位置。
    第 3 步：将 S03（连锁药店03）的 2 件货物装入车厢门口（车尾）位置，该门店最先送达（预计 08:12 到达），故最后装载。

实现要点：
- 步骤编号按数组顺序重排为连续 1..N（验收要求：编号连续、门店装载顺序与
  delivery_order 一致，即"后送先装"= delivery_order 的倒序）；
- 门店名（S07 → "连锁药店07"）懒加载自 data/orders.json（只读、失败静默，
  读不到时仅显示 store_id）；
- 最先送达门店的"预计到达时间"从 routes.json 的 stop_details 中提取（可选增强）；
- blocks_warning 非空时在指引末尾追加"装载提醒：..."文案；
- 全程不抛异常：结构非法返回 {"status": "error", ...}，单条计划异常跳过。
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Optional

_ORDERS_PATH = Path(__file__).resolve().parents[1] / "data" / "orders.json"


# ---------------------------------------------------------------------------
# 公共接口（契约冻结）
# ---------------------------------------------------------------------------

def gen_guide_text(loading_plan: dict, routes: dict) -> dict:
    """把 loading_plan 转为分步装卸指引文案；返回 guide_text.json 结构（3.5）。

    纯模板生成，不依赖 LLM、不联网；任何异常输入均安全返回，绝不抛异常。
    """
    try:
        if not isinstance(loading_plan, dict) or not isinstance(loading_plan.get("plans"), list):
            return {
                "status": "error",
                "guides": [],
                "error": "invalid_loading_plan",
                "detail": "loading_plan 必须为含 plans 列表的字典",
            }

        routes_dict = routes if isinstance(routes, dict) else {}
        stores = _load_store_names()
        guides: list[dict] = []

        for plan in loading_plan["plans"]:
            if not isinstance(plan, dict):
                continue
            text = _plan_to_text(plan, routes_dict, stores)
            if text is None:
                continue
            guides.append(
                {
                    "vehicle_instance": str(plan.get("vehicle_instance", "")),
                    "text": text,
                }
            )

        return {"status": "ok", "guides": guides}
    except Exception as exc:  # 终极防线：任何未预期异常一律转为错误结构
        return {
            "status": "error",
            "guides": [],
            "error": "internal_error",
            "detail": f"{type(exc).__name__}: {exc}",
        }


# ---------------------------------------------------------------------------
# 单条计划 → 指引文本
# ---------------------------------------------------------------------------

def _plan_to_text(plan: dict, routes: dict, stores: dict) -> Optional[str]:
    """把单个 plan 生成为整段指引文本；无法生成（无步骤/无门店）返回 None。"""
    steps = plan.get("loading_steps")
    if not isinstance(steps, list) or not steps:
        return None

    delivery = [s for s in (plan.get("delivery_order") or []) if isinstance(s, str)]
    reversed_delivery = list(reversed(delivery))
    route = _find_route(routes, plan.get("vehicle_instance"))

    lines: list[str] = []
    total = len(steps)
    used = 0  # 实际生成的有效步数（用于连续编号）

    for raw_step in steps:
        if not isinstance(raw_step, dict):
            continue
        store_id = raw_step.get("store_id")
        if not isinstance(store_id, str) or not store_id:
            continue
        used += 1
        idx = used  # 重编号：保证连续 1..N

        qty = _count_items(raw_step.get("items"))
        name = stores.get(store_id)

        pos = _position_desc(idx, total)
        tail = ""
        if len(reversed_delivery) >= idx and reversed_delivery[idx - 1] == store_id:
            if total > 1 and idx == 1:
                tail = "，该门店最后送达，故最先装载"
            elif total > 1 and idx == total:
                arrive = _find_arrive(route, delivery[0]) if delivery else None
                arrive_txt = f"（预计 {arrive} 到达）" if arrive else ""
                tail = f"，该门店最先送达{arrive_txt}，故最后装载"

        lines.append(_step_sentence(idx, store_id, name, qty, pos, tail))

    if not lines:
        return None

    warn_lines = _warning_lines(plan.get("blocks_warning"))
    lines.extend(warn_lines)
    return "\n".join(lines)


def _position_desc(idx: int, total: int) -> str:
    """装载位置描述：第一个在最内侧靠车头；最后一个在车尾门口；中间紧随前批外侧。"""
    if total <= 1:
        return "车厢内"
    if idx == 1:
        return "车厢最内侧靠车头位置"
    if idx == total:
        return "车厢门口（车尾）位置"
    return "紧靠前批货物的外侧位置"


def _step_sentence(
    idx: int,
    store_id: str,
    store_name: Optional[str],
    qty: Optional[int],
    pos: str,
    tail: str,
) -> str:
    """生成单步文案，如：第 1 步：将 S07（连锁药店07）的 3 件货物装入……。"""
    # "S07（连锁药店07）的…" 中文括号与"的"之间不留空格；无店名时 "S07 的…" 保留空格
    who = f"{store_id}（{store_name}）" if store_name else f"{store_id} "
    qty_txt = f"{qty} 件" if isinstance(qty, int) and qty > 0 else "若干件"
    return f"第 {idx} 步：将 {who}的 {qty_txt}货物装入{pos}{tail}。"


def _count_items(items: Any) -> Optional[int]:
    """统计一步中的货物件数（qty 求和）；无法统计返回 None。"""
    if not isinstance(items, list) or not items:
        return None
    total = 0
    seen = False
    for it in items:
        if isinstance(it, dict):
            q = it.get("qty")
            if isinstance(q, int) and not isinstance(q, bool) and q > 0:
                total += q
                seen = True
    return total if seen else None


def _warning_lines(warnings: Any) -> list[str]:
    """把 blocks_warning 转为提醒文案行；空/非法则返回空列表。"""
    if not isinstance(warnings, list) or not warnings:
        return []
    parts: list[str] = []
    for w in warnings:
        text = _warning_text(w)
        if text:
            parts.append(text)
    if not parts:
        return []
    return ["装载提醒：" + "；".join(parts) + "。"]


def _warning_text(w: Any) -> str:
    """规范化单条警告为可读文本（兼容 str / dict / 其他类型）。"""
    if isinstance(w, str):
        return w.strip()
    if isinstance(w, dict):
        for k in ("message", "detail", "text", "note"):
            v = w.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        try:
            return json.dumps(w, ensure_ascii=False)
        except Exception:
            return str(w)
    if w is None:
        return ""
    return str(w)


# ---------------------------------------------------------------------------
# routes 关联（可选增强：取最先送达门店的预计到达时间）
# ---------------------------------------------------------------------------

def _find_route(routes: dict, vehicle_instance: Any) -> Optional[dict]:
    """按 vehicle_instance（"R01-1"）前缀匹配 route_id（"R01"）。"""
    if not isinstance(vehicle_instance, str) or not vehicle_instance:
        return None
    rid = vehicle_instance.split("-")[0]
    for r in routes.get("routes") or []:
        if isinstance(r, dict) and r.get("route_id") == rid:
            return r
    return None


def _find_arrive(route: Optional[dict], node: Optional[str]) -> Optional[str]:
    """从 route.stop_details 中取指定节点的到达时间（"HH:MM"）。"""
    if not route or not node:
        return None
    for sd in route.get("stop_details") or []:
        if isinstance(sd, dict) and sd.get("node") == node:
            arrive = sd.get("arrive")
            if isinstance(arrive, str) and arrive:
                return arrive
    return None


# ---------------------------------------------------------------------------
# 门店名索引（只读 data/orders.json，失败静默降级）
# ---------------------------------------------------------------------------

_STORE_CACHE: Optional[dict] = None


def _load_store_names() -> dict:
    """懒加载 {store_id: store_name}；读不到返回空字典（指引退化为仅显示编号）。"""
    global _STORE_CACHE
    if _STORE_CACHE is not None:
        return _STORE_CACHE
    names: dict[str, str] = {}
    try:
        with open(_ORDERS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for order in data.get("orders", []):
            sid, name = order.get("store_id"), order.get("store_name")
            if isinstance(sid, str) and isinstance(name, str) and sid not in names:
                names[sid] = name
    except Exception:
        names = {}
    _STORE_CACHE = names
    return names


# ---------------------------------------------------------------------------
# 可选增强：LLM 文风润色（独立函数，默认不启用；gen_guide_text 本身保持纯模板）
# ---------------------------------------------------------------------------

_POLISH_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "polish_guide.txt"
_POLISH_BUILTIN = (
    "请对用户给出的中文装车指引做文风润色：保持每步一行、以「第 N 步：」开头，"
    "编号与顺序不得改变；不得改动任何门店编号（S 开头）、数字、数量与时间；"
    "保留「装载提醒：」行；只输出润色后的文本本体。"
)
_POLISH_PROMPT_CACHE: Optional[str] = None


def polish_guide_text(text: str) -> dict:
    """可选增强（调用方显式调用）：用 LLM 对模板指引做文风润色，模板原文兜底。

    硬性约束：润色不得改变步骤编号、门店编号与数量；结果须通过 _validate_polish
    校验，任何失败（无 Key / 超时 / 校验不过）一律回退模板原文。返回：
        {"text": 最终文本, "source": "llm" | "template_fallback" | "error", "detail": ...}
    """
    try:
        original = text if isinstance(text, str) else ""
        if not original.strip():
            return {"text": original, "source": "error", "detail": "invalid_input"}

        cfg = _resolve_polish_config()
        if cfg is None:
            return {"text": original, "source": "template_fallback", "detail": "llm_unavailable"}

        try:
            polished = _call_polish_llm(original, cfg).strip()
        except Exception as exc:
            return {
                "text": original,
                "source": "template_fallback",
                "detail": f"call_failed: {type(exc).__name__}: {exc}",
            }

        ok, reason = _validate_polish(original, polished)
        if not ok:
            return {
                "text": original,
                "source": "template_fallback",
                "detail": f"validation_failed: {reason}",
            }
        return {"text": polished, "source": "llm"}
    except Exception as exc:  # 终极防线
        return {
            "text": text if isinstance(text, str) else "",
            "source": "template_fallback",
            "detail": f"internal_error: {exc}",
        }


def _validate_polish(original: str, polished: str) -> tuple[bool, str]:
    """校验润色结果未破坏关键信息；返回 (是否通过, 失败原因)。"""
    o_idx = re.findall(r"第\s*(\d+)\s*步", original)
    p_idx = re.findall(r"第\s*(\d+)\s*步", polished)
    if o_idx != p_idx:
        return False, f"步骤编号序列不一致：{o_idx} -> {p_idx}"
    o_stores = set(re.findall(r"S\d+", original))
    p_stores = set(re.findall(r"S\d+", polished))
    if not o_stores.issubset(p_stores):
        return False, f"门店编号丢失：{sorted(o_stores - p_stores)}"
    if ("装载提醒" in original) and ("装载提醒" not in polished):
        return False, "装载提醒行丢失"
    if not (len(original) * 0.6 <= len(polished) <= len(original) * 1.8):
        return False, f"长度异常：{len(original)} -> {len(polished)}"
    return True, ""


def _resolve_polish_config() -> Optional[dict]:
    """解析润色用 LLM 配置（环境变量约定与 agent/parser.py 一致）；不可用返回 None。"""
    if os.environ.get("CITYLOAD_DISABLE_LLM", "").strip().lower() in {"1", "true", "yes", "on"}:
        return None
    key = os.environ.get("CITYLOAD_LLM_API_KEY")
    if key:
        base = os.environ.get("CITYLOAD_LLM_BASE_URL", "https://api.deepseek.com")
    elif os.environ.get("DEEPSEEK_API_KEY"):
        key = os.environ["DEEPSEEK_API_KEY"]
        base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    elif os.environ.get("OPENAI_API_KEY"):
        key = os.environ["OPENAI_API_KEY"]
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    else:
        return None
    model = os.environ.get("CITYLOAD_LLM_MODEL") or ("deepseek-chat" if "deepseek" in base else "gpt-4o-mini")
    try:
        timeout = float(os.environ.get("CITYLOAD_LLM_TIMEOUT", "30"))
    except ValueError:
        timeout = 30.0
    return {"api_key": key, "base_url": base, "model": model, "timeout": timeout}


def _call_polish_llm(text: str, cfg: dict) -> str:
    """调用兼容 OpenAI 协议的 /chat/completions 做润色；异常向上抛出由上层兜底。"""
    url = cfg["base_url"].rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": _load_polish_prompt()},
            {"role": "user", "content": text},
        ],
        "temperature": 0.3,
        "stream": False,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['api_key']}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=cfg["timeout"]) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"]


def _load_polish_prompt() -> str:
    """读取润色提示词模板（带缓存）；文件不可读时退回内置模板。"""
    global _POLISH_PROMPT_CACHE
    if _POLISH_PROMPT_CACHE is None:
        try:
            _POLISH_PROMPT_CACHE = _POLISH_PROMPT_PATH.read_text(encoding="utf-8")
        except Exception:
            _POLISH_PROMPT_CACHE = _POLISH_BUILTIN
    return _POLISH_PROMPT_CACHE


# ---------------------------------------------------------------------------
# 手工演示入口：python agent/explainer.py [loading_plan.json] [routes.json]
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    plan_path = sys.argv[1] if len(sys.argv) > 1 else "output/loading_plan.json"
    routes_path = sys.argv[2] if len(sys.argv) > 2 else "output/routes.json"

    def _read(path: str) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[warn] 读取 {path} 失败：{e}")
            return {}

    result = gen_guide_text(_read(plan_path), _read(routes_path))
    print(json.dumps(result, ensure_ascii=False, indent=2))
