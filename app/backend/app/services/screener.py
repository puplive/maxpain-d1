"""看板筛选：原子条件 + 组合器。

一个条件 = 窗口 + 左指标 + 行为 + 目标（指标/固定值）+ 聚合方式，
例如「近3日 + 价格区间 + 突破过 + Gamma峰值 + 任一日」。
多个条件默认 AND 叠加，也可以切换为 OR。
指标和行为都用白名单维护，后续加行为只改这里的注册表。
"""

from .data_source import load_screen_tail, load_symbols

# 左指标白名单：range 用当日最高/最低价，value 取单个数值字段
INDICATORS = [
    {"id": "price", "label": "价格区间", "kind": "range"},
    {"id": "close", "label": "收盘价", "kind": "value", "field": "c"},
    {"id": "mp", "label": "MP", "kind": "value", "field": "mp"},
    {"id": "bec", "label": "BEC", "kind": "value", "field": "bec"},
    {"id": "bep", "label": "BEP", "kind": "value", "field": "bep"},
    {"id": "gpk", "label": "Gamma峰值", "kind": "value", "field": "gpk"},
    {"id": "gex", "label": "GEX", "kind": "value", "field": "gex"},
    {"id": "oi_pcr", "label": "OI PCR", "kind": "value", "field": "oi_pcr"},
    {"id": "ivs", "label": "IV偏斜", "kind": "value", "field": "ivs"},
    {"id": "vr", "label": "量比", "kind": "value", "field": "vr"},
    {"id": "atm_iv", "label": "ATM IV", "kind": "value", "field": "atm_iv"},
]
_INDICATOR_MAP = {item["id"]: item for item in INDICATORS}

# 行为白名单：kinds 表示适用的左指标类型（range 价格区间 / value 单值）
BEHAVIORS = [
    {"id": "break", "label": "突破过（区间覆盖）", "kinds": ["range"]},
    {"id": "range_above", "label": "站上（区间在目标上方）", "kinds": ["range"]},
    {"id": "range_below", "label": "跌破（区间在目标下方）", "kinds": ["range"]},
    {"id": "above", "label": "站上", "kinds": ["value"]},
    {"id": "below", "label": "跌破", "kinds": ["value"]},
    {"id": "cross_up", "label": "上穿", "kinds": ["value"]},
    {"id": "cross_down", "label": "下穿", "kinds": ["value"]},
]
_BEHAVIOR_MAP = {item["id"]: item for item in BEHAVIORS}

AGGREGATES = [
    {"id": "any", "label": "任一日"},
    {"id": "last", "label": "最近一日"},
    {"id": "all", "label": "每日"},
]

WINDOWS = [1, 2, 3, 5, 10, 20]

_BEHAVIOR_VERB = {
    "break": "区间覆盖",
    "range_above": "站上",
    "range_below": "跌破",
    "above": "站上",
    "below": "跌破",
    "cross_up": "上穿",
    "cross_down": "下穿",
}


def screen_meta() -> dict:
    """返回前端条件构建器需要的选项。"""
    return {
        "windows": WINDOWS,
        "aggregates": AGGREGATES,
        "indicators": [
            {key: item[key] for key in ("id", "label", "kind")}
            for item in INDICATORS
        ],
        "behaviors": [
            {"id": item["id"], "label": item["label"], "kinds": item["kinds"]}
            for item in BEHAVIORS
        ],
    }


def validate_conditions(conditions: list[dict]) -> None:
    """校验条件白名单；不合法时抛 ValueError（接口转成 400）。"""
    for cond in conditions:
        left = _INDICATOR_MAP.get(cond["left"])
        if left is None:
            raise ValueError(f"unknown indicator: {cond['left']}")

        behavior = _BEHAVIOR_MAP.get(cond["behavior"])
        if behavior is None:
            raise ValueError(f"unknown behavior: {cond['behavior']}")
        if left["kind"] not in behavior["kinds"]:
            raise ValueError(
                f"behavior {behavior['id']} does not apply to {left['id']}"
            )

        target = cond["target"]
        if target["type"] == "indicator":
            target_ind = _INDICATOR_MAP.get(str(target["value"]))
            if target_ind is None or target_ind["kind"] != "value":
                raise ValueError(f"invalid target indicator: {target['value']}")
        elif not isinstance(target["value"], (int, float)):
            raise ValueError("fixed target must be a number")


def _target_of(cond: dict, row: dict):
    """取条件在某一交易日的目标值；指标缺失时返回 None。"""
    target = cond["target"]
    if target["type"] == "value":
        return float(target["value"])
    field = _INDICATOR_MAP[target["value"]]["field"]
    value = row.get(field)
    return float(value) if value is not None else None


def _target_label(cond: dict) -> str:
    target = cond["target"]
    if target["type"] == "value":
        return ""
    return _INDICATOR_MAP[target["value"]]["label"]


def _fmt(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _day_match(cond: dict, rows: list[dict], index: int) -> bool:
    """判断窗口内的第 index 天是否满足条件。"""
    row = rows[index]
    target = _target_of(cond, row)
    if target is None:
        return False

    left = _INDICATOR_MAP[cond["left"]]
    behavior = cond["behavior"]

    if left["kind"] == "range":
        low, high = row.get("l"), row.get("h")
        if low is None or high is None:
            return False
        if behavior == "break":
            return float(low) <= target <= float(high)
        if behavior == "range_above":
            return float(low) > target
        return float(high) < target

    value = row.get(left["field"])
    if value is None:
        return False
    value = float(value)

    if behavior == "above":
        return value > target
    if behavior == "below":
        return value < target

    # 上穿/下穿需要和前一交易日比较
    if index == 0:
        return False
    prev = rows[index - 1]
    prev_value = prev.get(left["field"])
    prev_target = _target_of(cond, prev)
    if prev_value is None or prev_target is None:
        return False
    prev_value = float(prev_value)
    if behavior == "cross_up":
        return prev_value <= prev_target and value > target
    return prev_value >= prev_target and value < target


def _describe(cond: dict, rows: list[dict], index: int) -> str:
    """命中说明，例如「11-06 区间覆盖 Gamma峰值 3200」。"""
    row = rows[index]
    target = _target_of(cond, row) or 0.0
    target_label = _target_label(cond)
    target_text = f"{target_label} {_fmt(target)}" if target_label else _fmt(target)

    left = _INDICATOR_MAP[cond["left"]]
    verb = _BEHAVIOR_VERB[cond["behavior"]]
    if left["kind"] == "range":
        return f"{str(row['d'])[5:]} 价格{verb} {target_text}"
    return f"{str(row['d'])[5:]} {left['label']}{verb} {target_text}"


def _eval_condition(cond: dict, rows: list[dict]) -> str | None:
    """在最近 window 根日线上评估一个条件；命中返回说明。"""
    window = int(cond["window"])
    n = len(rows)
    if n == 0:
        return None
    start = max(0, n - window)
    indices = list(range(start, n))
    if not indices:
        return None

    aggregate = cond["aggregate"]
    if aggregate == "last":
        indices = [n - 1]
    elif aggregate == "any":
        # 从新到旧，说明里展示最近一次命中
        indices = list(reversed(indices))

    hits = [index for index in indices if _day_match(cond, rows, index)]
    if aggregate == "all":
        if len(hits) != len(indices):
            return None
        note = _describe(cond, rows, indices[-1])
        return f"{note}（连续{len(indices)}日）"
    if not hits:
        return None
    return _describe(cond, rows, hits[0])


def screen(
    conditions: list[dict],
    combine: str = "and",
    symbols: list[str] | None = None,
) -> list[dict]:
    """在 symbols 范围内筛选（为空表示全部品种）；conditions 已通过校验。"""
    max_window = max(int(cond["window"]) for cond in conditions)
    # 多读一根，供上穿/下穿与前一交易日比较
    tail = max_window + 1
    scope = set(symbols or [])

    matched = []
    for info in load_symbols():
        if scope and info["symbol"] not in scope:
            continue
        rows = load_screen_tail(info["symbol"], tail)
        if not rows:
            continue

        results = [_eval_condition(cond, rows) for cond in conditions]
        if combine == "and":
            if any(note is None for note in results):
                continue
        elif all(note is None for note in results):
            continue

        notes = [note for note in results if note]
        matched.append(
            {
                "symbol": info["symbol"],
                "name": info["name"],
                "date": rows[-1]["d"],
                "note": "；".join(notes),
            }
        )

    matched.sort(key=lambda item: item["symbol"])
    return matched
