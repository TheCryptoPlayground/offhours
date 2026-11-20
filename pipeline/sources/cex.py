"""Centralised exchanges with free public market-data endpoints (no key needed).

Each adapter implements:
    list_markets() -> [{"native", "base", "quote", "price"}]   all live spot markets
    fetch(native, start, end) -> [(ts, o, h, l, c, quote_volume)]  30-minute bars

Quote volume is in the quote currency; only USD-like quotes are kept, and they are
counted 1:1 as US dollars.
"""
from __future__ import annotations

import logging
import math

import pandas as pd

from .. import config as C
from ..http import HTTP
from .base import Market, SourceError, backward, forward_windows

log = logging.getLogger("offhours")


def _known(v) -> bool:
    return v is not None and v == v and v > 0


def _f(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return math.nan
    return v


class Exchange:
    name = ""
    base_url = ""
    pace = 0.1

    def __init__(self, http: HTTP):
        self.http = http

    def get(self, path: str, **params):
        return self.http.get(self.base_url + path, params=params, pace=self.pace)

    def list_markets(self) -> list[dict]:
        raise NotImplementedError

    def fetch(self, native: str, start: int, end: int) -> list[tuple]:
        raise NotImplementedError


class Binance(Exchange):
    # data-api.binance.vision is Binance's public market-data mirror; api.binance.com refuses
    # US addresses, which is where GitHub's hosted runners live.
    name, base_url, pace = "binance", "https://data-api.binance.vision", 0.2

    def list_markets(self):
        info = self.get("/api/v3/exchangeInfo")
        prices = {p["symbol"]: _f(p["price"]) for p in self.get("/api/v3/ticker/price")}
        return [dict(native=s["symbol"], base=s["baseAsset"], quote=s["quoteAsset"], price=prices.get(s["symbol"]))
                for s in info.get("symbols", []) if s.get("status") == "TRADING"]

    def fetch(self, native, start, end):
        out = []
        for a, b in forward_windows(start, end, C.BAR_S, 1000):
            rows = self.get("/api/v3/klines", symbol=native, interval="30m",
                            startTime=a * 1000, endTime=b * 1000 - 1, limit=1000)
            # [open time, o, h, l, c, base vol, close time, quote vol, trades, ...]
            out += [(int(r[0]) // 1000, _f(r[1]), _f(r[2]), _f(r[3]), _f(r[4]), _f(r[7])) for r in rows]
        return out


class Kraken(Exchange):
    # xStocks live in Kraken's "tokenized_asset" class, which every market-data call must name.
    # OHLC returns only the latest 720 entries, so 30m history reaches back 15 days; after the
    # first run the daily job keeps it continuous.
    name, base_url, pace = "kraken", "https://api.kraken.com/0/public", 1.1
    AC = "tokenized_asset"

    def _result(self, j):
        if j.get("error"):
            raise SourceError(f"kraken: {j['error']}")
        return j["result"]

    def list_markets(self):
        pairs = self._result(self.get("/AssetPairs", aclass_base=self.AC))
        try:
            ticker = self._result(self.get("/Ticker", asset_class=self.AC))
        except Exception as e:  # prices only feed a sanity check
            log.warning("kraken ticker failed (%s); skipping price check", e)
            ticker = {}
        out = []
        for key, p in pairs.items():
            ws = p.get("wsname") or ""
            if "/" not in ws or p.get("status") == "delisted":
                continue
            base, quote = ws.split("/", 1)
            t = ticker.get(key) or ticker.get(p.get("altname", ""))
            out.append(dict(native=key, base=base, quote=quote, price=_f(t["c"][0]) if t else None))
        return out

    def fetch(self, native, start, end):
        res = self._result(self.get("/OHLC", pair=native, interval=30, since=start - C.BAR_S,
                                    asset_class=self.AC))
        rows = next((v for k, v in res.items() if k != "last"), [])
        # [time, o, h, l, c, vwap, volume, count]; the newest entry is still forming
        return [(int(r[0]), _f(r[1]), _f(r[2]), _f(r[3]), _f(r[4]), _f(r[5]) * _f(r[6])) for r in rows]


class Bybit(Exchange):
    name, base_url, pace = "bybit", "https://api.bybit.com", 0.12

    def _result(self, j):
        if j.get("retCode") != 0:
            raise SourceError(f"bybit: {j.get('retCode')} {j.get('retMsg')}")
        return j["result"]

    def list_markets(self):
        inst, cursor = [], ""
        for _ in range(20):
            params = {"category": "spot", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            r = self._result(self.get("/v5/market/instruments-info", **params))
            inst += r.get("list", [])
            cursor = r.get("nextPageCursor") or ""
            if not cursor:
                break
        tick = {t["symbol"]: _f(t.get("lastPrice")) for t in self._result(self.get("/v5/market/tickers", category="spot"))["list"]}
        return [dict(native=i["symbol"], base=i["baseCoin"], quote=i["quoteCoin"], price=tick.get(i["symbol"]))
                for i in inst if i.get("status") == "Trading"]

    def fetch(self, native, start, end):
        out = []
        for a, b in forward_windows(start, end, C.BAR_S, 1000):
            r = self._result(self.get("/v5/market/kline", category="spot", symbol=native, interval="30",
                                      start=a * 1000, end=b * 1000 - 1, limit=1000))
            # newest first: [start, o, h, l, c, base volume, turnover (quote volume)]
            out += [(int(x[0]) // 1000, _f(x[1]), _f(x[2]), _f(x[3]), _f(x[4]), _f(x[6])) for x in r.get("list", [])]
        return out


class Bitget(Exchange):
    name, base_url, pace = "bitget", "https://api.bitget.com", 0.1

    def _data(self, j):
        if str(j.get("code")) != "00000":
            raise SourceError(f"bitget: {j.get('code')} {j.get('msg')}")
        return j["data"]

    def list_markets(self):
        syms = self._data(self.get("/api/v2/spot/public/symbols"))
        tick = {t["symbol"]: _f(t.get("lastPr")) for t in self._data(self.get("/api/v2/spot/market/tickers"))}
        return [dict(native=s["symbol"], base=s["baseCoin"], quote=s["quoteCoin"], price=tick.get(s["symbol"]))
                for s in syms if s.get("status") in ("online", "gray")]

    def fetch(self, native, start, end):
        def before(t):
            rows = self._data(self.get("/api/v3/market/candles", category="SPOT", symbol=native,
                                       interval="15m", endTime=t * 1000, limit=1000))
            # Reality stock tokens no longer accept 30m candles. v3 returns
            # [ts, o, h, l, c, base volume, quote turnover], so fetch 15m and
            # combine pairs below to retain the dashboard's 30m session grid.
            return [(int(x[0]) // 1000, _f(x[1]), _f(x[2]), _f(x[3]), _f(x[4]), _f(x[6])) for x in rows]

        raw = backward(before, start, end)
        by_ts = {int(r[0]): r for r in raw}
        buckets: dict[int, list[tuple]] = {}
        for ts, row in by_ts.items():
            buckets.setdefault(ts // C.BAR_S * C.BAR_S, []).append(row)
        out = []
        for ts, rows in sorted(buckets.items()):
            rows.sort(key=lambda r: r[0])
            out.append((ts, rows[0][1], max(r[2] for r in rows), min(r[3] for r in rows),
                        rows[-1][4], sum(r[5] for r in rows)))
        return out


class Gate(Exchange):
    name, base_url, pace = "gate", "https://api.gateio.ws/api/v4", 0.12

    def list_markets(self):
        pairs = self.get("/spot/currency_pairs")
        tick = {t["currency_pair"]: _f(t.get("last")) for t in self.get("/spot/tickers")}
        return [dict(native=p["id"], base=p["base"], quote=p["quote"], price=tick.get(p["id"]))
                for p in pairs if p.get("trade_status") == "tradable"]

    def fetch(self, native, start, end):
        out = []
        for a, b in forward_windows(start, end, C.BAR_S, 900):
            rows = self.get("/spot/candlesticks", currency_pair=native, interval="30m", **{"from": a, "to": b - 1})
            # [t, quote volume, close, high, low, open, base volume, window closed]
            out += [(int(x[0]), _f(x[5]), _f(x[3]), _f(x[4]), _f(x[2]), _f(x[1])) for x in rows]
        return out


class OKX(Exchange):
    name, base_url, pace = "okx", "https://www.okx.com", 0.12

    def _data(self, j):
        if str(j.get("code")) != "0":
            raise SourceError(f"okx: {j.get('code')} {j.get('msg')}")
        return j["data"]

    def list_markets(self):
        inst = self._data(self.get("/api/v5/public/instruments", instType="SPOT"))
        tick = {t["instId"]: _f(t.get("last")) for t in self._data(self.get("/api/v5/market/tickers", instType="SPOT"))}
        return [dict(native=i["instId"], base=i["baseCcy"], quote=i["quoteCcy"], price=tick.get(i["instId"]))
                for i in inst if i.get("state") == "live"]

    def fetch(self, native, start, end):
        def before(t):
            rows = self._data(self.get("/api/v5/market/history-candles", instId=native, bar="30m",
                                       after=t * 1000, limit=100))
            # newest first: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
            return [(int(x[0]) // 1000, _f(x[1]), _f(x[2]), _f(x[3]), _f(x[4]), _f(x[7]))
                    for x in rows if str(x[8]) == "1"]
        return backward(before, start, end)


EXCHANGES = {cls.name: cls for cls in (Binance, Kraken, Bybit, Bitget, Gate, OKX)}


def match_markets(source: str, listed: list[dict], tokens: pd.DataFrame, now: str) -> list[Market]:
    """Pair an exchange's spot listings with the token universe.

    A listing matches when its base symbol equals a token's symbol, its quote is USD-like,
    and (where both prices are known) its price is within PRICE_TOLERANCE of CoinGecko's.
    The price check stops unrelated coins that share a ticker-like symbol.
    """
    by_symbol: dict[str, list] = {}
    for t in tokens.itertuples():
        by_symbol.setdefault(str(t.symbol).upper(), []).append(t)
    out = []
    for m in listed:
        quote = str(m["quote"]).upper()
        if quote not in C.USD_LIKE:
            continue
        cands = by_symbol.get(str(m["base"]).upper())
        if not cands:
            continue
        price = m.get("price")
        best, best_err = None, math.inf
        for t in cands:
            err = abs(price / t.price - 1) if (_known(price) and _known(t.price)) else 0.0
            if err < best_err:
                best, best_err = t, err
        if best is None or best_err > C.PRICE_TOLERANCE:
            log.info("%s: skip %s (price %.4g vs %.4g)", source, m["native"], price or 0, getattr(best, "price", 0) or 0)
            continue
        out.append(Market(
            market_id=f"{source}:{m['native']}", source=source, native=m["native"],
            base=str(m["base"]), quote=quote, cg_id=best.cg_id, family=best.family,
            underlying=best.underlying if isinstance(best.underlying, str) else "",
            venue_type="cex", counterpart="usd", bar_s=C.BAR_S, discovered=now,
        ))
    return out
