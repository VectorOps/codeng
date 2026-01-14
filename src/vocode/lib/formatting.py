from __future__ import annotations


def format_int_compact(value: int) -> str:
    v = int(value or 0)
    abs_v = abs(v)
    suffix = ""
    scaled = float(v)
    if abs_v >= 1_000_000_000:
        scaled = v / 1_000_000_000.0
        suffix = "B"
    elif abs_v >= 1_000_000:
        scaled = v / 1_000_000.0
        suffix = "M"
    elif abs_v >= 1_000:
        scaled = v / 1_000.0
        suffix = "k"
    else:
        return str(v)
    text = f"{scaled:.1f}"
    if text.endswith(".0"):
        text = text[:-2]
    return f"{text}{suffix}"


def format_cost_compact(value: float) -> str:
    v = float(value or 0.0)
    if v <= 0:
        return "0"
    if v >= 1000:
        return format_int_compact(int(v))
    if v >= 1:
        text = f"{v:.2f}"
    else:
        text = f"{v:.4f}"
    text = text.rstrip("0").rstrip(".")
    return text