"""Shared pieces for every source: the market record, the bar schema, pagination helpers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable, Iterator

import numpy as np
import pandas as pd

BAR_COLUMNS = ["market_id", "ts", "bar_s", "open", "high", "low", "close", "vol_usd"]
Row = tuple  # (ts, open, high, low, close, vol_usd); ts = bar start, UTC epoch seconds


class SourceError(Exception):
    pass


@dataclass
class Market:
    market_id: str            # "binance:TSLABUSDT", "dex-solana:<pool>", "stocks:TSLA"
    source: str               # "binance", "dex-solana", "stocks"
    native: str               # exchange symbol or pool address
    base: str
    quote: str
    cg_id: str = ""
    family: str = "other"
    underlying: str = ""
    venue_type: str = "cex"   # "cex" | "dex" | "stock"
    counterpart: str = "usd"  # "usd" | "major" | "stock" | "other"
    network: str = ""
    token_side: str = ""      # DEX only: which side of the pool is the stock token
    bar_s: int = 1800
    vol24: float = 0.0
    discovered: str = field(default="")

    def row(self) -> dict:
        return asdict(self)


def frame(market_id: str, bar_s: int, rows: list[Row], start: int, end: int) -> pd.DataFrame:
    """Rows to the bar schema, keeping only complete bars that start in [start, end)."""
    if not rows:
        return pd.DataFrame(columns=BAR_COLUMNS)
    a = np.array(rows, dtype=float)
    df = pd.DataFrame({
        "market_id": market_id,
        "ts": a[:, 0].astype(np.int64),
        "bar_s": np.int32(bar_s),
        "open": a[:, 1], "high": a[:, 2], "low": a[:, 3], "close": a[:, 4],
        "vol_usd": np.nan_to_num(a[:, 5], nan=0.0),
    })
    df = df[(df.ts >= start) & (df.ts < end) & (df.ts % bar_s == 0)]
    return df.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)


def forward_windows(start: int, end: int, bar_s: int, per_call: int) -> Iterator[tuple[int, int]]:
    """Consecutive [a, b) windows covering [start, end), each at most per_call bars long."""
    step = bar_s * per_call
    a = start
    while a < end:
        b = min(end, a + step)
        yield a, b
        a = b


def backward(fetch_before: Callable[[int], list[Row]], start: int, end: int,
             max_calls: int = 5000) -> list[Row]:
    """Page backwards for APIs that return the bars before a timestamp.

    fetch_before(t) returns rows older than t (any order). Stops when a page reaches start,
    comes back empty, or stops moving.
    """
    out: list[Row] = []
    t = end
    for _ in range(max_calls):
        rows = [r for r in fetch_before(t) if r[0] < t]
        if not rows:
            break
        out.extend(r for r in rows if r[0] >= start)
        oldest = min(r[0] for r in rows)
        if oldest <= start or oldest >= t:
            break
        t = oldest
    return out


def floor_to(ts: float, step: int) -> int:
    return int(ts // step * step)
