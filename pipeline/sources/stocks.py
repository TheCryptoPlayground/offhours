"""Underlying US stocks: 30-minute consolidated-tape (SIP) bars from Alpaca's free plan.

The free Basic plan serves SIP history from 2016 as long as the request ends at least 15
minutes ago, at 200 requests a minute. Before 6 Dec 2026 the tape does not comprehensively
cover overnight ATS trading, although occasional eligible or late prints can appear after
20:00 ET; the exchanges' night sessions begin from that date.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .. import config as C
from ..http import HTTP

DATA_URL = "https://data.alpaca.markets/v2/stocks/bars"


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


class Alpaca:
    pace = 0.32

    def __init__(self, http: HTTP, key: str = "", secret: str = ""):
        self.http = http
        self.key, self.secret = key or C.ALPACA_KEY, secret or C.ALPACA_SECRET

    @property
    def enabled(self) -> bool:
        return bool(self.key and self.secret)

    @property
    def headers(self) -> dict:
        return {"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret}

    def assets(self) -> set[str]:
        """Symbols of US equities and ETFs Alpaca knows about (active only)."""
        j = self.http.get(f"{C.ALPACA_TRADING_BASE}/v2/assets", params={"status": "active", "asset_class": "us_equity"},
                          headers=self.headers, pace=self.pace)
        return {a["symbol"] for a in j}

    def bars(self, tickers: list[str], start: int, end: int, chunk: int = 50) -> list[tuple]:
        """Rows of (ticker, ts, open, high, low, close, dollar volume)."""
        out = []
        tickers = sorted(set(tickers))
        for i in range(0, len(tickers), chunk):
            token = ""
            for _ in range(10_000):
                params = dict(symbols=",".join(tickers[i:i + chunk]), timeframe="30Min", start=_iso(start),
                              end=_iso(end), limit=10_000, feed="sip", adjustment="raw", sort="asc")
                if token:
                    params["page_token"] = token
                j = self.http.get(DATA_URL, params=params, headers=self.headers, pace=self.pace)
                for sym, rows in (j.get("bars") or {}).items():
                    for b in rows:
                        vw = b.get("vw") or b["c"]
                        out.append((sym, _epoch(b["t"]), b["o"], b["h"], b["l"], b["c"], float(b["v"]) * float(vw)))
                token = j.get("next_page_token") or ""
                if not token:
                    break
        return out
