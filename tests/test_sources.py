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


def test_bybit_newest_first_and_turnover():
    start, end = T0, T0 + 1500 * 1800
    def h(url, p):
        a, b = p["start"] // 1000, (p["end"] + 1) // 1000
        rows = [[str(t * 1000), "1", "2", "0.5", "1.5", "3", "42.5"] for t in range(a, b, 1800)]
        return {"retCode": 0, "result": {"list": rows[::-1]}}
    df = frame("bybit:TSLAXUSDT", 1800, cex.Bybit(FakeHTTP(h)).fetch("TSLAXUSDT", start, end), start, end)
    assert len(df) == 1500 and df.ts.is_monotonic_increasing and (df.vol_usd == 42.5).all()
    with pytest.raises(cex.SourceError):
        cex.Bybit(FakeHTTP(lambda u, p: {"retCode": 10001, "retMsg": "bad"})).fetch("X", T0, T0 + 1800)


def test_bitget_pages_backwards():
    start, end = T0, T0 + 1100 * 1800
    data = {t: [str(t * 1000), str(t), str(t + 2), str(t - 1), str(t + 1), "3", "7"]
            for t in range(start - 10 * 1800, end + 5 * 1800, 900)}
    def h(url, p):
        assert url.endswith("/api/v3/market/candles")
        assert p["category"] == "SPOT" and p["interval"] == "15m" and p["limit"] == 1000
        older = sorted(k for k in data if k < p["endTime"] // 1000)[-p["limit"]:]
        return {"code": "00000", "data": [data[k] for k in older]}
    http = FakeHTTP(h)
    df = frame("bitget:X", 1800, cex.Bitget(http).fetch("X", start, end), start, end)
    assert len(df) == 1100 and (df.vol_usd == 14).all() and len(http.calls) == 3
    first = df.iloc[0]
    assert (first.open, first.high, first.low, first.close) == (start, start + 902, start - 1, start + 901)


def test_okx_backwards_and_skips_unconfirmed_bars():
    start, end = T0, T0 + 250 * 1800
    def h(url, p):
        ks = [k for k in range(start - 1800 * 300, end, 1800) if k < p["after"] // 1000][-p["limit"]:]
        rows = [[str(k * 1000), "1", "2", "0.5", "1.5", "3", "4", "9.5", "0" if k == end - 1800 else "1"] for k in ks]
        return {"code": "0", "data": rows[::-1]}
    df = frame("okx:X", 1800, cex.OKX(FakeHTTP(h)).fetch("X", start, end), start, end)
    assert len(df) == 249 and end - 1800 not in set(df.ts) and (df.vol_usd == 9.5).all()


def test_gate_column_order():
    start, end = T0, T0 + 3 * 1800
    h = lambda url, p: [[str(t), "123.4", "10.5", "11", "9.5", "10", "12", "true"] for t in range(p["from"], p["to"] + 1, 1800)]
    r = frame("gate:X", 1800, cex.Gate(FakeHTTP(h)).fetch("X", start, end), start, end).iloc[0]
    assert (r.open, r.high, r.low, r.close, r.vol_usd) == (10, 11, 9.5, 10.5, 123.4)


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
    return {"id": f"robinhood_{addr}", "attributes": {"address": addr, "name": f"{bsym} / {qsym}", "volume_usd": {"h24": str(vol)}},
            "relationships": {"base_token": {"data": {"id": f"robinhood_{base}"}}, "quote_token": {"data": {"id": f"robinhood_{quote}"}},
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
    tokens = pd.DataFrame([dict(cg_id="nvda-rh", symbol="NVDA", family="robinhood", underlying="NVDA", price=200.0, vol24=5e6),
                           dict(cg_id="tsla-rh", symbol="TSLA", family="robinhood", underlying="TSLA", price=400.0, vol24=1e3)])
    addresses = pd.DataFrame([dict(cg_id="nvda-rh", platform="rh", network="robinhood", address="0xAAA"),
                              dict(cg_id="tsla-rh", platform="rh", network="robinhood", address="0xbbb")])
    tok = lambda a, s: {"id": f"robinhood_{a}", "type": "token", "attributes": {"address": a, "symbol": s}}
    def h(url, p):
        assert url.endswith("/networks/robinhood/tokens/0xAAA/pools")
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
    df = frame("dex-robinhood:0xp2", 900, dex.GeckoTerminal(http).fetch("robinhood", "0xp2", "quote", start, end), start, end)
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
