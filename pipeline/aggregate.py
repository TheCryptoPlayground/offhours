"""Bars to facts: USD volume by market date, session, venue, issuer, ticker and counterpart.

Works one daily file at a time, so memory stays flat as history grows.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from . import calendar as K
from . import config as C
from . import store

TOKEN_KEYS = ["market_date", "session", "source", "venue_type", "family", "underlying", "counterpart"]
STOCK_KEYS = ["market_date", "session", "underlying"]


def _sessions(df: pd.DataFrame) -> pd.DataFrame:
    code, mdate = K.classify(df.ts.values)
    df = df.assign(session=np.asarray(C.SESSIONS)[code], market_date=mdate)
    return df


def token_facts(markets: pd.DataFrame | None = None) -> pd.DataFrame:
    markets = store.load_markets() if markets is None else markets
    meta = markets[["market_id", "venue_type", "family", "underlying", "counterpart"]]
    parts = []
    for source, _, path in store.bar_files(exclude=("stocks",)):
        df = pd.read_parquet(path, columns=["market_id", "ts", "vol_usd"])
        df = df[df.vol_usd > 0]
        if df.empty:
            continue
        df = _sessions(df).merge(meta, on="market_id", how="left")
        df["source"] = source
        for col, fill in (("venue_type", "dex" if source.startswith("dex-") else "cex"), ("family", "other"),
                          ("underlying", ""), ("counterpart", "usd")):
            df[col] = df[col].fillna(fill)
        parts.append(df.groupby(TOKEN_KEYS, dropna=False).vol_usd.sum())
    if not parts:
        return pd.DataFrame(columns=TOKEN_KEYS + ["vol_usd"])
    out = pd.concat(parts).groupby(level=list(range(len(TOKEN_KEYS)))).sum().reset_index()
    return out.sort_values(TOKEN_KEYS).reset_index(drop=True)


def stock_facts() -> pd.DataFrame:
    parts = []
    for _, _, path in store.bar_files(sources=["stocks"]):
        df = pd.read_parquet(path, columns=["market_id", "ts", "vol_usd"])
        df = df[df.vol_usd > 0]
        if df.empty:
            continue
        df = _sessions(df)
        df["underlying"] = df.market_id.str.removeprefix("stocks:")
        parts.append(df.groupby(STOCK_KEYS).vol_usd.sum())
    if not parts:
        return pd.DataFrame(columns=STOCK_KEYS + ["vol_usd"])
    out = pd.concat(parts).groupby(level=[0, 1, 2]).sum().reset_index()
    return out.sort_values(STOCK_KEYS).reset_index(drop=True)


def week_profile(markets: pd.DataFrame, end_ts: int, days: int) -> dict:
    """Average USD volume per New York half hour over the last `days` days (rows Mon..Sun)."""
    # Define the window in New York calendar days, not fixed 24-hour spans. A local day
    # is 23 or 25 hours at a DST transition; fixed subtraction otherwise drops or adds
    # an hour at the beginning of a profile that crosses the clock change.
    local_end = pd.Timestamp(end_ts - 1, unit="s", tz="UTC").tz_convert(C.MARKET_TZ).date()
    local_start = local_end - timedelta(days=days - 1)
    local_days = pd.date_range(local_start, local_end, freq="D")
    start_ts = int(pd.Timestamp(local_start).tz_localize(C.MARKET_TZ).timestamp())
    utc_days = pd.date_range(pd.Timestamp(start_ts, unit="s").normalize(), pd.Timestamp(end_ts - 1, unit="s").normalize(), freq="D").strftime("%Y-%m-%d").tolist()
    weekday_counts = np.bincount(local_days.dayofweek, minlength=7)
    weekday_counts = np.maximum(weekday_counts, 1)
    head = set(markets.market_id[markets.counterpart.isin(C.HEADLINE_COUNTERPARTS)])
    out = {}
    for name, sources, exclude in (("token", None, ("stocks",)), ("stock", ["stocks"], ())):
        df = store.read_days(sources, utc_days, exclude=exclude, columns=["market_id", "ts", "vol_usd"])
        df = df[(df.ts >= start_ts) & (df.ts < end_ts)]
        if name == "token":
            df = df[df.market_id.isin(head)]
        grid = np.zeros((7, 48))
        if len(df):
            dow, slot = K.week_slots(df.ts.values)
            np.add.at(grid, (dow, slot), df.vol_usd.values)
        out[name] = (grid / weekday_counts[:, None]).round(0).tolist()
    out["template"] = K.week_template()
    out["days"] = days
    return out
