"""Per-image cost accounting for Google (Vertex) image models.

Prices are the official per-image rates in USD, keyed by output resolution tier
(0.5K/1K/2K/4K), as published on the Vertex AI pricing page in 2026-11.
The accumulator is process-wide so a batch job can report what it spent.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

# 官方价目：USD/张。同一张图 0.5K 与 1K 常同价，2K/4K 逐档上涨。
GOOGLE_IMAGE_PRICES: Dict[str, Dict[str, float]] = {
    "gemini-nano-banana-2.1": {"0.5K": 0.0336, "1K": 0.0336, "2K": 0.0504, "4K": 0.113},
    "gemini-3.1-flash-lite-image": {"0.5K": 0.0336, "1K": 0.0336},
    "gemini-3.1-flash-image": {"0.5K": 0.045, "1K": 0.067, "2K": 0.101, "4K": 0.151},
    "gemini-3-pro-image": {"0.5K": 0.134, "1K": 0.134, "2K": 0.134, "4K": 0.24},
    "gemini-2.5-flash-image": {"1K": 0.039},
}

_LOCK = threading.Lock()
_BY_MODEL: Dict[str, Dict[str, Any]] = {}
_TOTAL_USD = 0.0
_TOTAL_COUNT = 0


def normalize_model(model: str) -> str:
    """把 `publishers/google/models/x`、大小写混写统一成价目表里的键。"""
    text = str(model or "").strip().lower()
    if "/models/" in text:
        text = text.rsplit("/models/", 1)[-1]
    return text.rsplit("/", 1)[-1]


def image_price(model: str, image_size: str = "1K") -> Optional[float]:
    """单张价格（USD）。模型不在价目表里时返回 None，而不是猜一个数字。

    请求了价目表未覆盖的档位（例如 Lite 只有 1K）时，按该模型最高档计价，
    宁可高估也不低估账单。
    """
    entry = GOOGLE_IMAGE_PRICES.get(normalize_model(model))
    if not entry:
        return None
    tier = str(image_size or "1K").strip().upper()
    if tier in entry:
        return entry[tier]
    return max(entry.values())


def record_image_cost(*, model: str, image_size: str = "1K") -> Optional[float]:
    """记一次成功出图，返回这一张的价格（USD）。未知模型仍计数，但价格记 None。"""
    global _TOTAL_USD, _TOTAL_COUNT
    price = image_price(model, image_size)
    name = normalize_model(model)
    with _LOCK:
        _TOTAL_COUNT += 1
        bucket = _BY_MODEL.setdefault(name, {"count": 0, "usd": 0.0})
        bucket["count"] += 1
        if price is not None:
            _TOTAL_USD += price
            bucket["usd"] += price
    return price


def cost_summary() -> Dict[str, Any]:
    """当前进程累计：张数、美元合计、按模型拆分。"""
    with _LOCK:
        return {
            "count": _TOTAL_COUNT,
            "usd": round(_TOTAL_USD, 4),
            "by_model": {
                model: {"count": bucket["count"], "usd": round(bucket["usd"], 4)}
                for model, bucket in _BY_MODEL.items()
            },
        }


def reset_costs() -> None:
    """清空累计（测试或新任务开始时用）。"""
    global _TOTAL_USD, _TOTAL_COUNT
    with _LOCK:
        _BY_MODEL.clear()
        _TOTAL_USD = 0.0
        _TOTAL_COUNT = 0
