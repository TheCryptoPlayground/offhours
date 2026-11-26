"""Discover stock-token pools from the two issuers' contract directories."""
import re

from .base import Market
from .dex import GT, norm_addr
from .. import config as C
from ..http import HTTPError

COINBASE_LIST = "https://api.coinbase.com/v1/tokenized-stocks"
ONDO_LIST = "https://raw.githubusercontent.com/ondoprotocol/ondo-global-markets-token-list/main/tokenlist.json"

# Canonical quote contracts prevent an unrelated token with a familiar symbol from qualifying.
QUOTES = {
    "base": {
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": "USDC",
        "0x4200000000000000000000000000000000000006": "WETH",
        "0x0000000000000000000000000000000000000000": "ETH",
        "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf": "cbBTC",
    },
    "eth": {
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
        "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH",
        "0x0000000000000000000000000000000000000000": "ETH",
        "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": "WBTC",
        "0x6b175474e89094c44da98b954eedeac495271d0f": "DAI",
    },
}


def issuer_contracts(http):
    contracts = {}

    def add(network, address, symbol, name, suffix):
        address = norm_addr(address)
        if not re.fullmatch("0x[0-9a-f]{40}", address) or not symbol.endswith(suffix):
            raise ValueError(f"Unexpected issuer listing: {network} {symbol} {address}")
        ticker = symbol[:-len(suffix)].upper().replace("-", ".")
        if not re.fullmatch(r"[A-Z0-9.]{1,15}", ticker):
            raise ValueError(f"Unexpected underlying: {symbol}")
        contracts[network, address] = dict(ticker=ticker, symbol=symbol, name=name)

    page, seen = "", set()
    while True:
        result = http.get(COINBASE_LIST, params={"page_token": page} if page else {})
        for t in result["tokens"]:
            add("base", t["contract_address"], t["symbol"], t["name"], "c")
            contracts["base", norm_addr(t["contract_address"])]["first_mint_at"] = t.get("first_mint_date") or ""
        page = result.get("next_page_token")
        if not page:
            break
        if page in seen:
            raise ValueError("Coinbase repeated a catalog page")
        seen.add(page)
    for t in http.get(ONDO_LIST)["tokens"]:
        if t["chainId"] == 1 and "ondo tokenized" in t["name"].lower():
            add("eth", t["address"], t["symbol"], t["name"], "on")
    if {net for net, _ in contracts} != {"eth", "base"}:
        raise ValueError("Incomplete issuer directories")
    return contracts


def _batches(values):
    values = sorted(set(values))
    for start in range(0, len(values), 30):
        yield values[start:start + 30]


def _gecko_multi(http, network, resource, addresses, **params):
    try:
        return http.get(f"{GT}/networks/{network}/{resource}/multi/{','.join(addresses)}",
                        params=params, pace=6.5)
    except HTTPError as error:
        if error.status == 404 and "not found" in error.body.lower():
            return {}
        raise


def verified_markets(http, net, candidates, contracts, known, now):
    """Use the candle provider's canonical pool ID, token addresses and side ordering."""
    markets = {}
    for batch in _batches(candidates):
        response = _gecko_multi(http, net, "pools", batch)
        for p in response.get("data", []):
            a, sides = p["attributes"], p["relationships"]
            base, quote = [norm_addr(sides[s]["data"]["id"].removeprefix(net + "_"))
                           for s in ("base_token", "quote_token")]
            pool = norm_addr(a["address"])
            if (net, base) in contracts and quote in QUOTES[net]:
                addr, counter, side = base, quote, "base"
            elif (net, quote) in contracts and base in QUOTES[net]:
                addr, counter, side = quote, base, "quote"
            else:
                continue
            t, other = contracts[net, addr], QUOTES[net][counter]
            mid = f"dex-{net}:{pool}"
            markets[mid] = Market(
                market_id=mid, source="dex-" + net, native=pool,
                base=t["symbol"] if side == "base" else other, quote=other if side == "base" else t["symbol"],
                cg_id=known[mid].cg_id if mid in known else "", token_address=addr,
                family="coinbase" if net == "base" else "ondo", underlying=t["ticker"],
                venue_type="dex", network=net, token_side=side,
                counterpart="usd" if other in ("USDC", "USDT", "DAI") else "major",
                bar_s=C.DEX_BAR_S, vol24=float(a["volume_usd"]["h24"] or 0), discovered=now)
    return markets


def index_coverage(network, reported, tracked):
    """Compare once per pool on the same index and rolling-volume snapshot."""
    matched = [volume for pool, volume in reported.items() if f"dex-{network}:{pool}" in tracked]
    return dict(reported_pools=len(reported), tracked_pools=len(matched),
                reported_usd=sum(reported.values()), tracked_usd=sum(matched))


def discover_issuer_pools(http, contracts, known, now):
    """Search every issuer contract, including listings without recent trading volume."""
    markets = {m.market_id: m for m in known if (m.network, m.token_address) in contracts}
    checks = {}
    for net, chain in (("base", "base"), ("eth", "ethereum")):
        candidates, reported = set(), {}
        for batch in _batches(addr for network, addr in contracts if network == net):
            for address in batch:
                pairs = http.get(f"https://api.dexscreener.com/token-pairs/v1/{chain}/{address}", pace=0.25)
                if not isinstance(pairs, list):
                    raise ValueError("Invalid DEX Screener pool response")
                for pair in pairs:
                    sides = {norm_addr(pair[s]["address"]) for s in ("baseToken", "quoteToken")}
                    if pair["chainId"] != chain or address not in sides:
                        continue
                    pool = norm_addr(pair["pairAddress"])
                    volume = (pair.get("volume") or {}).get("h24")
                    if volume is not None:
                        reported.setdefault(pool, float(volume))
                    if sides.intersection(QUOTES[net]):
                        candidates.add(pool)
            data = _gecko_multi(http, net, "tokens", batch, include="top_pools")
            candidates.update(norm_addr(p["attributes"]["address"]) for p in data.get("included", [])
                              if p["type"] == "pool")
        markets.update(verified_markets(http, net, candidates, contracts, markets, now))
        checks[net] = index_coverage(net, reported, markets)
    return list(markets.values()), checks
