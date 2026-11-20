"""Synthetic data for previewing the site. Not market data.

Writes a fake universe and bars into data-demo/ and builds site/data with meta.demo = true,
which makes the page show a banner. A real `python -m pipeline build` overwrites it.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from . import calendar as K
from . import config as C
from . import store
from .sources.base import BAR_COLUMNS, Market

TICKERS = {  # ticker: (token volume scale, stock dollar volume per RTH bar, start price)
    "TSLA": (9.0, 2.2e9, 410), "NVDA": (8.0, 2.6e9, 190), "CRCL": (6.0, 1.2e8, 95), "SPCX": (7.0, 4.0e8, 160),
    "MSTR": (4.0, 2.0e8, 260), "COIN": (3.0, 1.5e8, 300), "AAPL": (2.5, 9.0e8, 270), "QQQ": (5.0, 2.0e9, 610),
    "SPY": (3.0, 3.5e9, 680), "MU": (3.5, 3.0e8, 210), "HOOD": (2.0, 2.0e8, 120), "GOOGL": (2.0, 6.0e8, 280),
}
VENUES = [  # source, family, counterpart, scale, trades weekends
    ("binance", "bstocks", "usd", 1.0, True), ("bybit", "xstocks", "usd", 0.35, True),
    ("kraken", "xstocks", "usd", 0.30, False), ("okx", "ondo", "usd", 0.15, False),
    ("dex-robinhood", "robinhood", "usd", 1.2, True), ("dex-robinhood", "robinhood", "other", 0.4, True),
    ("dex-solana", "xstocks", "usd", 0.25, True), ("dex-bsc", "ondo", "usd", 0.3, False),
]
SESSION_MULT = {K.RTH: 1.0, K.PRE: 0.55, K.POST: 0.45, K.OVN: 0.4, K.CLOSED: 0.3}


def generate(days: int = 84, seed: int = 7) -> None:
    rng = np.random.default_rng(seed)
    C.DATA = C.ROOT / "data-demo"
    if C.DATA.exists():
        import shutil
        shutil.rmtree(C.DATA)
    end_day = pd.Timestamp.now(tz=C.MARKET_TZ).normalize()
    t1 = int(end_day.timestamp())
    t0 = t1 - days * 86400
    ts = np.arange(t0, t1, C.BAR_S, dtype=np.int64)
    code, _ = K.classify(ts)
    trading = code != K.CLOSED
    hour_et = pd.to_datetime(ts, unit="s", utc=True).tz_convert(C.MARKET_TZ).hour.values
    asia = np.isin(hour_et, [20, 21, 22, 23, 0, 1, 2, 3])  # extra activity during the Asian day

    # closures: one weekend shock per window, revealed to the token during the closure and
    # to the stock at the next open
    W = K.closure_windows()
    W = W[(W.start >= t0) & (W.next_open < t1)]

    markets, bars, stock = [], [], []
    for u, (scale, stock_dv, p0) in TICKERS.items():
        steps = rng.normal(0, 0.004, len(ts)) * trading      # the stock moves only in session
        token_steps = steps.copy()
        for w in W.itertuples():
            news = rng.normal(0, 0.025)
            inside = np.nonzero((ts >= w.start) & (ts < w.end - 2 * 3600))[0]
            if len(inside):
                token_steps[inside] += news * 0.9 / len(inside) + rng.normal(0, 0.0015, len(inside))
            reopen = np.nonzero(ts >= w.end)[0]
            if len(reopen):
                k = reopen[0]  # the stock absorbs the news once it trades again (overnight/pre-market)
                steps[k] += news + rng.normal(0, 0.008)
                token_steps[k] += steps[: k + 1].sum() - token_steps[: k + 1].sum()
        price = p0 * np.exp(np.cumsum(steps))
        tprice = p0 * np.exp(np.cumsum(token_steps))

        mult = np.vectorize(SESSION_MULT.get)(code) * (1 + 0.6 * asia)
        for src, fam, cp, vscale, weekends in VENUES:
            active = np.ones(len(ts), bool) if weekends else trading
            vol = 2.0e4 * scale * vscale * mult * rng.lognormal(0, 0.7, len(ts)) * active
            mid = f"{src}:{u}{fam[:2].upper()}{cp}"
            net = src.removeprefix("dex-") if src.startswith("dex-") else ""
            markets.append(Market(market_id=mid, source=src, native=mid.split(":")[1], base=u, quote="USD",
                                  cg_id=f"demo-{u}-{fam}", family=fam, underlying=u,
                                  venue_type="dex" if net else "cex", counterpart=cp, network=net,
                                  token_side="base", bar_s=C.BAR_S, vol24=float(vol[-48:].sum()), discovered="demo"))
            noise = 1 + rng.normal(0, 0.0008, len(ts))
            bars.append(pd.DataFrame({"market_id": mid, "ts": ts, "bar_s": C.BAR_S, "open": tprice * noise,
                                      "high": tprice * noise * 1.001, "low": tprice * noise * 0.999,
                                      "close": tprice * noise, "vol_usd": vol.round(2)})[lambda d: d.vol_usd > 0])
        sv = np.where(code == K.RTH, stock_dv, np.where(np.isin(code, [K.PRE, K.POST]), stock_dv * 0.02, 0.0))
        sv = sv * rng.lognormal(0, 0.3, len(ts))
        stock.append(pd.DataFrame({"market_id": f"stocks:{u}", "ts": ts, "bar_s": C.BAR_S,
                                   "open": np.r_[p0, price[:-1]], "high": price * 1.001, "low": price * 0.999,
                                   "close": price, "vol_usd": sv})[lambda d: d.vol_usd > 0])

    allbars = pd.concat(bars, ignore_index=True)
    for src, part in allbars.groupby(allbars.market_id.str.split(":").str[0]):
        store.write_bars(src, part[BAR_COLUMNS])
    store.write_bars("stocks", pd.concat(stock, ignore_index=True)[BAR_COLUMNS])
    store.save_markets(markets)
    now = pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")
    store.save_state({"cursor": {}, "sources": {
        s: {"type": "dex" if s.startswith("dex") else "cex", "ok": True, "markets": len(TICKERS), "errors": 0,
            "finished": now} for s in sorted({v[0] for v in VENUES})} | {
        "stocks": {"type": "stock", "ok": True, "markets": len(TICKERS), "errors": 0, "finished": now}}})
    (C.DATA / "universe").mkdir(parents=True, exist_ok=True)
    (C.DATA / "universe" / "meta.json").write_text(json.dumps({"fetched_at": now, "tokens": len(markets), "demo": True}))
