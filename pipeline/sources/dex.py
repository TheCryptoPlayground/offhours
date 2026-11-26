"""On-chain DEX pools through GeckoTerminal's free API (no key, ~30 calls a minute).

Pools are classified by what the stock token trades against:
    usd    stablecoins              major  ETH, SOL, BNB, BTC wrappers
    stock  another stock token      other  anything else, mostly memecoins
"Other" pools are reported separately: a memecoin launched against a stock token generates
swaps that say more about the memecoin than about the stock.
"""
from __future__ import annotations

import logging

import pandas as pd

from .. import config as C
from ..http import HTTP
from .base import Market

log = logging.getLogger("offhours")
GT = "https://api.geckoterminal.com/api/v2"
HEADERS = {"Accept": "application/json"}


def norm_addr(a: str) -> str:
    a = (a or "").strip()
    return a.lower() if a.startswith("0x") else a


class GeckoTerminal:
    pace = 6.5

    def __init__(self, http: HTTP):
        self.http = http
        self.failed_deployments = set()

    def get(self, path: str, **params):
        return self.http.get(GT + path, params=params, headers=HEADERS, pace=self.pace)

    def networks(self) -> dict[str, str]:
        """CoinGecko asset-platform id -> GeckoTerminal network id."""
        out: dict[str, str] = {}
        for page in range(1, 30):
            response = self.get("/networks", page=page)
            data = response.get("data", [])
            if not data:
                break
            for n in data:
                plat = (n.get("attributes") or {}).get("coingecko_asset_platform_id")
                if plat:
                    out[plat] = n["id"]
            if not (response.get("links") or {}).get("next"):
                break
        return out

    def token_pools(self, network: str, address: str) -> list[dict]:
        j = self.get(f"/networks/{network}/tokens/{address}/pools", page=1,
                     include="base_token,quote_token", sort="h24_volume_usd_desc")
        included = {t["id"]: t.get("attributes", {}) for t in j.get("included", [])}
        out = []
        for p in j.get("data", []):
            a, rel = p.get("attributes", {}), p.get("relationships", {})
            sides = {}
            for side in ("base", "quote"):
                tid = ((rel.get(f"{side}_token") or {}).get("data") or {}).get("id", "")
                attrs = included.get(tid, {})
                sides[side] = (norm_addr(attrs.get("address") or tid.removeprefix(network + "_")),
                               str(attrs.get("symbol") or "").upper())
            if not all(s[1] for s in sides.values()) and " / " in str(a.get("name", "")):
                names = [x.strip().upper() for x in str(a["name"]).split(" / ", 1)]
                sides = {k: (v[0], v[1] or names[i]) for i, (k, v) in enumerate(sides.items())}
            out.append(dict(
                pool=a.get("address") or p.get("id", "").removeprefix(network + "_"),
                dex=((rel.get("dex") or {}).get("data") or {}).get("id", ""),
                base_addr=sides["base"][0], base_sym=sides["base"][1],
                quote_addr=sides["quote"][0], quote_sym=sides["quote"][1],
                vol24=float(((a.get("volume_usd") or {}).get("h24")) or 0.0),
            ))
        return out

    def fetch(self, network: str, pool: str, token_side: str, start: int, end: int) -> list[tuple]:
        out = []
        before = end
        while before > start:
            limit = min(1000, (before - start + 899) // 900)
            response = self.get(f"/networks/{network}/pools/{pool}/ohlcv/minute", aggregate=15,
                                before_timestamp=before - 1, limit=limit, currency="usd", token=token_side)
            rows = response["data"]["attributes"]["ohlcv_list"]
            out.extend(tuple([int(r[0])] + [float(x) for x in r[1:6]]) for r in rows if start <= r[0] < before)
            # The provider skips empty intervals and looks back up to six months per request.
            if len(rows) < limit and before - start <= 150 * 86400:
                break
            earlier = min((r[0] for r in rows if r[0] < before), default=before - 150 * 86400)
            before = int(earlier)
        return out


def counterpart(symbol: str, is_stock: bool) -> str:
    if is_stock:
        return "stock"
    s = symbol.upper()
    if s in C.USD_LIKE:
        return "usd"
    if s in C.MAJORS:
        return "major"
    return "other"


def discover_pools(gt: GeckoTerminal, addresses: pd.DataFrame, tokens: pd.DataFrame, now: str) -> list[Market]:
    """Find the pools worth tracking for every listed stock token on the configured networks."""
    tok = tokens.set_index("cg_id")
    registry = {(r.network, norm_addr(r.address)): r.cg_id for r in addresses.itertuples()}
    want = addresses[addresses.network.isin(C.DEX_NETWORKS)].copy()
    want["vol24"] = want.cg_id.map(tok.vol24).fillna(0)
    want = want[want.vol24 >= C.DEX_MIN_TOKEN_VOL].sort_values("vol24", ascending=False)
    log.info("dex: discovering pools for %d token deployments", len(want))

    found: dict[tuple, Market] = {}
    for r in want.itertuples():
        try:
            pools = gt.token_pools(r.network, r.address)
        except Exception as e:
            gt.failed_deployments.add((r.network, r.cg_id))
            log.warning("dex: pools for %s on %s failed: %s", r.cg_id, r.network, e)
            continue
        for p in sorted(pools, key=lambda pool: -pool["vol24"]):
            key = (r.network, p["pool"])
            if key in found:
                continue
            base_id = registry.get((r.network, p["base_addr"]))
            quote_id = registry.get((r.network, p["quote_addr"]))
            if base_id is None and quote_id is None:
                continue
            side = "base" if base_id is not None else "quote"
            stock_id = base_id if side == "base" else quote_id
            other_sym = p["quote_sym"] if side == "base" else p["base_sym"]
            kind = counterpart(other_sym, (quote_id if side == "base" else base_id) is not None)
            floor = C.DEX_MIN_OTHER_POOL_VOL if kind == "other" else C.DEX_MIN_POOL_VOL
            if p["vol24"] < floor or stock_id not in tok.index:
                continue
            t = tok.loc[stock_id]
            found[key] = Market(
                market_id=f"dex-{r.network}:{p['pool']}", source=f"dex-{r.network}", native=p["pool"],
                base=p["base_sym"], quote=p["quote_sym"], cg_id=stock_id, family=str(t.family),
                underlying=t.underlying if isinstance(t.underlying, str) else "",
                venue_type="dex", counterpart=kind, network=r.network, token_side=side,
                bar_s=C.DEX_BAR_S, vol24=p["vol24"], discovered=now,
            )
    markets = sorted(found.values(), key=lambda m: -m.vol24)[: C.DEX_MAX_POOLS]
    log.info("dex: tracking %d pools (of %d found)", len(markets), len(found))
    return markets
