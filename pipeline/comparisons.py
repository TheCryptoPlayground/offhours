"""Volume crossovers and adjacent half-hour return pairs for the dashboard window."""
import numpy as np
import pandas as pd

from . import calendar, store
from . import config as C


def volume_views(tokens, stocks):
    # Daily exports round each fact to cents before users can sum it.
    tokens = tokens.assign(vol_usd=tokens.vol_usd.astype(float).round(2))
    stocks = stocks.assign(vol_usd=stocks.vol_usd.astype(float).round(2))
    views = {}
    keys = ["underlying", "market_date"]
    for mode in ("calendar", "matched", *C.SESSIONS):
        if tokens.empty or stocks.empty:
            views[mode] = []
            continue
        t, s = tokens, stocks
        if mode in C.SESSIONS:
            t, s = t[t.session == mode], s[s.session == mode]
        daily = pd.concat([t.groupby(keys).vol_usd.sum().rename("token"),
                           s.groupby(keys).vol_usd.sum().rename("stock")], axis=1).fillna(0)
        matched = daily[(daily.token > 0) & (daily.stock > 0)]
        totals = (daily if mode == "calendar" else matched).groupby(level=0).sum()
        counts = (matched.token > matched.stock).groupby(level=0).agg(["size", "sum"])
        views[mode] = [
            dict(t=ticker, token=r.token, stock=r.stock, days=int(counts.loc[ticker, "size"]),
                 above=int(counts.loc[ticker, "sum"]))
            for ticker, r in totals.iterrows() if ticker and ticker in counts.index
        ]
    return views


def lead_lag(markets, start, end):
    # Within each market, measure an open-to-close log return over a complete
    # half hour. This avoids splicing price levels across issuers or venues.
    days = pd.date_range(start, end + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    meta = markets.loc[markets.counterpart.isin(("major", "usd")), ["market_id", "underlying"]]
    returns = []
    for side in ("token", "stock"):
        bars = (store.read_days(["stocks"], days) if side == "stock" else
                store.read_days(None, days, exclude=("stocks",)).merge(meta, on="market_id"))
        if bars.empty:
            return {}
        codes, dates = calendar.classify(bars.ts.to_numpy())
        bars = bars.assign(session=np.asarray(C.SESSIONS)[codes])
        bars = bars[bars.session.isin(["PRE", "RTH", "POST"]) & (dates >= start.to_datetime64()) &
                    (dates <= end.to_datetime64()) & (bars.vol_usd > 0) &
                    (bars.open > 0) & (bars.close > 0)].sort_values("ts").copy()
        if side == "stock":
            bars["underlying"] = bars.market_id.str.removeprefix("stocks:")
        bars["slot"] = bars.ts // 1800
        half = bars.groupby(["session", "underlying", "slot", "market_id"]).agg(
            opening=("open", "first"), closing=("close", "last"), seconds=("bar_s", "sum"),
            dollars=("vol_usd", "sum")).query("seconds == 1800").copy()
        half["weighted"] = np.log(half.closing / half.opening) * half.dollars
        by_slot = half.groupby(level=[0, 1, 2])[["weighted", "dollars"]].sum()
        returns.append((by_slot.weighted / by_slot.dollars).rename(side))
    current = pd.concat(returns, axis=1).dropna().reset_index()
    following = current.assign(slot=current.slot - 1)
    pairs = current.merge(following, on=["session", "underlying", "slot"], suffixes=("", "_next"))
    out = {}
    for (session, ticker), rows in pairs.groupby(["session", "underlying"]):
        if len(rows) >= 30:
            def correlation(x, y):
                return x.corr(y) if x.nunique() > 1 and y.nunique() > 1 else None
            out.setdefault(session, {})[ticker] = dict(n=len(rows), token_first=correlation(rows.token, rows.stock_next),
                                                      stock_first=correlation(rows.stock, rows.token_next))
    return out
