"""One update run: refresh the universe if stale, then pull new bars from every source.

Sources fail independently. A venue that errors (geo-block, outage, API change) is logged
in data/state.json and shown on the site; the rest of the run carries on.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import pandas as pd

from . import config as C
from . import store, universe
from .http import HTTP
from .sources.base import BAR_COLUMNS, Market, floor_to, frame
from .sources.cex import EXCHANGES, match_markets
from .sources.dex import GeckoTerminal
from .sources.issuer_pools import issuer_contracts, discover_issuer_pools
from .sources.stocks import Alpaca

log = logging.getLogger("offhours")
FLUSH_EVERY = 25


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _window(state: dict, market_id: str, bar_s: int, backfill_days: int, end: int) -> tuple[int, int]:
    cur = state["cursor"].get(market_id)
    if cur:
        start = int(cur) - C.REFETCH_BARS * bar_s
    else:
        start = floor_to(end - backfill_days * 86400, bar_s)
    return start, end


def _pull(markets: list[Market], fetch, state: dict, status: dict, backfill_days: int, now: float) -> None:
    """Fetch every market, writing bars and cursors in batches so an interrupted run keeps progress."""
    pending: dict[str, list[pd.DataFrame]] = {}
    for i, m in enumerate(markets, 1):
        end = floor_to(now - 60, m.bar_s)
        start, end = _window(state, m.market_id, m.bar_s, backfill_days, end)
        if start >= end:
            continue
        try:
            rows = fetch(m, start, end)
        except Exception as e:
            status["errors"] = status.get("errors", 0) + 1
            status["last_error"] = f"{m.native}: {e}"[:300]
            log.warning("%s %s: %s", m.source, m.native, e)
            continue
        df = frame(m.market_id, m.bar_s, rows, start, end)
        pending.setdefault(m.source, []).append(df[df.vol_usd > 0])  # bars without trades carry nothing we use
        state["cursor"][m.market_id] = end
        if i % FLUSH_EVERY == 0 or i == len(markets):
            for src, frames in pending.items():
                status["rows"] = status.get("rows", 0) + store.write_bars(src, pd.concat(frames, ignore_index=True))
            pending.clear()
            store.save_state(state)
    for src, frames in pending.items():
        status["rows"] = status.get("rows", 0) + store.write_bars(src, pd.concat(frames, ignore_index=True))
    store.save_state(state)


def update_cex(http: HTTP, name: str, tokens: pd.DataFrame, state: dict, now: float) -> list[Market]:
    status = {"type": "cex", "rows": 0, "errors": 0, "started": _now_iso()}
    state["sources"][name] = status
    ex = EXCHANGES[name](http)
    try:
        markets = match_markets(name, ex.list_markets(), tokens, _now_iso())
    except Exception as e:
        status.update(ok=False, error=str(e)[:300])
        log.error("%s: listing failed: %s", name, e)
        store.save_state(state)
        return []
    status["markets"] = len(markets)
    log.info("%s: %d tokenized-stock markets", name, len(markets))
    _pull(markets, lambda m, a, b: ex.fetch(m.native, a, b), state, status, C.BACKFILL_DAYS, now)
    status.update(ok=status["errors"] < max(1, len(markets)), finished=_now_iso())
    return markets


def update_dex(http: HTTP, uni: dict, state: dict, now: float, rediscover: bool) -> list[Market]:
    gt = GeckoTerminal(http)
    status = {"type": "dex", "rows": 0, "errors": 0, "started": _now_iso()}
    state["sources"]["dex"] = status
    last = state.get("dex_discovered_at")
    stale = not last or pd.Timestamp.now(tz="UTC") - pd.Timestamp(last) > pd.Timedelta(days=C.UNIVERSE_MAX_AGE_DAYS)
    known = store.load_markets()
    active = set(state.get("dex_active", []))
    previous = [Market(**r) for r in known[known.market_id.isin(active)].to_dict("records")]
    markets = previous
    if rediscover or stale or not active or not state.get("issuer_catalog_at"):
        try:
            contracts = issuer_contracts(http)
            legacy = {(r.network, r.cg_id): r.address.lower() for r in uni["addresses"].itertuples()}
            for m in previous:
                if not m.token_address:
                    m.token_address = legacy.get((m.network, m.cg_id), "")
            markets, checks = discover_issuer_pools(http, contracts, previous, _now_iso())
            if not markets:
                raise ValueError("No issuer-verified Coinbase or Ondo pools discovered")
            catalog = pd.DataFrame([dict(network=net, address=addr, **t) for (net, addr), t in contracts.items()])
            catalog.to_csv(C.DATA / "issuer_contracts.csv", index=False)
            (C.DATA / "issuer_pool_check.json").write_text(json.dumps({"checked_at": _now_iso(), "networks": checks}, indent=2))
            state["issuer_catalog_at"] = state["dex_discovered_at"] = _now_iso()
            state["dex_active"] = [m.market_id for m in markets]
            store.save_markets(markets)
        except Exception as e:
            status.update(discovery_errors=1, error=str(e)[:300])
            log.error("dex: issuer discovery failed: %s", e)
            if not previous:
                status.update(ok=False, finished=_now_iso())
                return []
    status["markets"] = len(markets)
    _pull(markets, lambda m, a, b: gt.fetch(m.network, m.native, m.token_side, a, b), state, status,
          C.DEX_BACKFILL_DAYS, now)
    status.update(ok=bool(markets) and status["errors"] < len(markets), finished=_now_iso())
    return markets


def update_stocks(http: HTTP, tickers: list[str], state: dict, now: float) -> None:
    status = {"type": "stock", "rows": 0, "errors": 0, "started": _now_iso(), "markets": len(tickers)}
    state["sources"]["stocks"] = status
    al = Alpaca(http)
    if not al.enabled:
        status.update(ok=False, error="ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set")
        log.warning("stocks: no Alpaca keys, skipping underlying volume")
        return
    end = floor_to(now - 16 * 60, C.BAR_S)  # the free plan serves the SIP up to 15 minutes ago
    known = [t for t in tickers if state["cursor"].get(f"stocks:{t}")]
    fresh = [t for t in tickers if t not in known]
    batches = []
    if known:
        batches.append((known, min(int(state["cursor"][f"stocks:{t}"]) for t in known) - C.REFETCH_BARS * C.BAR_S))
    if fresh:
        batches.append((fresh, floor_to(end - C.BACKFILL_DAYS * 86400, C.BAR_S)))
    for group, start in batches:
        try:
            rows = al.bars(group, start, end)
        except Exception as e:
            status["errors"] += 1
            status["last_error"] = str(e)[:300]
            log.error("stocks: %s", e)
            continue
        if rows:
            df = pd.DataFrame(rows, columns=["ticker", "ts", "open", "high", "low", "close", "vol_usd"])
            df["market_id"] = "stocks:" + df.ticker
            df["bar_s"] = C.BAR_S
            df = df[(df.ts >= start) & (df.ts < end)]
            status["rows"] += store.write_bars("stocks", df[BAR_COLUMNS])
        for t in group:
            state["cursor"][f"stocks:{t}"] = end
        store.save_state(state)
    status.update(ok=status["errors"] == 0, finished=_now_iso())


def run(only: list[str] | None = None, refresh_universe: bool = False, rediscover: bool = False,
        skip_dex: bool = False, skip_stocks: bool = False) -> None:
    http = HTTP()
    t0 = time.time()
    uni = universe.load_or_build(http, force=refresh_universe)
    tokens = uni["tokens"]
    state = store.load_state()
    state.setdefault("cursor", {})
    state.setdefault("sources", {})
    now = time.time()

    tracked: list[Market] = []
    for name in C.CEX_SOURCES:
        if only and name not in only:
            continue
        tracked += update_cex(http, name, tokens, state, now)
    if not skip_dex and (not only or "dex" in only):
        tracked += update_dex(http, uni, state, now, rediscover)
    store.save_markets(tracked)

    if not skip_stocks and (not only or "stocks" in only):
        markets = store.load_markets()
        tickers = sorted({u for u in markets.underlying if u} | set(C.EXTRA_STOCKS))
        update_stocks(http, tickers, state, time.time())
    state["last_run"] = {"finished": _now_iso(), "seconds": round(time.time() - t0), "http_calls": http.calls}
    store.save_state(state)
    log.info("update finished in %.0fs with %d HTTP calls", time.time() - t0, http.calls)
