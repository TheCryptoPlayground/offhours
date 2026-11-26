"""Adapters against the documented response shapes, with a fake HTTP layer (no network)."""
import pandas as pd
import pytest

from pipeline import universe
from pipeline.sources import cex, dex, stocks
from pipeline.sources.base import backward, forward_windows, frame

T0 = 1_763_000_000 // 1800 * 1800


class FakeHTTP:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def get(self, url, params=None, headers=None, pace=0.0, **_):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        return self.handler(url, dict(params or {}))


def test_forward_windows_and_frame():
    w = list(forward_windows(0, 1800 * 2500, 1800, 1000))
    assert w == [(0, 1_800_000), (1_800_000, 3_600_000), (3_600_000, 4_500_000)]
    rows = [(0, 1, 1, 1, 1, 1), (900, 1, 1, 1, 1, 1), (1800, 1, 1, 1, 1, 1), (3600, 1, 1, 1, 1, 1)]
    assert list(frame("m", 1800, rows, 0, 3600).ts) == [0, 1800]  # misaligned and out-of-range bars dropped


def test_backward_stops_when_page_does_not_move():
    calls = []
    def f(t):
        calls.append(t)
        return [(t, 1, 1, 1, 1, 1)]  # nothing older than t
    assert backward(f, 0, 100) == [] and len(calls) == 1


def test_binance_pages_forward_and_uses_quote_volume():
    start, end = T0, T0 + 2500 * 1800
    def h(url, p):
        assert url.startswith("https://data-api.binance.vision/api/v3/klines") and p["interval"] == "30m"
        a, b = p["startTime"] // 1000, (p["endTime"] + 1) // 1000
        return [[t * 1000, "1", "2", "0.5", "1.5", "10", t * 1000 + 1_799_999, str(t % 7 + 100), 5, "0", "0", "0"]
                for t in range(a, b, 1800)][: p["limit"]]
    http = FakeHTTP(h)
    df = frame("binance:TSLABUSDT", 1800, cex.Binance(http).fetch("TSLABUSDT", start, end), start, end)
    assert len(df) == 2500 and len(http.calls) == 3
    assert df.ts.iloc[0] == start and df.ts.iloc[-1] == end - 1800 and df.vol_usd.iloc[0] == start % 7 + 100


def test_kraken_ohlc_asset_class_and_vwap_volume():
    start, end = T0, T0 + 4 * 1800
    def h(url, p):
        assert p["asset_class"] == "tokenized_asset"
        rows = [[t, "10", "11", "9", "10.5", "10.2", "3", 4] for t in range(start - 1800, end + 1800, 1800)]
        return {"error": [], "result": {"AAPLxUSD": rows, "last": end}}
    df = frame("kraken:AAPLxUSD", 1800, cex.Kraken(FakeHTTP(h)).fetch("AAPLxUSD", start, end), start, end)
    assert list(df.ts) == [start + i * 1800 for i in range(4)]  # the forming bar is dropped
    assert df.vol_usd.iloc[0] == pytest.approx(30.6)
    with pytest.raises(cex.SourceError):
        cex.Kraken(FakeHTTP(lambda u, p: {"error": ["EQuery:Unknown asset pair"]})).fetch("X", T0, T0 + 1800)


def test_kraken_lists_tokenized_pairs():
    def h(url, p):
        if url.endswith("/AssetPairs"):
            assert p["aclass_base"] == "tokenized_asset"
            return {"error": [], "result": {"AAPLxUSD": {"altname": "AAPLxUSD", "wsname": "AAPLx/USD", "status": "online"},
                                            "OLDxUSD": {"wsname": "OLDx/USD", "status": "delisted"}}}
        return {"error": [], "result": {"AAPLxUSD": {"c": ["271.5", "1"]}}}
    assert cex.Kraken(FakeHTTP(h)).list_markets() == [dict(native="AAPLxUSD", base="AAPLx", quote="USD", price=271.5)]


TOKENS = pd.DataFrame([
    dict(cg_id="tesla-xstock", symbol="TSLAX", family="xstocks", underlying="TSLA", price=400.0, vol24=1e6),
    dict(cg_id="tesla-bstock", symbol="TSLAB", family="bstocks", underlying="TSLA", price=401.0, vol24=1e6),
    dict(cg_id="micron-bstock", symbol="MUB", family="bstocks", underlying="MU", price=200.0, vol24=1e6),
])


def test_match_markets_symbol_quote_and_price():
    listed = [
        dict(native="TSLABUSDT", base="TSLAB", quote="USDT", price=402.0),
        dict(native="TSLAxUSD", base="TSLAx", quote="USD", price=399.0),      # case-insensitive
        dict(native="TSLABBTC", base="TSLAB", quote="BTC", price=0.004),     # not a dollar quote
        dict(native="MUBUSDT", base="MUB", quote="USDT", price=0.02),         # same symbol, unrelated coin
        dict(native="NOPEUSDT", base="NOPE", quote="USDT", price=1.0),
        dict(native="TSLAXUSDC", base="TSLAX", quote="USDC", price=None),     # no price: accept on symbol
    ]
    got = {m.native: (m.cg_id, m.underlying, m.family) for m in cex.match_markets("x", listed, TOKENS, "now")}
    assert got == {"TSLABUSDT": ("tesla-bstock", "TSLA", "bstocks"), "TSLAxUSD": ("tesla-xstock", "TSLA", "xstocks"),
                   "TSLAXUSDC": ("tesla-xstock", "TSLA", "xstocks")}


def _pool(addr, base, bsym, quote, qsym, vol):
    return {"id": f"base_{addr}", "attributes": {"address": addr, "name": f"{bsym} / {qsym}", "volume_usd": {"h24": str(vol)}},
            "relationships": {"base_token": {"data": {"id": f"base_{base}"}}, "quote_token": {"data": {"id": f"base_{quote}"}},
                              "dex": {"data": {"id": "uniswap-v4"}}}}


def test_geckoterminal_networks_stops_at_last_linked_page():
    pages = {
        1: {"data": [{"id": "eth", "attributes": {"coingecko_asset_platform_id": "ethereum"}}],
            "links": {"next": "https://api.geckoterminal.com/api/v2/networks?page=2"}},
        2: {"data": [{"id": "solana", "attributes": {"coingecko_asset_platform_id": "solana"}}],
            "links": {"next": None}},
    }
    http = FakeHTTP(lambda url, p: pages[p["page"]])
    assert dex.GeckoTerminal(http).networks() == {"ethereum": "eth", "solana": "solana"}
    assert len(http.calls) == 2


def test_dex_discovery_counterparts_and_floors():
    tokens = pd.DataFrame([dict(cg_id="nvda-rh", symbol="NVDA", family="coinbase", underlying="NVDA", price=200.0, vol24=5e6),
                           dict(cg_id="tsla-rh", symbol="TSLA", family="coinbase", underlying="TSLA", price=400.0, vol24=1e3)])
    addresses = pd.DataFrame([dict(cg_id="nvda-rh", platform="base", network="base", address="0xAAA"),
                              dict(cg_id="tsla-rh", platform="base", network="base", address="0xbbb")])
    tok = lambda a, s: {"id": f"base_{a}", "type": "token", "attributes": {"address": a, "symbol": s}}
    def h(url, p):
        assert url.endswith("/networks/base/tokens/0xAAA/pools")
        return {"data": [_pool("0xp1", "0xaaa", "NVDA", "0xusdg", "USDG", 50_000),
                         _pool("0xp2", "0xmeme", "AIMEME", "0xaaa", "NVDA", 60_000),
                         _pool("0xp3", "0xmeme2", "TINY", "0xaaa", "NVDA", 10_000),
                         _pool("0xp4", "0xaaa", "NVDA", "0xbbb", "TSLA", 5_000),
                         _pool("0xp5", "0xccc", "FAKE", "0xddd", "USDG", 1e9)],
                "included": [tok("0xaaa", "NVDA"), tok("0xusdg", "USDG"), tok("0xmeme", "AIMEME"), tok("0xmeme2", "TINY"),
                             tok("0xbbb", "TSLA"), tok("0xccc", "FAKE"), tok("0xddd", "USDG")]}
    http = FakeHTTP(h)
    ms = {m.native: m for m in dex.discover_pools(dex.GeckoTerminal(http), addresses, tokens, "now")}
    assert set(ms) == {"0xp1", "0xp2", "0xp4"}   # p3 under the memecoin floor; p5 has no registered token
    assert (ms["0xp1"].counterpart, ms["0xp1"].token_side) == ("usd", "base")
    assert (ms["0xp2"].counterpart, ms["0xp2"].token_side) == ("other", "quote")
    assert ms["0xp4"].counterpart == "stock" and ms["0xp4"].underlying == "NVDA"
    assert len(http.calls) == 1                   # TSLA sits under the discovery volume floor


def test_dex_ohlcv_pages_backwards():
    start, end = T0, T0 + 1500 * 900
    def h(url, p):
        assert url.endswith("/pools/0xp2/ohlcv/minute") and p["aggregate"] == 15 and p["token"] == "quote" and p["currency"] == "usd"
        ks = [k for k in range(start - 900 * 50, end, 900) if k < p["before_timestamp"]][-p["limit"]:]
        return {"data": {"attributes": {"ohlcv_list": [[k, 1, 2, 0.5, 1.5, 25.0] for k in ks[::-1]]}}}
    http = FakeHTTP(h)
    df = frame("dex-base:0xp2", 900, dex.GeckoTerminal(http).fetch("base", "0xp2", "quote", start, end), start, end)
    assert len(df) == 1500 and (df.vol_usd == 25).all() and len(http.calls) == 2


def test_alpaca_pages_and_dollar_volume():
    pages = {
        "": {"bars": {"TSLA": [{"t": "2026-11-16T14:30:00Z", "o": 400, "h": 401, "l": 399, "c": 400.5, "v": 1000, "vw": 400.2}]}, "next_page_token": "p2"},
        "p2": {"bars": {"NVDA": [{"t": "2026-11-16T14:30:00Z", "o": 190, "h": 191, "l": 189, "c": 190.5, "v": 10, "vw": 190.0}]}, "next_page_token": None},
    }
    def h(url, p):
        assert p["feed"] == "sip" and p["timeframe"] == "30Min" and p["symbols"] == "NVDA,TSLA" and p["adjustment"] == "raw"
        return pages[p.get("page_token", "")]
    http = FakeHTTP(h)
    rows = stocks.Alpaca(http, key="k", secret="s").bars(["TSLA", "NVDA"], T0, T0 + 1800)
    assert rows[0][:2] == ("TSLA", int(pd.Timestamp("2026-11-16T14:30:00Z").timestamp()))
    assert rows[0][6] == pytest.approx(400_200) and rows[1][0] == "NVDA"
    assert http.calls[0][2]["APCA-API-KEY-ID"] == "k"


@pytest.mark.parametrize("symbol,family,valid,expected", [
    ("tslax", "xstocks", None, "TSLA"), ("crclon", "ondo", None, "CRCL"), ("spcxb", "bstocks", None, "SPCX"),
    ("mub", "bstocks", None, "MU"), ("onon", "ondo", None, "ON"), ("ononon", "ondo", None, "ONON"),
    ("nvda", "robinhood", None, "NVDA"), ("brk-bx", "xstocks", None, "BRK.B"),
    ("tslax", "other", {"TSLA"}, "TSLA"), ("zzzz", "other", {"TSLA"}, ""), ("spyx", "xstocks", {"SPY", "SPYX"}, "SPY"),
])
def test_derive_ticker(symbol, family, valid, expected):
    assert universe.derive_ticker(symbol, family, valid) == expected


def test_family_from_category_name_and_overrides():
    assert universe.map_token(dict(cg_id="a", symbol="tslab", name="Tesla (bStocks Tokenized Stock)", families={"bstocks"}), None, {}) == ("bstocks", "TSLA", False)
    ov = {"b": {"cg_id": "b", "family": "ondo", "underlying": "aapl", "exclude": ""}}
    assert universe.map_token(dict(cg_id="b", symbol="weird", name="Something", families=set()), None, ov) == ("ondo", "AAPL", False)
    assert universe.name_family("Circle Internet Group (Ondo Tokenized Stock)") == "ondo"
    assert universe.name_family("Tesla xStock") == "xstocks"
    assert universe.category_family("bstocks-ecosystem", "bStocks Ecosystem") == "bstocks"


def test_discover_contracts_paginates_and_excludes_cash_and_other_chains():
    from pipeline.sources.issuer_pools import issuer_contracts, COINBASE_LIST
    a, b, c = ("0x" + ch * 40 for ch in "abc")
    def response(url, params):
        if url == COINBASE_LIST:
            if params.get("page_token") == "next":
                return {"tokens": [{"contract_address": b, "symbol": "RKTc", "name": "Rocket"}]}
            return {"tokens": [{"contract_address": a, "symbol": "BRK-Bc", "name": "Berkshire"}],
                    "next_page_token": "next"}
        return {"tokens": [
            {"chainId": 1, "address": c, "name": "Fund (Ondo Tokenized)", "symbol": "XYon"},
            {"chainId": 1, "address": b, "name": "Ondo U.S. Dollar Token", "symbol": "USDon"},
            {"chainId": 56, "address": a, "name": "Fund (Ondo Tokenized)", "symbol": "XYon"}]}
    out = issuer_contracts(FakeHTTP(response))
    assert {k: r["ticker"] for k, r in out.items()} == {("base", a): "BRK.B", ("base", b): "RKT", ("eth", c): "XY"}


def test_discover_issuer_checks_every_contract_and_matches_authoritative_sides():
    from pipeline.sources.issuer_pools import discover_issuer_pools, QUOTES
    a, b, fake = ("0x" + ch * 40 for ch in "abf")
    pool, wrong = ("0x" + ch * 64 for ch in "ab")
    usdc = next(k for k, v in QUOTES["base"].items() if v == "USDC")
    contracts = {("base", addr): dict(ticker=t, symbol=t+"c", name=t) for addr, t in [(a, "RKT"), (b, "HPE")]}
    def response(url, params):
        if "/token-pairs/" in url:
            return [dict(chainId="base", pairAddress=pool, baseToken={"address": a}, quoteToken={"address": usdc})]
        if "/tokens/multi/" in url:
            return {"included": [{"type":"pool","attributes":{"address":p}} for p in [pool, wrong]]}
        assert "/pools/multi/" in url
        def record(addr, token):
            return {"attributes":{"address":addr,"volume_usd":{"h24":"0"}},
                    "relationships": {s:{"data":{"id":"base_"+v}} for s,v in [("base_token", usdc),("quote_token",token)]}}
        return {"data":[record(pool, a),record(wrong, fake)]}
    http = FakeHTTP(response)
    markets, checks = discover_issuer_pools(http, contracts, [], "now")
    assert {u.rsplit("/",1)[-1] for u,_,_ in http.calls if "/token-pairs/" in u} == {a,b}
    assert len(markets) == 1
    m = markets[0]
    assert (m.underlying,m.token_side,m.token_address,m.counterpart,m.vol24) == ("RKT","quote",a,"usd",0)


def test_pool_snapshot_compares_dollars_within_the_same_index():
    from pipeline.sources.issuer_pools import index_coverage
    r = index_coverage("base", {"a": 10, "b": 30, "c": 0}, {"dex-base:a", "dex-eth:b", "dex-base:c"})
    assert r == dict(reported_pools=3, tracked_pools=2, reported_usd=40, tracked_usd=10)
