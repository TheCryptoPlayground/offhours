"""Additional liquid stock-token pools on Solana and BNB Chain."""
from collections import defaultdict

from .dex import norm_addr
from .issuer_pools import ONDO_LIST, QUOTES, verified_markets

BACKED = "https://api.backed.fi/api/v2/public/assets"
BINANCE_NETWORKS = "https://www.binance.com/bapi/capital/v1/public/capital/getNetworkCoinAll"
POOL_LIMIT = 50  # per network and product, refreshed weekly within the free candle budget


def stock_contracts(http, core_markets):
    eligible = set(core_markets.underlying) - {""}
    contracts = {}

    def add(net, address, underlying, symbol, name, family):
        if underlying in eligible and address:
            key = (net, norm_addr(address))
            row = dict(ticker=underlying, symbol=symbol, name=name, family=family)
            if key in contracts and contracts[key] != row:
                raise ValueError(f"Conflicting issuer identities for {key}")
            contracts[key] = row

    page = 1
    while True:
        batch = http.get(BACKED, params={"page": page, "pageSize": 100})
        if batch["page"]["currentPage"] != page or not batch["nodes"]:
            raise ValueError("Invalid xStocks directory page")
        for t in batch["nodes"]:
            if (t.get("underlying") or {}).get("currency") != "USD":
                continue
            for d in t["deployments"]:
                if d["network"] == "Solana":
                    add("solana", d["address"], t["underlyingSymbol"], t["symbol"], t["name"], "xstocks")
        if not batch["page"]["hasNextPage"]:
            break
        page += 1

    for t in http.get(ONDO_LIST)["tokens"]:
        if t["chainId"] == 56 and "ondo tokenized" in t["name"].lower() and t["symbol"].endswith("on"):
            add("bsc", t["address"], t["symbol"][:-2].upper().replace("-", "."), t["symbol"], t["name"], "ondo")

    bstocks = core_markets[core_markets.source == "binance"].set_index("base").underlying.to_dict()
    for t in http.get(BINANCE_NETWORKS)["data"]:
        if t["coin"] in bstocks:
            for chain in t["networkList"]:
                if chain["network"] == "BSC":
                    add("bsc", chain.get("contractAddress"), bstocks[t["coin"]], t["coin"], t["name"], "bstocks")
    if {r["family"] for r in contracts.values()} != {"xstocks", "ondo", "bstocks"}:
        raise ValueError("Incomplete supplementary issuer directories")
    return contracts


def ranked_pools(http, contracts, now):
    reported = defaultdict(dict)
    candidates = defaultdict(dict)
    for (net, addr), token in sorted(contracts.items()):
        group = net, token["family"]
        pairs = http.get(f"https://api.dexscreener.com/token-pairs/v1/{net}/{addr}", pace=0.24)
        if not isinstance(pairs, list):
            raise ValueError("Invalid DEX Screener pool response")
        for p in pairs:
            sides = {norm_addr(p[k]["address"]) for k in ("baseToken", "quoteToken")}
            if p["chainId"] != net or addr not in sides:
                continue
            pool = norm_addr(p["pairAddress"])
            dollars = float((p.get("volume") or {}).get("h24") or 0)
            reported[group].setdefault(pool, dollars)
            if dollars > 0 and sides.intersection(QUOTES[net]):
                candidates[group][pool] = dollars
    markets, checks = [], []
    for net, family in sorted({(n, r["family"]) for (n, _), r in contracts.items()}):
        group = net, family
        volumes = candidates[group]
        ordered = sorted(volumes, key=lambda k: (-volumes[k], k))
        # A CEX ticker or DEX Screener label alone cannot establish identity.
        # The candle provider must confirm the issuer mint and quote addresses.
        picked = []
        for i in range(0, len(ordered), 30):
            verified = verified_markets(http, net, ordered[i:i + 30], contracts, {}, now)
            picked.extend(verified[f"dex-{net}:{pid}"] for pid in ordered[i:i + 30]
                          if f"dex-{net}:{pid}" in verified and verified[f"dex-{net}:{pid}"].family == family)
            if len(picked) >= POOL_LIMIT:
                break
        picked = picked[:POOL_LIMIT]
        if ordered and not picked:
            raise ValueError(f"No candle-provider matches for {net} {family}")
        markets.extend(picked)
        checks.append(dict(network=net, family=family, contracts=sum(n == net and t["family"] == family
                           for (n, _), t in contracts.items()), reported_pools=len(reported[group]),
                           reported_usd=sum(reported[group].values()), tracked_pools=len(picked),
                           tracked_usd=sum(volumes[m.native] for m in picked)))
    return markets, checks
