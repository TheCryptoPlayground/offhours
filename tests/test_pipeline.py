"""Storage to facts to the weekend test to site files, on hand-built bars with known answers."""
import json

import numpy as np
import pandas as pd
import pytest

from pipeline import aggregate, comparisons, pricing, site, store
from pipeline import config as C
from pipeline.sources.base import Market

H = 1800


def et(s):
    return int(pd.Timestamp(s, tz="America/New_York").timestamp())


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA", tmp_path / "data")
    monkeypatch.setattr(C, "SITE_DATA", tmp_path / "site")
    return tmp_path


def bars(mid, a, b, step=H, vol=1000.0, price=100.0):
    ts = np.arange(a, b, step)
    p = price(ts) if callable(price) else np.full(len(ts), price)
    return pd.DataFrame({"market_id": mid, "ts": ts, "bar_s": step, "open": p, "high": p, "low": p, "close": p, "vol_usd": vol})


def seed():
    """Mon 9 Nov to Mon 16 Nov 2026 noon. The token jumps to 105 over the weekend; the stock opens at 104."""
    a, b = et("2026-11-09 00:00"), et("2026-11-16 12:00")
    fri_close, sun_cut = et("2026-11-13 20:00"), et("2026-11-15 18:00")
    tok_price = lambda ts: np.where(ts < fri_close, 100.0, np.where(ts < sun_cut, 105.0, 104.0))
    store.write_bars("binance", bars("binance:TSLABUSDT", a, b, price=tok_price))
    store.write_bars("dex-base", bars("dex-base:0xpool", a, b, step=900, vol=10.0))
    frames = []
    for day in ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]:
        frames.append(bars("stocks:TSLA", et(f"{day} 04:00"), et(f"{day} 20:00"), vol=1e6))
    frames.append(bars("stocks:TSLA", et("2026-11-16 04:00"), et("2026-11-16 12:00"), vol=1e6, price=104.0))
    store.write_bars("stocks", pd.concat(frames, ignore_index=True))
    store.save_markets([
        Market(market_id="binance:TSLABUSDT", source="binance", native="TSLABUSDT", base="TSLAB", quote="USDT",
               cg_id="tsla-b", family="bstocks", underlying="TSLA", venue_type="cex", counterpart="usd"),
        Market(market_id="dex-base:0xpool", source="dex-base", native="0xpool", base="MEME", quote="TSLA",
               cg_id="tsla-rh", family="robinhood", underlying="TSLA", venue_type="dex", counterpart="other",
               network="base", token_side="quote", bar_s=900),
    ])


def test_calendar_and_matched_crossovers():
    cols = ["underlying", "market_date", "session", "vol_usd"]
    token = pd.DataFrame([("X", 1, "RTH", 80), ("X", 1, "PRE", 12), ("X", 2, "CLOSED", 50),
                          ("Y", 1, "POST", 0.001)], columns=cols)
    stock = pd.DataFrame([("X", 1, "RTH", 100), ("X", 1, "PRE", 10), ("Y", 1, "POST", 1000)], columns=cols)
    result = comparisons.volume_views(token, stock)
    assert result["calendar"][0] == dict(t="X", token=142, stock=110, days=1, above=0)
    assert result["matched"][0]["token"] == 92
    assert result["PRE"][0]["above"] == 1
    assert result["CLOSED"] == []
    assert result["POST"] == []


def test_history_uses_published_cents_and_preserves_one_sided_dates():
    date = pd.Timestamp("2026-11-13")
    columns = ["underlying", "market_date", "session", "vol_usd"]
    token = pd.DataFrame([("X", date, "PRE", 1.006), ("X", date, "PRE", 2.006),
                          ("", date, "PRE", 50)], columns=columns)
    stock = pd.DataFrame([("X", date, "RTH", 300)], columns=columns)
    rows = comparisons.volume_history(token, stock)["days"]["2026-11-13"]
    assert sorted(rows) == [["X", "PRE", 302, 0], ["X", "RTH", 0, 30000]]
    assert comparisons.volume_history(token.iloc[:0], stock.iloc[:0])["days"] == {}


def test_offhours_trend_uses_common_underlyings_and_keeps_weekends():
    cols = ["underlying", "market_date", "session", "vol_usd"]
    token = pd.DataFrame([("X", 1, "RTH", 50), ("X", 1, "PRE", 5),
                          ("X", 2, "CLOSED", 3), ("Y", 2, "CLOSED", 10)], columns=cols)
    stock = pd.DataFrame([("X", 1, "RTH", 100), ("X", 1, "PRE", 20),
                          ("Z", 1, "POST", 80)], columns=cols)
    assert comparisons.offhours_trend(token, stock, [1, 2]) == dict(
        tickers=1, total_token=[5, 13], token=[5, 3], stock_off=[20, 0], stock_all=[120, 0])


def test_rebuild_respects_collection_cutoff():
    assert site.last_full_date("2026-11-25T18:05:56Z") == pd.Timestamp("2026-11-24")


def test_adjacent_return_direction(paths):
    seed()
    days = pd.bdate_range("2026-11-09", "2026-11-13")
    rng = np.random.default_rng(12)
    for day in days:
        start = et(f"{day.date()} 09:30")
        moves = rng.normal(0, 0.01, 14)
        for source, mid, changes in [("binance", "binance:TSLABUSDT", moves[1:]), ("stocks", "stocks:TSLA", moves[:-1])]:
            b = bars(mid, start, start + 13 * H)
            b["close"] = b.open * np.exp(changes)
            store.write_bars(source, b)
    r = comparisons.lead_lag(store.load_markets(), days[0], days[-1])["RTH"]["TSLA"]
    assert r["n"] == 60  # 12 adjacent pairs per day, no overnight joins
    assert r["token_first"] == pytest.approx(1)
    assert abs(r["stock_first"]) < 0.5


def test_facts_by_session_and_market_date(paths):
    seed()
    f = aggregate.token_facts()
    head = f[f.counterpart == "usd"]
    by = head.groupby([head.market_date.astype(str), "session"]).vol_usd.sum()
    assert by[("2026-11-10", "OVERNIGHT")] == 16_000   # Mon 20:00 to Tue 04:00
    assert by[("2026-11-10", "PRE")] == 11_000 and by[("2026-11-10", "RTH")] == 13_000 and by[("2026-11-10", "POST")] == 8_000
    assert by[("2026-11-13", "CLOSED")] == 8_000      # Friday 20:00 to midnight
    assert by[("2026-11-14", "CLOSED")] == 48_000
    assert by[("2026-11-15", "CLOSED")] == 40_000 and by[("2026-11-16", "OVERNIGHT")] == 16_000
    assert head.vol_usd.sum() == pytest.approx(1000 * (7 * 48 + 24))
    assert f[f.counterpart == "other"].vol_usd.sum() == pytest.approx(10 * (7 * 96 + 48))
    s = aggregate.stock_facts()
    sb = s.groupby([s.market_date.astype(str), "session"]).vol_usd.sum()
    assert sb[("2026-11-10", "RTH")] == 13e6 and ("2026-11-14", "CLOSED") not in sb.index


def test_weekend_point(paths):
    seed()
    pts = pricing.closure_points(store.load_markets(), et("2026-11-17 12:00"))
    assert len(pts) == 1
    r = pts.iloc[0]
    assert r.underlying == "TSLA" and r.window == "2026-11-13" and r.next_date == "2026-11-16"
    assert r.x == pytest.approx(np.log(1.05)) and r.y == pytest.approx(np.log(1.04))
    assert r.token_vol == pytest.approx(92_000) and r.venues == 1   # 46 hours of half-hour bars, memecoin pool excluded


def test_clustered_ols_recovers_slope():
    rng = np.random.default_rng(0)
    g = np.repeat(np.arange(40), 10)
    x = rng.normal(0, 0.02, 400) + np.repeat(rng.normal(0, 0.02, 40), 10)
    y = 0.8 * x + rng.normal(0, 0.005, 400)
    r = pricing.ols(x, y, g)
    assert abs(r["beta"] - 0.8) < 0.05 and 0 < r["se"] < 0.05 and r["r2"] > 0.8
    d = pricing.describe(pd.DataFrame({"window": g, "x": x, "y": y}))
    assert d["windows"] == 40 and 0 < d["anticipated"] <= 1 and d["hit_rate"] > 0.8


def test_week_profile_uses_local_days_across_dst(paths, monkeypatch):
    """Midnight at the start of a 25-hour New York day must not fall out of the window."""
    first = et("2026-10-31 00:00")
    end = et("2026-11-03 00:00")
    one = bars("binance:TSLABUSDT", first, first + H, vol=123.0)
    monkeypatch.setattr(store, "read_days", lambda *args, **kwargs: one.copy())
    markets = pd.DataFrame([{"market_id": "binance:TSLABUSDT", "counterpart": "usd"}])
    profile = aggregate.week_profile(markets, end, days=3)
    assert profile["token"][5][0] == 123.0  # Saturday 00:00 ET


def test_site_build_writes_consistent_files(paths):
    seed()
    meta = site.build(end_date="2026-11-16")
    load = lambda n: json.loads((C.SITE_DATA / n).read_text())
    daily, week, tickers, closures = load("daily.json"), load("week.json"), load("tickers.json"), load("closures.json")
    assert daily["dates"][0] == "2026-11-09" and daily["dates"][-1] == "2026-11-16"
    assert daily["us_trading_day"] == [True, True, True, True, True, False, False, True]
    assert daily["product_venues"] == {"binance": {"bstocks": daily["venues"]["binance"]}}
    i = daily["dates"].index("2026-11-10")
    assert daily["token"]["RTH"][i] == 13_000 and daily["stock"]["RTH"][i] == 13e6
    assert sum(daily["token_other"]) == pytest.approx(10 * (7 * 96 + 48))
    k = meta["kpis"]
    assert k["days"] == 8 and k["start"] == "2026-11-09" and k["token_dates"] == 8
    assert k["token_total"] == pytest.approx(1000 * (7 * 48 + 24))
    assert k["share_stock"]["CLOSED"] == 0 and k["share_token"]["CLOSED"] > 0.25
    assert k["ratio_bp"] == pytest.approx(k["token_total"] / k["stock_total"] * 1e4)
    assert len(week["token"]) == 7 and len(week["token"][0]) == 48
    assert tickers["rows"][0]["t"] == "TSLA" and (C.SITE_DATA / "t" / "TSLA.json").exists()
    assert len(closures["points"]) == 1 and closures["points"][0]["t"] == "TSLA"
    assert (C.SITE_DATA / "token_daily.csv").exists() and (C.SITE_DATA / "closures.csv").exists()


def test_summaries_include_data_older_than_a_month(paths):
    seed()
    early = et("2026-09-14 09:30")
    store.write_bars("binance", bars("binance:TSLABUSDT", early, early + H, vol=9876))
    store.write_bars("stocks", bars("stocks:TSLA", early, early + H, vol=12345))
    meta = site.build(end_date="2026-11-16")
    tickers = json.loads((C.SITE_DATA / "tickers.json").read_text())
    week = json.loads((C.SITE_DATA / "week.json").read_text())
    series = json.loads((C.SITE_DATA / "t/TSLA.json").read_text())
    assert meta["first_date"] == tickers["start"] == "2026-09-14"
    assert meta["kpis"]["days"] == tickers["window_days"] == week["days"] == 64
    assert meta["kpis"]["token_total"] == tickers["rows"][0]["token"] == 9876 + 1000 * (7 * 48 + 24)
    assert tickers["comparisons"]["calendar"][0]["token"] == tickers["rows"][0]["token"]
    assert series["token"]["RTH"][0] == 9876
    assert week["token"][0][19] == round((9876 + 2000) / 10)


def test_site_build_removes_stale_optional_export(paths, monkeypatch):
    seed()
    site.build(end_date="2026-11-16")
    stock_csv = C.SITE_DATA / "stock_daily.csv"
    assert stock_csv.exists()
    monkeypatch.setattr(aggregate, "stock_facts", lambda: pd.DataFrame(columns=aggregate.STOCK_KEYS + ["vol_usd"]))
    site.build(end_date="2026-11-16")
    assert not stock_csv.exists()


def test_flat_token_returns_preserve_closure_counts():
    frame = pd.DataFrame({"window": np.repeat([1, 2, 3], 3), "x": np.zeros(9),
                          "y": np.linspace(-0.02, 0.02, 9)})
    result = pricing.describe(frame)
    assert result["n"] == 9 and result["anticipated"] == 0
    assert "beta" not in result


def test_session_ratio_excludes_control_stocks_and_partial_overnight_feed(paths):
    seed()
    start = et("2026-11-16 09:30")
    store.write_bars("stocks", bars("stocks:CONTROL", start, start + H, vol=1e12))
    night = et("2026-11-15 20:00")
    store.write_bars("stocks", bars("stocks:TSLA", night, night + H, vol=100))
    kpis = site.build(end_date="2026-11-16")["kpis"]
    stock = aggregate.stock_facts().query("underlying == 'TSLA' and session == 'RTH'").vol_usd.sum()
    token = kpis["token_total"] * kpis["share_token"]["RTH"]
    assert np.isclose(kpis["ratio_bp_by_session"]["RTH"], token / stock * 1e4)
    assert kpis["ratio_bp_by_session"]["OVERNIGHT"] is None
    assert kpis["ratio_bp_by_session"]["CLOSED"] is None


def test_retired_venue_files_do_not_reenter_a_build(paths):
    seed()
    start = et("2026-11-16 09:30")
    store.write_bars("retired", bars("retired:TSLA", start, start + H, vol=9e14))
    store.save_state({"cursor": {}, "sources": {"retired": {"ok": True}}})
    meta = site.build(end_date="2026-11-16")
    assert "retired" not in {s["id"] for s in meta["sources"]}
    assert not any(source == "retired" for source, _, _ in store.bar_files())
    assert store.read_days(["retired"], ["2026-11-16"]).empty
    assert meta["kpis"]["token_total"] < 9e14


def test_issuer_roster_counts_pools_and_preserves_unobserved_volume(paths):
    # The same ticker exists on both chains. Only its Base contract has archived trades.
    C.DATA.mkdir()
    listings = [("base", "a", "DUAL"), ("eth", "b", "DUAL"), ("eth", "c", "NONE"), ("eth", "d", "PENDING")]
    pd.DataFrame([dict(network=n, address=a, ticker=t, symbol=t, name=t) for n,a,t in listings]).to_csv(
        C.DATA / "issuer_contracts.csv", index=False)
    ms = [Market(market_id=f"dex-{n}:{pool}", source="dex-"+n, native=pool, base=t, quote="USDC",
                 underlying=t, token_address=addr, network=n)
          for n,addr,t,pool in [("base","a","DUAL","p1"),("base","a","DUAL","p2"),
                                ("eth","b","DUAL","p3"),("eth","d","PENDING","p4")]]
    markets = store.save_markets(ms)
    start = et("2026-11-10 10:00")
    later = et("2026-11-11 10:00")
    for mid in ["dex-base:p1", "dex-base:p2"]:
        store.write_bars("dex-base", pd.concat([bars(mid,start,start+900,step=900,vol=5),
                                               bars(mid,later,later+900,step=900,vol=100)]))
    state = dict(dex_active=[m.market_id for m in ms], cursor={m.market_id:later for m in ms[:3]})
    result = site.issuer_coverage(markets, state, pd.Timestamp("2026-11-10")).set_index("token_address")
    assert result.loc["a", ["tracked_pools","observed_usd","status"]].tolist() == [2,10,"Volume observed"]
    assert result.loc["b","status"] == "No positive volume returned in archive"
    assert result.loc["c","status"] == "No eligible indexed pool found"
    assert result.loc["d","status"] == "Pool history pending"
    assert result.loc[["b","c","d"],"observed_usd"].isna().all()


def test_kraken_unit_alias_counts_only_when_canonical_bar_missing():
    bars = pd.DataFrame({"market_id":["kraken:AAPLSPVUSD","kraken:AAPLxUSD","kraken:AAPLSPVUSD"],
                         "ts":[1,1,2]})
    assert store.unique_kraken_bars(bars).index.tolist() == [1,2]


def test_shared_venue_comparison_matches_each_ticker_and_date():
    venues = ["binance","kraken","dex-base","dex-eth"]
    rows = [(ticker, pd.Timestamp(day), venue, "product", session, dollars)
            for ticker, day, sources in [("ONE","2026-11-10",venues),("ONE","2026-11-11",venues[:2]),
                                         ("TWO","2026-11-11",venues[2:]),("TWO","2026-11-12",venues[:2])]
            for venue in sources for session,dollars in [("RTH", 5.25),("POST",3.50)]]
    df = pd.DataFrame(rows, columns=["underlying","market_date","source","family","session","vol_usd"])
    out = comparisons.venue_comparison(df)
    assert out["tickers"] == ["ONE","TWO"]
    assert len(out["matched"]) == 4
    assert all(r[0]=="2026-11-10" and r[3:]==[875,350,"ONE"] for r in out["matched"])


def test_core_matches_survive_expansion_and_new_venues_require_the_same_ticker_date():
    keys = [("A", "2026-11-22", C.CORE_SOURCES),
            ("A", "2026-11-23", [s for s in C.BAR_SOURCES if s != "stocks"]),
            ("B", "2026-11-22", ["gate","dex-bsc","dex-solana"])]
    rows = [(u,pd.Timestamp(d),src,"product","RTH",1.) for u,d,sources in keys for src in sources]
    facts = pd.DataFrame(rows, columns=["underlying","market_date","source","family","session","vol_usd"])
    result = comparisons.venue_comparison(facts)
    assert len(result["matched"]) == 8 and {r[1] for r in result["matched"]} == set(C.CORE_SOURCES)
    assert len(result["expanded"]) == 7 and {r[0] for r in result["expanded"]} == {"2026-11-23"}
    assert sum(r[3] for r in result["all"]) == len(rows)*100


def test_ranking_window_changes_leader_and_sums_only_selected_dates():
    dates = pd.date_range("2026-10-01", "2026-11-01")
    facts = pd.DataFrame([
        [dates[0], "A", "binance", "bstocks", "RTH", 200],
        [dates[-1], "B", "gate", "ondo", "CLOSED", 12.34],
        [dates[-1], "B", "gate", "xstocks", "PRE", 0.02],
    ], columns=["market_date", "underlying", "source", "family", "session", "vol_usd"])
    stocks = facts.iloc[:1]
    windows = comparisons.ranking_windows(facts, stocks, dates)
    assert windows["all"]["stock"] == {"A": 20000}
    assert windows["30"]["stock"] == {}
    assert windows["30"]["tokens"] == [["B", "gate", "ondo", 1234, 0, 1234],
                                      ["B", "gate", "xstocks", 2, 0, 0]]
    assert windows["1"]["start"] == "2026-11-01"
    assert windows["all"]["tokens"][0] == ["A", "binance", "bstocks", 20000, 20000, 0]
