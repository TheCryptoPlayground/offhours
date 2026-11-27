"""Centralised exchanges with free public market-data endpoints (no key needed).

Each adapter implements:
    list_markets() -> [{"native", "base", "quote", "family"}]   venue-classified stock markets
    fetch(native, start, end) -> [(ts, o, h, l, c, quote_volume)]  30-minute bars

Quote volume is in the quote currency; only USD-like quotes are kept, and they are
counted 1:1 as US dollars.
"""
from __future__ import annotations

import json
import logging
import math

import pandas as pd

from .. import config as C
from ..http import HTTP
from .base import Market, SourceError, forward_windows

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
        cache = C.DATA / "binance_bstocks.json"
        try:
            directory = self.http.get("https://www.binance.com/bapi/asset/v2/public/asset-service/product/get-products",
                                      params={"includeEtf": "true"})
            names = {p["b"]: p["an"] for p in directory["data"] if "bStocks" in (p.get("tags") or [])}
            if not names:
                raise SourceError("Binance returned no tagged bStocks")
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(names, sort_keys=True, indent=2))
        except Exception as error:
            if not cache.exists():
                raise
            names = json.loads(cache.read_text())
            self.discovery_warning = f"Using the last verified bStocks directory: {error}"
        return [dict(native=s["symbol"], base=s["baseAsset"], quote=s["quoteAsset"], family="bstocks")
                for s in info["symbols"] if s["status"] == "TRADING" and s["baseAsset"] in names]

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
        out = []
        for key, p in pairs.items():
            ws = p.get("wsname") or ""
            if "/" not in ws or p.get("status") == "delisted":
                continue
            base, quote = ws.split("/", 1)
            if base.endswith("x") and key == base + quote:
                out.append(dict(native=key, base=base, quote=quote, family="xstocks"))
        return out

    def fetch(self, native, start, end):
        res = self._result(self.get("/OHLC", pair=native, interval=30, since=start - C.BAR_S,
                                    asset_class=self.AC))
        rows = next((v for k, v in res.items() if k != "last"), [])
        # [time, o, h, l, c, vwap, volume, count]; the newest entry is still forming
        return [(int(r[0]), _f(r[1]), _f(r[2]), _f(r[3]), _f(r[4]), _f(r[5]) * _f(r[6])) for r in rows]


class Gate(Exchange):
    name, base_url, pace = "gate", "https://api.gateio.ws/api/v4/spot", 0.15

    def list_markets(self):
        assets = {}
        for row in self.get("/currencies"):
            tags = row.get("category") or []
            ticker = (row.get("tokenized_security_info") or {}).get("underlying")
            if not ticker or "stocks" not in tags:
                continue
            for tag, family in (("ondo-stocks", "ondo"), ("xstocks", "xstocks")):
                if tag in tags:
                    assets[row["currency"]] = (family, ticker.upper().replace("-", "."))
        return [dict(native=p["id"], base=p["base"], quote=p["quote"],
                     family=assets[p["base"]][0], underlying=assets[p["base"]][1])
                for p in self.get("/currency_pairs") if p["base"] in assets and p["trade_status"] == "tradable"]

    def fetch(self, native, start, end):
        bars = []
        for a, b in forward_windows(start, end, C.BAR_S, 999):
            rows = self.get("/candlesticks", currency_pair=native, interval="30m",
                            **{"from": a, "to": b - 1})
            for ts, turnover, close, high, low, opening, units, closed in rows:
                if str(closed).lower() == "true":
                    bars.append((int(ts), float(opening), float(high), float(low), float(close), float(turnover)))
        return bars


EXCHANGES = {cls.name: cls for cls in (Binance, Kraken, Gate)}


def match_markets(source: str, listed: list[dict], tokens: pd.DataFrame, now: str) -> list[Market]:
    """Use a venue-verified stock category when available, otherwise match the token universe.

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
        family = m.get("family")
        if source == "gate" and family in ("ondo", "xstocks") and m.get("underlying"):
            out.append(Market(market_id=f"gate:{m['native']}", source=source, native=m["native"],
                              base=m["base"], quote=quote, family=family, underlying=m["underlying"],
                              venue_type="cex", counterpart="usd", bar_s=C.BAR_S, discovered=now))
            continue
        if family in ("bstocks", "xstocks"):
            base = m["base"]
            suffix = "B" if family == "bstocks" else "x"
            if not base.endswith(suffix) or len(base) <= 1:
                raise SourceError(f"Unexpected stock-token code {base}")
            ticker = "BRK.B" if base == "BRKBB" else base[:-1].upper().replace("-", ".")
            token = next((t for t in (cands or []) if t.family == family), None)
            out.append(Market(market_id=f"{source}:{m['native']}", source=source, native=m["native"],
                              base=base, quote=quote, cg_id=token.cg_id if token else "", family=family,
                              underlying=ticker, venue_type="cex", counterpart="usd", bar_s=C.BAR_S, discovered=now))
            continue
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
