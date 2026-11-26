"""Do token prices set while every US venue is shut anticipate where the stock opens?

For each closure (weekends and holidays, at least 20 hours with no US session) and each
stock with enough token trading during it:

    x = token log return from the closure start (usually Friday 20:00 ET) to 2h before it
        ends (Sunday 18:00 ET, when CME equity futures reopen). Each venue's own return,
        weighted by that venue's volume in the window, so issuers whose tokens accrue
        dividends (and so sit at a different price level) do not distort the average.
    y = the stock's log return from its last trade before the closure to the next open.

If token trading carried no information about the stock, x would not predict y. The
summary reports the slope of y on x (standard errors clustered by closure, since every
stock shares the same weekend news), R squared, the share of the gap's mean square that
the token move had already covered, and how often the signs agree.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import calendar as K
from . import config as C
from . import store

H = 3600


def _utc_days(a: int, b: int) -> list[str]:
    return pd.date_range(pd.Timestamp(a, unit="s").normalize(), pd.Timestamp(b, unit="s").normalize(),
                         freq="D").strftime("%Y-%m-%d").tolist()


def _last_close(bars: pd.DataFrame, at: int, stale_h: float) -> pd.Series:
    """Per market: close of the last traded bar that finished by `at` and began within stale_h."""
    b = bars[(bars.ts + bars.bar_s <= at) & (bars.ts >= at - stale_h * H) & (bars.vol_usd > 0)]
    return b.sort_values("ts").groupby("market_id").close.last()


def closure_points(markets: pd.DataFrame, now_ts: int) -> pd.DataFrame:
    toks = markets[markets.counterpart.isin(("usd", "major")) & (markets.underlying != "")]
    tok_under = toks.set_index("market_id").underlying
    first = min((int(pd.Timestamp(day).timestamp()) for _, day, _ in store.bar_files(exclude=("stocks",))), default=None)
    if first is None:
        return pd.DataFrame(columns=["window", "underlying", "x", "y", "token_vol", "venues"])
    W = K.closure_windows()
    W = W[(W.start >= first + 86400) & (W.next_open + C.BAR_S <= now_ts - 16 * 60)]
    rows = []
    for w in W.itertuples():
        m_end = int(w.end - C.CLOSURE_END_BUFFER_H * H)
        tok = store.read_days(None, _utc_days(w.start - C.STALE_START_H * H, m_end), exclude=("stocks",))
        tok = tok[tok.market_id.isin(tok_under.index)]
        und = store.read_days(["stocks"], _utc_days(w.start - C.UNDERLYING_STALE_H * H, w.next_open + C.BAR_S))
        if tok.empty or und.empty:
            continue
        p0 = _last_close(tok, w.start, C.STALE_START_H)
        p1 = _last_close(tok, m_end, C.STALE_END_H)
        inside = tok[(tok.ts >= w.start) & (tok.ts + tok.bar_s <= m_end)]
        vol = inside.groupby("market_id").vol_usd.sum()
        m = pd.DataFrame({"p0": p0, "p1": p1, "v": vol}).dropna()
        m = m[(m.p0 > 0) & (m.p1 > 0) & (m.v > 0)]
        if m.empty:
            continue
        m["r"] = np.log(m.p1 / m.p0)
        m["u"] = tok_under.reindex(m.index).values
        tv = m.v.groupby(m.u).sum()
        x = (m.r * m.v).groupby(m.u).sum() / tv
        nv = m.groupby("u").size()

        und = und.assign(u=und.market_id.str.removeprefix("stocks:"))
        q0 = und[(und.ts + und.bar_s <= w.start) & (und.ts >= w.start - C.UNDERLYING_STALE_H * H) & (und.vol_usd > 0)]
        q0 = q0.sort_values("ts").groupby("u").close.last()
        q1 = und[und.ts == w.next_open].groupby("u").open.first()
        y = np.log(q1 / q0).dropna()

        for u in x.index.intersection(y.index):
            rows.append(dict(window=str(pd.Timestamp(w.start, unit="s", tz="UTC").tz_convert(C.MARKET_TZ).date()),
                             next_date=str(pd.Timestamp(w.next_date).date()), underlying=u, x=float(x[u]),
                             y=float(y[u]), token_vol=float(tv[u]), venues=int(nv[u]),
                             hours=round((m_end - w.start) / H, 1)))
    df = pd.DataFrame(rows, columns=["window", "next_date", "underlying", "x", "y", "token_vol", "venues", "hours"])
    keep = (df.token_vol >= C.CLOSURE_MIN_TOKEN_VOL) & (df.x.abs() <= C.CLOSURE_MAX_ABS_LOGRET) \
        & (df.y.abs() <= C.CLOSURE_MAX_ABS_LOGRET)
    return df[keep].reset_index(drop=True)


def ols(x: np.ndarray, y: np.ndarray, groups: np.ndarray | None = None) -> dict:
    n = len(x)
    X = np.column_stack([np.ones(n), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ beta
    bread = np.linalg.inv(X.T @ X)
    if groups is not None:
        labels, inv = np.unique(groups, return_inverse=True)
        G = len(labels)
        scores = np.zeros((G, 2))
        np.add.at(scores, inv, X * e[:, None])
        meat = scores.T @ scores
        adj = (G / (G - 1)) * ((n - 1) / (n - 2)) if G > 1 else np.nan
    else:  # HC1
        meat = (X * e[:, None]).T @ (X * e[:, None])
        adj = n / (n - 2)
    V = adj * bread @ meat @ bread
    tss = float(((y - y.mean()) ** 2).sum())
    return dict(alpha=float(beta[0]), beta=float(beta[1]), se=float(np.sqrt(V[1, 1])),
                r2=float(1 - (e ** 2).sum() / tss) if tss > 0 else np.nan)


def describe(df: pd.DataFrame, cluster: bool = True, min_n: int = 8) -> dict:
    out = dict(n=int(len(df)), windows=int(df.window.nunique()) if len(df) else 0)
    if len(df) < min_n or out["windows"] < (3 if cluster else 1):
        return out
    x, y = df.x.to_numpy(), df.y.to_numpy()
    out.update(ols(x, y, df.window.to_numpy() if cluster else None))
    msy = float(np.mean(y ** 2))
    out["anticipated"] = float(1 - np.mean((y - x) ** 2) / msy) if msy > 0 else np.nan
    big = np.abs(y) > 0.005
    out["hit_rate"] = float(np.mean(np.sign(x[big]) == np.sign(y[big]))) if big.any() else np.nan
    out["n_hit"] = int(big.sum())
    out["up_rate"] = float(np.mean(y[big] > 0)) if big.any() else np.nan
    return {k: (round(v, 4) if isinstance(v, float) and v == v else v) for k, v in out.items()}


def summary(points: pd.DataFrame) -> dict:
    by_ticker = []
    for u, d in points.groupby("underlying"):
        if d.window.nunique() >= C.CLOSURE_MIN_WINDOWS_PER_TICKER:
            s = describe(d, cluster=False, min_n=C.CLOSURE_MIN_WINDOWS_PER_TICKER)
            s["underlying"] = u
            s["token_vol"] = round(float(d.token_vol.sum()))
            by_ticker.append(s)
    by_ticker.sort(key=lambda s: -s["token_vol"])
    return dict(pooled=describe(points), by_ticker=by_ticker)
