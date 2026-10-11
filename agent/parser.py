# -*- coding: utf-8 -*-
"""agent/parser.py —— T21 LLM 订单需求解析（窗口D）

接口（docs/interfaces.md v1.0 第 2.6 节，签名已冻结，不得更改）：
    parse_requirement(text: str) -> dict
        自然语言 → orders 片段 JSON；解析失败返回 {"error": "..."}。不得抛出异常。
    load_structured_fallback(path: str) -> dict
        旁路：直接读取结构化 orders.json。

设计要点
--------
1. 三级降级，绝不抛异常，**绝不让 LLM 成为主流程必经环节**：
     ① LLM 解析（仅当环境存在可用 API Key 且未被禁用时尝试）——调用兼容
        OpenAI 协议的 /chat/completions 接口（默认适配 DeepSeek）；输出必须
        通过 JSON schema 校验；
     ② 规则 + 正则轻量解析（离线兜底，也是单测覆盖的主路径）；
     ③ 返回 {"error": "..."}。
   默认模式下 LLM 任何失败（无 Key / 超时 / 报错 / schema 不合法）都会自动
   回退到规则解析；设置 CITYLOAD_LLM_STRICT=1 时对齐派发单要求：LLM 不可用
   直接返回 {"error": "llm_unavailable"}。
2. 主流程默认走旁路 load_structured_fallback（读取结构化 orders.json），
   LLM 解析仅作为可选增强演示。
3. 规则解析器会尝试匹配 data/orders.json 的 SKU 库（只读、失败静默降级），
   自动补全重量 / 尺寸 / 堆叠属性等物理参数；匹配不到时物理字段留空并记入
   missing_fields 标注"待补"。

环境变量
--------
- CITYLOAD_LLM_API_KEY / CITYLOAD_LLM_BASE_URL / CITYLOAD_LLM_MODEL
      显式指定 LLM（最高优先级；BASE_URL 默认 https://api.deepseek.com）
- DEEPSEEK_API_KEY     自动适配 https://api.deepseek.com（deepseek-chat）
- OPENAI_API_KEY       自动适配 OPENAI_BASE_URL 或 https://api.openai.com/v1
- CITYLOAD_DISABLE_LLM=1   强制禁用 LLM，直接走规则解析（单测 / 离线演示）
- CITYLOAD_LLM_STRICT=1    严格模式：LLM 不可用时不回退，返回 llm_unavailable
- CITYLOAD_LLM_TIMEOUT     单次请求超时秒数，默认 30

返回结构说明
------------
成功时返回**单个 order 结构**（同 data/orders.json 的 orders[i]），其中自然
语言无法确定的值（order_id / store_id / lat / lng 等）置 null，并额外附带
两个演示标注字段（下游可直接剥离）：
    missing_fields: 待补字段清单，如 ["order_id", "store_id", "lat", ...]
    parse_meta:     解析元信息（来源 llm/rule/rule_fallback、置信度、原文等）
"""

from __future__ import annotations

import json
import os
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

MAX_INPUT_CHARS = 2000          # 超长输入直接拒绝（防异常输入拖垮解析）
DEFAULT_SERVICE_MINUTES = 15    # 未提及卸货时长时的默认值
DEFAULT_MAX_STACK_LAYERS = 6    # 未匹配到 SKU 库时的保守默认堆叠层数

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "parse_requirement.txt"
# 内置精简提示词：模板文件不可读时的最后兜底（保证模块永不因缺文件而崩溃）
_BUILTIN_PROMPT = (
    "你是物流订单解析助手。把用户的中文下单需求解析为一个订单 JSON 对象，"
    "包含字段：store_name, time_window([\"HH:MM\",\"HH:MM\"] 或 null), "
    "service_minutes(整数或 null), items(数组，每项含 name, qty 正整数, "
    "fragile 布尔, stackable 布尔, max_stack_layers 整数)。"
    "无法确定的字段填 null，禁止编造。只输出 JSON，不要任何其他文字。"
)

# ---------------------------------------------------------------------------
# 正则与词表（规则解析器）
# ---------------------------------------------------------------------------

_UNITS = "箱|件|盒|个|包|袋|瓶|桶|托|罐|支"
# 名称终止边界：连接词 / 标点 / 空白 / 运送动词（防止把"送到XX"吞进货物名）
_NAME_BOUNDARY = (
    r"(?=和|、|，|,|。|；|;|以及|还有|另加|加上|再加|共计|以及|送到|送至|送往|"
    r"配送|发往|送去|送|给|\s|$)"
)
# 顺序 A：数量 + 单位 + 名称（如"5 箱感冒灵"）
_RE_ITEM_A = re.compile(rf"(\d{{1,4}})\s*({_UNITS})\s*([\u4e00-\u9fa5A-Za-z]{{1,20}}?){_NAME_BOUNDARY}")
# 顺序 B：名称 + 数量 + 单位（如"板蓝根颗粒 30 箱"）
_RE_ITEM_B = re.compile(rf"([\u4e00-\u9fa5A-Za-z]{{2,20}}?)\s*(\d{{1,4}})\s*({_UNITS})")

# 门店名：模式1 带"给/送到"前缀；模式2 带"补货/进货"后缀；模式3 全文宽松兜底
_RE_STORE_1 = re.compile(
    r"(?:给|送到|送至|配送至|交付|送往)\s*([\u4e00-\u9fa5A-Za-z0-9]{2,20}?)"
    r"(药房|药店|大药房|连锁店|诊所|医院|超市|便利店|门店)"
)
_RE_STORE_2 = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9]{2,20}?)"
    r"(药房|药店|大药房|连锁店|诊所|医院|超市|便利店|门店)"
    r"\s*(?:需要|要|急需)?\s*(?:补货|进货|送货|订货|下单)"
)
_RE_STORE_3 = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9]{2,20}?)(药房|药店|大药房|连锁店|诊所|医院|超市|便利店)"
)

# 时间窗
_RE_TIME_RANGE = re.compile(r"(\d{1,2})\s*点\s*(?:到|至|-|~)\s*(\d{1,2})\s*点")
_RE_TIME_BEFORE = re.compile(r"(\d{1,2})\s*点\s*(?:前|之前|以前)")
# 服务时长："卸货大约需要 25 分钟"
_RE_SERVICE = re.compile(r"(?:卸货|卸车|服务|停留|交接)[^0-9]{0,8}(\d{1,3})\s*分钟")

# 搬运属性关键词
_FRAGILE_WORDS = ("易碎", "轻放", "轻拿", "防震", "勿压", "不可挤压", "小心搬运")
_STACKABLE_OFF_WORDS = ("不可堆叠", "不能堆叠", "禁止堆叠", "不得堆叠")

_RE_HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")


# ---------------------------------------------------------------------------
# 公共接口（契约冻结）
# ---------------------------------------------------------------------------

def parse_requirement(text: str) -> dict:
    """自然语言 → orders 片段 JSON；解析失败返回 {"error": "..."}。不得抛出异常。

    降级链：LLM（若可用）→ 规则解析（离线兜底）→ {"error": ...}。
    默认模式下 LLM 的任何失败都不会中断流程；严格模式（CITYLOAD_LLM_STRICT=1）
    下 LLM 不可用返回 {"error": "llm_unavailable"}（对齐派发单要求）。
    """
    try:
        # ---- 0. 输入防护：任何异常输入都返回 error，绝不抛异常 ----
        if not isinstance(text, str):
            return {"error": "invalid_input", "detail": "text 必须为字符串"}
        s = text.strip()
        if not s:
            return {"error": "invalid_input", "detail": "text 为空"}
        if len(s) > MAX_INPUT_CHARS:
            return {
                "error": "invalid_input",
                "detail": f"text 超长（{len(s)} > {MAX_INPUT_CHARS} 字符），已拒绝",
            }

        cfg = _resolve_llm_config()
        strict = _env_truthy("CITYLOAD_LLM_STRICT")
        llm_error: Optional[str] = None

        # ---- 1. LLM 路径（可选增强，不是必经环节） ----
        if cfg is not None:
            order, llm_error = _try_llm(s, cfg)
            if order is not None:
                return _finalize(order, source="llm", text=s)
            if strict:
                # 严格模式：LLM 失败不回退（对齐派发单：无 Key/超时/报错 → llm_unavailable）
                return {"error": "llm_unavailable", "detail": llm_error or "LLM 调用失败"}
        elif strict:
            return {"error": "llm_unavailable", "detail": "未配置 LLM API Key（或已禁用）"}

        # ---- 2. 规则解析路径（离线兜底 / 主演示路径） ----
        order = _rule_parse(s)
        if order is not None:
            source = "rule_fallback" if cfg is not None else "rule"
            return _finalize(order, source=source, text=s, llm_error=llm_error)

        # ---- 3. 全部失败 ----
        err: dict[str, Any] = {
            "error": "parse_failed",
            "detail": "无法从文本中解析出有效订单（未识别到货物与数量）",
            "raw_text": s[:200],
        }
        if llm_error:
            err["llm_error"] = llm_error
        return err
    except Exception as exc:  # 终极防线：任何未预期异常一律转为 error 返回
        return {"error": "internal_error", "detail": f"{type(exc).__name__}: {exc}"}


def load_structured_fallback(path: str) -> dict:
    """旁路：直接读取结构化 orders.json。主流程默认走此路径。

    读取失败（文件缺失 / JSON 非法 / 结构不对）返回 {"error": ...}，绝不抛异常。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"error": "file_not_found", "detail": f"文件不存在: {path}"}
    except json.JSONDecodeError as exc:
        return {"error": "invalid_json", "detail": f"{path}: {exc}"}
    except OSError as exc:
        return {"error": "io_error", "detail": f"{path}: {exc}"}

    if not isinstance(data, dict) or not isinstance(data.get("orders"), list):
        return {"error": "invalid_structure", "detail": "缺少 orders 列表字段"}
    return data


# ---------------------------------------------------------------------------
# LLM 路径
# ---------------------------------------------------------------------------

def _env_truthy(name: str) -> bool:
    """判断环境变量是否为真值（1/true/yes/on，大小写不敏感）。"""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_llm_config() -> Optional[dict]:
    """解析 LLM 配置；无可用 Key 或已禁用时返回 None。每次调用实时读取环境变量。"""
    if _env_truthy("CITYLOAD_DISABLE_LLM"):
        return None
    timeout = 30.0
    try:
        timeout = float(os.environ.get("CITYLOAD_LLM_TIMEOUT", "30"))
    except ValueError:
        pass

    key = os.environ.get("CITYLOAD_LLM_API_KEY")
    if key:
        return {
            "api_key": key,
            "base_url": os.environ.get("CITYLOAD_LLM_BASE_URL", "https://api.deepseek.com"),
            "model": os.environ.get("CITYLOAD_LLM_MODEL", "deepseek-chat"),
            "timeout": timeout,
        }
    if os.environ.get("DEEPSEEK_API_KEY"):
        return {
            "api_key": os.environ["DEEPSEEK_API_KEY"],
            "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            "model": os.environ.get("CITYLOAD_LLM_MODEL", "deepseek-chat"),
            "timeout": timeout,
        }
    if os.environ.get("OPENAI_API_KEY"):
        return {
            "api_key": os.environ["OPENAI_API_KEY"],
            "base_url": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            "model": os.environ.get("CITYLOAD_LLM_MODEL", "gpt-4o-mini"),
            "timeout": timeout,
        }
    return None


def _try_llm(text: str, cfg: dict) -> tuple[Optional[dict], Optional[str]]:
    """尝试 LLM 解析。成功返回 (order, None)；失败返回 (None, 错误说明)。内部全部捕获。"""
    try:
        content = _call_llm(text, cfg)
    except socket.timeout:
        return None, "timeout: LLM 请求超时"
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        return None, f"http_error {exc.code}: {body}"
    except urllib.error.URLError as exc:
        return None, f"url_error: {exc.reason}"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"

    try:
        obj = _extract_json_object(content)
    except Exception as exc:
        return None, f"json_extract: {exc}（原始回复前 120 字: {content.strip()[:120]}）"

    if isinstance(obj, dict) and obj.get("error"):
        return None, f"llm_reported: {obj.get('error')}"

    order = _normalize_order(obj) if isinstance(obj, dict) else None
    if order is None:
        return None, "schema_invalid: LLM 输出不是合法的订单对象"
    errors = _validate_order(order)
    if errors:
        return None, "schema_invalid: " + "; ".join(errors[:3])
    _augment_with_sku(order)  # 与规则路径对齐：SKU 库补全物理参数
    return order, None


def _call_llm(text: str, cfg: dict) -> str:
    """调用兼容 OpenAI 协议的 /chat/completions，返回回复文本。异常向上抛出。"""
    url = cfg["base_url"].rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": _load_prompt()},
            {"role": "user", "content": text},
        ],
        "temperature": 0.0,
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


_PROMPT_CACHE: Optional[str] = None


def _load_prompt() -> str:
    """读取提示词模板（带缓存）；文件不可读时退回内置精简模板。"""
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        try:
            _PROMPT_CACHE = _PROMPT_PATH.read_text(encoding="utf-8")
        except Exception:
            _PROMPT_CACHE = _BUILTIN_PROMPT
    return _PROMPT_CACHE


def _extract_json_object(content: str) -> dict:
    """从 LLM 回复中提取 JSON 对象：兼容裸 JSON / markdown 围栏 / 前后夹带文字。"""
    s = content.strip()
    m = re.search(r"```(?:json)?\s*(.+?)\s*```", s, flags=re.S)
    if m:
        s = m.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j > i:
        return json.loads(s[i : j + 1])
    raise ValueError("回复中未找到 JSON 对象")


# ---------------------------------------------------------------------------
# 规则解析路径（离线兜底）
# ---------------------------------------------------------------------------

def _rule_parse(text: str) -> Optional[dict]:
    """规则 + 正则轻量解析。解析不出有效订单时返回 None（由上层降级）。"""
    items = _extract_items(text)
    if not items:
        return None

    store_name = _extract_store_name(text)
    time_window = _extract_time_window(text)
    service_minutes = _extract_service_minutes(text)

    fragile_all = any(w in text for w in _FRAGILE_WORDS)
    stackable_off = any(w in text for w in _STACKABLE_OFF_WORDS)

    parsed_items = []
    for name, qty in items:
        sku = _match_sku(name)
        item: dict[str, Any] = {
            "sku": sku["sku"] if sku else None,
            "name": sku["name"] if sku else name,
            "qty": int(qty),
            "weight_kg": sku["weight_kg"] if sku else None,
            "length_cm": sku["length_cm"] if sku else None,
            "width_cm": sku["width_cm"] if sku else None,
            "height_cm": sku["height_cm"] if sku else None,
            "fragile": bool(fragile_all or (sku["fragile"] if sku else False)),
            "stackable": False if stackable_off else bool(sku["stackable"] if sku else True),
            "max_stack_layers": 1 if stackable_off else int(sku["max_stack_layers"] if sku else DEFAULT_MAX_STACK_LAYERS),
        }
        parsed_items.append(item)

    order = {
        "order_id": None,
        "store_id": None,
        "store_name": store_name,
        "lat": None,
        "lng": None,
        "time_window": time_window,
        "service_minutes": service_minutes,
        "items": parsed_items,
    }
    # 规则路径产出同样必须通过 schema 校验（防御性：绝不放行非法结构）
    if _validate_order(order):
        return None
    return order


def _extract_items(text: str) -> list[tuple[str, int]]:
    """提取「货物 + 数量」。先扫"数量+单位+名称"式，再扫"名称+数量+单位"式，
    按字符区间去重，防止同一段文字被两种语序重复解析。"""
    found: list[tuple[str, int]] = []
    spans: list[tuple[int, int]] = []

    def _overlaps(span: tuple[int, int]) -> bool:
        return any(span[0] < e and s < span[1] for s, e in spans)

    for m in _RE_ITEM_A.finditer(text):
        qty = int(m.group(1))
        name = m.group(3).strip()
        if not name or qty < 1 or _overlaps(m.span()):
            continue
        found.append((name, qty))
        spans.append(m.span())

    for m in _RE_ITEM_B.finditer(text):
        qty = int(m.group(2))
        name = m.group(1).strip()
        if not name or qty < 1 or _overlaps(m.span()):
            continue
        found.append((name, qty))
        spans.append(m.span())

    return found


def _extract_store_name(text: str) -> Optional[str]:
    """提取门店名。三种模式依次尝试；去掉"XX区的"之类前缀，只留店名主体。"""
    for pattern in (_RE_STORE_1, _RE_STORE_2, _RE_STORE_3):
        m = pattern.search(text)
        if m:
            head = m.group(1).split("的")[-1]  # "杭州萧山的康健" → "康健"
            name = (head + m.group(2)).strip()
            if name:
                return name
    return None


def _hm(hour: int, minute: int = 0) -> str:
    """格式化 "HH:MM"。"""
    return f"{hour:02d}:{minute:02d}"


def _extract_time_window(text: str) -> Optional[list[str]]:
    """提取时间窗。支持"X 点到 Y 点"/"X 点前"/上午/中午/下午/晚上等表述。"""
    afternoon = ("下午" in text) or ("晚上" in text)

    m = _RE_TIME_RANGE.search(text)
    if m:
        h1, h2 = int(m.group(1)), int(m.group(2))
        if afternoon:
            if h1 < 12:
                h1 += 12
            if h2 < 12:
                h2 += 12
        if h2 <= h1:
            h2 = min(h1 + 4, 23)
        return [_hm(h1), _hm(h2)]

    m = _RE_TIME_BEFORE.search(text)
    if m:
        h = int(m.group(1))
        if afternoon and h < 12:
            h += 12
        start = max(h - 4, 8)
        if start >= h:
            start = max(h - 1, 0)
        return [_hm(start), _hm(h)]

    if "中午前" in text or "午前" in text:
        return ["08:00", "12:00"]
    if any(w in text for w in ("上午", "早上", "早晨")):
        return ["08:00", "12:00"]
    if "中午" in text:
        return ["12:00", "14:00"]
    if "下午" in text:
        return ["14:00", "18:00"]
    if "晚上" in text:
        return ["18:00", "21:00"]
    return None


def _extract_service_minutes(text: str) -> Optional[int]:
    """提取卸货/服务时长（分钟）。"""
    m = _RE_SERVICE.search(text)
    if m:
        v = int(m.group(1))
        if 1 <= v <= 300:
            return v
    return None


# ---- SKU 库匹配（只读 data/orders.json，失败静默降级） ----

_SKU_INDEX_CACHE: Optional[dict] = None


def _sku_index_path() -> Path:
    """SKU 索引数据源：项目根 / data/orders.json。"""
    return Path(__file__).resolve().parents[1] / "data" / "orders.json"


def _load_sku_index() -> dict:
    """懒加载 SKU 名称库（首次调用时读取一次并缓存）。读不到返回空库。"""
    global _SKU_INDEX_CACHE
    if _SKU_INDEX_CACHE is not None:
        return _SKU_INDEX_CACHE
    index: dict[str, dict] = {}
    try:
        with open(_sku_index_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        for order in data.get("orders", []):
            for it in order.get("items", []):
                name = it.get("name")
                if isinstance(name, str) and name not in index:
                    index[name] = {
                        "sku": it.get("sku"),
                        "name": name,
                        "weight_kg": it.get("weight_kg"),
                        "length_cm": it.get("length_cm"),
                        "width_cm": it.get("width_cm"),
                        "height_cm": it.get("height_cm"),
                        "fragile": bool(it.get("fragile", False)),
                        "stackable": bool(it.get("stackable", True)),
                        "max_stack_layers": int(it.get("max_stack_layers", DEFAULT_MAX_STACK_LAYERS)),
                    }
    except Exception:
        index = {}
    _SKU_INDEX_CACHE = index
    return index


def _match_sku(name: str) -> Optional[dict]:
    """按名称匹配 SKU 库：精确 > 前缀 > 包含；多候选取名称最短者（最通用）。"""
    index = _load_sku_index()
    if not index:
        return None
    if name in index:
        return index[name]
    candidates = [v for k, v in index.items() if k.startswith(name)]
    if not candidates:
        candidates = [v for k, v in index.items() if name in k]
    if not candidates:
        return None
    return min(candidates, key=lambda v: (len(v["name"]), str(v["sku"])))


def _augment_with_sku(order: dict) -> None:
    """LLM 结果落地前用 SKU 库增强（与规则路径行为对齐）：

    - 名称能命中 SKU 库时：标准化为库全名、补全 sku / 重量 / 尺寸；
    - 承压属性融合：fragile 取"或"（任一说易碎即可）、stackable 取"与"
      （任一说不可堆叠即不可）、max_stack_layers 为解析默认值时用库值；
    - 未命中则保持 LLM 原值不变。只读、失败静默。
    """
    for it in order.get("items", []):
        sku = _match_sku(it.get("name") or "")
        if not sku:
            continue
        it["sku"] = sku["sku"]
        it["name"] = sku["name"]
        for k in ("weight_kg", "length_cm", "width_cm", "height_cm"):
            if it.get(k) is None:
                it[k] = sku[k]
        it["fragile"] = bool(it.get("fragile") or sku["fragile"])
        it["stackable"] = bool(it.get("stackable") and sku["stackable"])
        if it.get("max_stack_layers") in (None, DEFAULT_MAX_STACK_LAYERS):
            it["max_stack_layers"] = sku["max_stack_layers"]


# ---------------------------------------------------------------------------
# 规范化 / 校验 / 收尾
# ---------------------------------------------------------------------------

def _coerce_int(value: Any) -> Optional[int]:
    """宽容转 int："5"/5.0/5 → 5；失败返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        return int(value.strip())
    return None


def _coerce_bool(value: Any, default: bool) -> bool:
    """宽容转 bool："true"/"是" 等；失败用默认值。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"true", "1", "是", "yes"}:
            return True
        if low in {"false", "0", "否", "no"}:
            return False
    return default


def _norm_hhmm(value: Any) -> Optional[str]:
    """规范化 "HH:MM"："8:00"/"8：00"（全角）→ "08:00"；失败返回 None。"""
    if not isinstance(value, str):
        return None
    s = value.strip().replace("：", ":")
    m = _RE_HHMM.match(s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return _hm(h, mi)


def _norm_time_window(value: Any) -> Optional[list[str]]:
    """规范化时间窗：支持 ["8:00","12:00"] / "08:00-12:00"；无法修复返回 None。"""
    if value is None:
        return None
    parts: Any = value
    if isinstance(value, str):
        parts = re.split(r"\s*(?:到|至|-|~|—)\s*", value)
    if isinstance(parts, (list, tuple)) and len(parts) == 2:
        a, b = _norm_hhmm(parts[0]), _norm_hhmm(parts[1])
        if a and b:
            return [a, b]
    return None


def _normalize_order(obj: dict) -> Optional[dict]:
    """把（LLM 或规则输出的）原始 dict 规范化为统一订单结构；结构不可修复返回 None。"""
    items_raw = obj.get("items")
    if not isinstance(items_raw, list):
        return None
    items = []
    for it in items_raw:
        if not isinstance(it, dict):
            continue
        name = it.get("name")
        qty = _coerce_int(it.get("qty"))
        if not isinstance(name, str) or not name.strip() or qty is None or qty < 1:
            continue
        items.append(
            {
                "sku": it.get("sku") if isinstance(it.get("sku"), str) else None,
                "name": name.strip(),
                "qty": qty,
                "weight_kg": it.get("weight_kg") if isinstance(it.get("weight_kg"), (int, float)) else None,
                "length_cm": it.get("length_cm") if isinstance(it.get("length_cm"), (int, float)) else None,
                "width_cm": it.get("width_cm") if isinstance(it.get("width_cm"), (int, float)) else None,
                "height_cm": it.get("height_cm") if isinstance(it.get("height_cm"), (int, float)) else None,
                "fragile": _coerce_bool(it.get("fragile"), False),
                "stackable": _coerce_bool(it.get("stackable"), True),
                "max_stack_layers": _coerce_int(it.get("max_stack_layers")) or DEFAULT_MAX_STACK_LAYERS,
            }
        )
    if not items:
        return None

    service = _coerce_int(obj.get("service_minutes"))
    if service is not None and service <= 0:
        service = None

    return {
        "order_id": obj.get("order_id") if isinstance(obj.get("order_id"), str) else None,
        "store_id": obj.get("store_id") if isinstance(obj.get("store_id"), str) else None,
        "store_name": obj.get("store_name").strip() if isinstance(obj.get("store_name"), str) else None,
        "lat": obj.get("lat") if isinstance(obj.get("lat"), (int, float)) else None,
        "lng": obj.get("lng") if isinstance(obj.get("lng"), (int, float)) else None,
        "time_window": _norm_time_window(obj.get("time_window")),
        "service_minutes": service,
        "items": items,
    }


def _validate_order(order: dict) -> list[str]:
    """JSON schema 校验：返回错误信息列表（空列表 = 通过）。"""
    errors: list[str] = []

    if not isinstance(order.get("items"), list) or not order["items"]:
        errors.append("items 必须为非空列表")
    else:
        for i, it in enumerate(order["items"]):
            if not isinstance(it.get("name"), str) or not it["name"].strip():
                errors.append(f"items[{i}].name 必须为非空字符串")
            qty = it.get("qty")
            if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1:
                errors.append(f"items[{i}].qty 必须为正整数")
            for k in ("fragile", "stackable"):
                if not isinstance(it.get(k), bool):
                    errors.append(f"items[{i}].{k} 必须为布尔值")
            msl = it.get("max_stack_layers")
            if not isinstance(msl, int) or isinstance(msl, bool) or msl < 1:
                errors.append(f"items[{i}].max_stack_layers 必须为 ≥1 的整数")

    tw = order.get("time_window")
    if tw is not None:
        if (
            not isinstance(tw, list)
            or len(tw) != 2
            or not all(isinstance(x, str) and _RE_HHMM.match(x) for x in tw)
        ):
            errors.append("time_window 必须为 [HH:MM, HH:MM]")
        elif tw[0] >= tw[1]:
            errors.append("time_window 结束时间必须晚于开始时间")

    for k in ("lat", "lng"):
        v = order.get(k)
        if v is not None and not isinstance(v, (int, float)):
            errors.append(f"{k} 必须为数值或 null")

    sm = order.get("service_minutes")
    if sm is not None and (not isinstance(sm, int) or isinstance(sm, bool) or sm <= 0):
        errors.append("service_minutes 必须为正整数或 null")

    return errors


def _collect_missing(order: dict) -> list[str]:
    """收集"待补"字段清单（自然语言无法确定的字段）。"""
    missing: list[str] = []
    for k in ("order_id", "store_id"):
        if not order.get(k):
            missing.append(k)
    for k in ("lat", "lng"):
        if order.get(k) is None:
            missing.append(k)
    if not order.get("store_name"):
        missing.append("store_name")
    if not order.get("time_window"):
        missing.append("time_window")
    for i, it in enumerate(order.get("items", [])):
        if it.get("sku") is None:
            missing.append(f"items[{i}].sku")
        for k in ("weight_kg", "length_cm", "width_cm", "height_cm"):
            if it.get(k) is None:
                missing.append(f"items[{i}].{k}")
    return missing


def _finalize(
    order: dict,
    *,
    source: str,
    text: str,
    llm_error: Optional[str] = None,
) -> dict:
    """收尾：补默认值、统计 SKU 匹配情况、生成 missing_fields 与 parse_meta。"""
    result = dict(order)

    defaulted_service = False
    if result.get("service_minutes") is None:
        result["service_minutes"] = DEFAULT_SERVICE_MINUTES
        defaulted_service = True

    missing = _collect_missing(result)

    total = len(result.get("items", []))
    matched = sum(1 for it in result.get("items", []) if it.get("sku"))

    notes: list[str] = []
    if total:
        notes.append(f"已匹配 SKU 库补全 {matched}/{total} 条货物物理参数")
    if defaulted_service:
        notes.append(f"service_minutes 未提及，默认 {DEFAULT_SERVICE_MINUTES} 分钟")
    if any("lat" == m or "lng" == m for m in missing):
        notes.append("坐标/编号类字段待系统按门店档案补充")

    confidence = 0.5
    if source == "llm":
        confidence = 0.9
    else:
        if result.get("store_name"):
            confidence += 0.15
        if result.get("time_window"):
            confidence += 0.10
        if total and matched == total:
            confidence += 0.15
        elif matched:
            confidence += 0.05
        confidence = round(min(confidence, 0.95), 2)

    meta: dict[str, Any] = {
        "source": source,   # llm | rule | rule_fallback
        "confidence": confidence,
        "raw_text": text[:200],
        "notes": notes,
    }
    if llm_error:
        meta["llm_error"] = llm_error

    result["missing_fields"] = missing
    result["parse_meta"] = meta
    return result


# ---------------------------------------------------------------------------
# 手工演示入口：python agent/parser.py "明天上午给杭州萧山的康健药房送 5 箱感冒灵…"
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    demo_text = " ".join(sys.argv[1:]) or (
        "明天上午给杭州萧山的康健药房送 5 箱感冒灵和 3 箱板蓝根，12 点前必须到，药品易碎要轻放"
    )
    print(json.dumps(parse_requirement(demo_text), ensure_ascii=False, indent=2))
