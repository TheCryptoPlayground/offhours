"""Write everything the static site reads into site/data/."""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import aggregate, calendar, comparisons, pricing, store, universe
from . import config as C

log = logging.getLogger("offhours")


def _dump(name: str, obj) -> None:
    C.SITE_DATA.mkdir(parents=True, exist_ok=True)
    (C.SITE_DATA / name).write_text(json.dumps(obj, separators=(",", ":"), allow_nan=False))


def _clean(x):
    """JSON-safe numbers: NaN/inf to None, numpy scalars to Python."""
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else float(x)
    if isinstance(x, np.integer):
        return int(x)
    return x


def _series(df: pd.DataFrame, dates: pd.DatetimeIndex, by: str, keys: list[str]) -> dict[str, list]:
    p = df.pivot_table(index="market_date", columns=by, values="vol_usd", aggfunc="sum")
    p = p.reindex(index=dates, columns=keys).fillna(0.0).round(0)
    return {k: p[k].tolist() for k in keys}


def _share(d: dict[str, float]) -> dict[str, float]:
    tot = sum(d.values())
    return {k: (v / tot if tot else None) for k, v in d.items()}


def last_full_date(finished=None) -> pd.Timestamp:
    # A rebuild can happen days after collection stopped mid-session.
    cutoff = pd.Timestamp(finished).tz_convert(C.MARKET_TZ) if finished else pd.Timestamp.now(tz=C.MARKET_TZ)
    return cutoff.tz_localize(None).normalize() - pd.Timedelta(days=1)


def _observed(facts):
    dates = facts.market_date
    return dict(first_date=dates.min().strftime("%Y-%m-%d") if len(dates) else None,
                last_date=dates.max().strftime("%Y-%m-%d") if len(dates) else None,
                tickers=sorted(set(facts.underlying.dropna()) - {""}))


def issuer_coverage(markets, state, end):
    """One row per issuer contract, with volume attributed only by its verified pool IDs."""
    catalog_file = C.DATA / "issuer_contracts.csv"
    output = C.SITE_DATA / "issuer_coverage.csv"
    if not catalog_file.exists():
        output.unlink(missing_ok=True)
        return pd.DataFrame()
    catalog = pd.read_csv(catalog_file, dtype=str, keep_default_na=False)
    catalog["family"] = catalog.network.map({"base": "coinbase", "eth": "ondo"})
    extra = C.DATA / "extra_contracts.csv"
    if extra.exists():
        catalog = pd.concat([catalog, pd.read_csv(extra, dtype=str, keep_default_na=False)], ignore_index=True)
    pools = markets[markets.token_address != ""]
    active = pools[pools.market_id.isin(state.get("dex_active", []) + state.get("extra_active", []))]
    keys = ["network", "token_address"]
    indexed = catalog.rename(columns={"address": "token_address"}).set_index(keys)
    indexed["tracked_pools"] = active.groupby(keys).size().reindex(indexed.index, fill_value=0)
    collected = active[active.market_id.isin(state.get("cursor", {}))]
    indexed["pools_collected"] = collected.groupby(keys).size().reindex(indexed.index, fill_value=0)
    facts = []
    for _, _, path in store.bar_files(sources=["dex-" + n for n in (*C.DEX_NETWORKS, *C.EXTRA_NETWORKS)]):
        bars = pd.read_parquet(path, columns=["market_id", "ts", "vol_usd"])
        bars = bars[bars.market_id.isin(pools.market_id) & (bars.vol_usd > 0)].copy()
        if len(bars):
            bars["market_date"] = pd.to_datetime(calendar.classify(bars.ts.values)[1])
            bars = bars[bars.market_date <= end]
            facts.append(bars.groupby("market_id").agg(first_date=("market_date", "min"),
                         last_date=("market_date", "max"), observed_usd=("vol_usd", "sum")))
    if facts:
        by_pool = pd.concat(facts).join(pools.set_index("market_id")[keys])
        measured = by_pool.groupby(keys).agg(dict(first_date="min", last_date="max", observed_usd="sum"))
        indexed = indexed.join(measured)
    else:
        indexed = indexed.assign(first_date=pd.NaT, last_date=pd.NaT, observed_usd=np.nan)
    indexed["status"] = np.select(
        [indexed.observed_usd.notna(), indexed.tracked_pools == 0, indexed.pools_collected < indexed.tracked_pools],
        ["Volume observed", "No eligible indexed pool found", "Pool history pending"],
        default="No positive volume returned in archive")
    indexed["catalog_checked_at"] = state.get("issuer_catalog_at", "")
    indexed.loc[indexed.index.get_level_values("network").isin(C.EXTRA_NETWORKS), "catalog_checked_at"] = state.get("extra_discovered_at", "")
    outside_sample = indexed.index.get_level_values("network").isin(C.EXTRA_NETWORKS) & (indexed.tracked_pools == 0) & indexed.observed_usd.isna()
    indexed.loc[outside_sample, "status"] = "No pool selected in latest ranked sample"
    indexed.loc[(indexed.tracked_pools == 0) & indexed.observed_usd.notna(), "status"] = "Historical volume; no current pool selected"
    indexed["archive_through"] = end.strftime("%Y-%m-%d")
    result = indexed.reset_index().sort_values(["network", "ticker", "token_address"])
    C.SITE_DATA.mkdir(parents=True, exist_ok=True)
    result.to_csv(output, index=False, float_format="%.2f")
    return result


def build(demo: bool = False, end_date: str | None = None) -> dict:
    """end_date pins the last market date (tests, backfills); by default it is yesterday in New York."""
    state = store.load_state()
    completed_date = last_full_date((state.get("last_run") or {}).get("finished"))
    # A partial refresh cannot establish that the other feeds have finished a newer date.
    for name in ("binance", "kraken", "dex", "stocks"):
        finished = state.get("sources", {}).get(name, {}).get("finished")
        if finished:
            completed_date = min(completed_date, last_full_date(finished))
    markets = store.load_markets()
    tf = aggregate.token_facts(markets)
    sf = aggregate.stock_facts()
    for df in (tf, sf):
        if len(df):
            df["market_date"] = pd.to_datetime(df.market_date)
    last_dates = [f.market_date.max() for f in (tf, sf) if len(f)]
    if end_date is not None:
        end = pd.Timestamp(end_date)
    elif last_dates:
        latest = max(last_dates)
        end = latest if demo else min(completed_date, latest)
    else:
        end = completed_date
    tf = tf[tf.market_date <= end] if len(tf) else tf
    sf = sf[sf.market_date <= end] if len(sf) else sf
    head = tf[tf.counterpart.isin(C.HEADLINE_COUNTERPARTS)]
    other = tf[tf.counterpart == "other"]
    starts = [f.market_date.min() for f in (tf, sf) if len(f)]
    start = min(starts, default=end)
    dates = pd.date_range(start, end, freq="D") if starts else pd.DatetimeIndex([])
    S = C.SESSIONS
    venues = sorted(head.source.unique()) if len(head) else []
    families = sorted(head.family.unique()) if len(head) else []
    daily = dict(
        dates=[d.strftime("%Y-%m-%d") for d in dates],
        us_trading_day=dates.isin(pd.to_datetime(calendar.tables().days)).tolist(),
        token=_series(head, dates, "session", S) if len(head) else {s: [] for s in S},
        venue_comparison=comparisons.venue_comparison(head),
        offhours=comparisons.offhours_trend(head, sf, dates),
        token_other=_series(other.assign(k="x"), dates, "k", ["x"])["x"] if len(other) else [0.0] * len(dates),
        stock=_series(sf, dates, "session", S) if len(sf) else {s: [0.0] * len(dates) for s in S},
        venues=_series(head, dates, "source", venues) if venues else {},
        product_venues={
            source: _series(group, dates, "family", sorted(group.family.unique()))
            for source, group in head.groupby("source")
        },
        families=_series(head, dates, "family", families) if families else {},
    )

    tok_s = {s: float(head[head.session == s].vol_usd.sum()) if len(head) else 0.0 for s in S}
    stk_s = {s: float(sf[sf.session == s].vol_usd.sum()) if len(sf) else 0.0 for s in S}
    with_stock = set(sf.underlying) if len(sf) else set()
    mapped = head[head.underlying.isin(with_stock)] if len(head) else head
    tok_m = {s: float(mapped[mapped.session == s].vol_usd.sum()) if len(mapped) else 0.0 for s in S}
    common_stocks = sf[sf.underlying.isin(mapped.underlying)] if len(sf) else sf
    common_stock_s = common_stocks.groupby("session").vol_usd.sum().to_dict()
    common_stock_total = sum(common_stock_s.values())
    total = sum(tok_s.values())
    kpis = dict(
        days=len(dates), target_days=len(dates), start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"),
        token_dates=int(head.market_date.nunique()) if len(head) else 0,
        stock_dates=int(sf.market_date.nunique()) if len(sf) else 0,
        token_total=total, token_other=float(other.vol_usd.sum()) if len(other) else 0.0,
        outside_rth_share=(1 - tok_s["RTH"] / total) if total else None,
        closed_share=(tok_s["CLOSED"] / total) if total else None,
        stock_total=sum(stk_s.values()),
        ratio_bp=(sum(tok_m.values()) / common_stock_total * 1e4) if common_stock_total else None,
        share_token=_share(tok_s), share_stock=_share(stk_s),
        ratio_bp_by_session={s: (tok_m[s] / common_stock_s[s] * 1e4
                                 if s in ("RTH", "PRE", "POST") and common_stock_s.get(s) else None) for s in S},
    )

    # tickers
    profiles = pd.read_csv(C.ROOT / "config/security_profiles.csv", keep_default_na=False).set_index("ticker").to_dict("index")
    roster = issuer_coverage(markets, state, end)
    for entry in sorted(roster.itertuples(), key=lambda r: r.network not in C.DEX_NETWORKS):
        profiles.setdefault(entry.ticker, {"name": entry.name.split(" (Ondo Tokenized")[0]})
    rows = []
    if len(head):
        g = head[head.underlying != ""].groupby("underlying")
        stock_by = sf.groupby("underlying").vol_usd.sum() if len(sf) else pd.Series(dtype=float)
        for u, d in g:
            ss = d.groupby("session").vol_usd.sum()
            tot = float(d.vol_usd.sum())
            stock = float(stock_by.get(u, 0.0))
            rows.append(dict(t=u, **profiles.get(u, {}), token=tot, rth=float(ss.get("RTH", 0)), pre=float(ss.get("PRE", 0)),
                             post=float(ss.get("POST", 0)), ovn=float(ss.get("OVERNIGHT", 0)),
                             closed=float(ss.get("CLOSED", 0)), stock=stock or None,
                             ratio_bp=(tot / stock * 1e4) if stock else None,
                             venues=sorted(d.source.unique()), families=sorted(d.family.unique())))
        rows.sort(key=lambda r: -r["token"])
    tdir = C.SITE_DATA / "t"
    if tdir.exists():
        shutil.rmtree(tdir)
    tdir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        u = row["t"]
        hd, sd = head[head.underlying == u], sf[sf.underlying == u] if len(sf) else sf
        ser = dict(t=u, dates=daily["dates"], token=_series(hd, dates, "session", S),
                   stock=_series(sd, dates, "session", S) if len(sd) else None,
                   venues=_series(hd, dates, "source", sorted(hd.source.unique())))
        (tdir / f"{u}.json").write_text(json.dumps(_clean(ser), separators=(",", ":")))
        row["series"] = True

    # closure-window test
    archive_end_ts = int((end + pd.Timedelta(days=1)).tz_localize(C.MARKET_TZ).timestamp())
    pts = pricing.closure_points(markets, archive_end_ts)
    summ = pricing.summary(pts)
    closures = dict(
        definition=dict(end_buffer_h=C.CLOSURE_END_BUFFER_H, min_token_vol=C.CLOSURE_MIN_TOKEN_VOL,
                        max_abs_logret=C.CLOSURE_MAX_ABS_LOGRET, min_hours=C.CLOSURE_MIN_HOURS),
        points=[dict(w=r.window, d=r.next_date, t=r.underlying, x=round(r.x, 5), y=round(r.y, 5),
                     v=round(r.token_vol)) for r in pts.itertuples()],
        **summ,
    )

    # universe, coverage, sources
    uni = universe.load() or {"meta": {}, "tokens": pd.DataFrame()}
    umeta = dict(uni["meta"])
    toks = uni["tokens"]
    if len(toks):
        umeta["families"] = toks.family.value_counts().to_dict()
    coverage = None
    if umeta.get("fetched_at") and umeta.get("coingecko_vol24"):
        t1 = int(pd.Timestamp(umeta["fetched_at"]).timestamp()) // C.BAR_S * C.BAR_S
        t0 = t1 - 86400
        days = pd.date_range(pd.Timestamp(t0, unit="s").normalize(), pd.Timestamp(t1, unit="s").normalize()).strftime("%Y-%m-%d").tolist()
        b = store.read_days(None, days, exclude=("stocks",), columns=["market_id", "ts", "vol_usd"])
        b = b[(b.ts >= t0) & (b.ts < t1)]
        coverage = dict(as_of=umeta["fetched_at"], window_end=pd.Timestamp(t1, unit="s", tz="UTC").isoformat(),
                        measured_24h=float(b.vol_usd.sum()),
                        coingecko_24h=float(umeta["coingecko_vol24"]))
    sources = []
    source_state = state.get("sources", {})
    expected_sources = C.CEX_SOURCES + ["dex", "extra", "stocks"]
    for sid in expected_sources:
        st = source_state.get(sid, {})
        observed = sf if sid == "stocks" else tf[tf.source == sid]
        pool_groups = []
        if sid in ("dex", "extra"):
            networks = C.DEX_NETWORKS if sid == "dex" else C.EXTRA_NETWORKS
            active_key = "dex_active" if sid == "dex" else "extra_active"
            observed = tf[tf.source.isin(["dex-" + n for n in networks])]
            active = markets[markets.market_id.isin(state.get(active_key, []))]
            active_counts = active.groupby(["source", "family"]).market_id.nunique()
            grouped = observed.groupby(["source", "family"])
            catalog_groups = {("dex-" + r.network, r.family) for r in roster.itertuples() if r.network in networks} if len(roster) else set()
            for network, family in sorted(set(grouped.groups) | set(active_counts.index) | catalog_groups):
                key = (network, family)
                group = grouped.get_group(key) if key in grouped.groups else observed.iloc[:0]
                count = int(active_counts.get(key, 0)) if active_key in state and st.get("markets") is not None else None
                listing = roster[(roster.network == network.removeprefix("dex-")) & (roster.family == family)] if len(roster) else roster
                pool_groups.append(dict(network=network, family=family, markets=count,
                                        listed=len(listing) if len(listing) else None,
                                        with_pool=int((listing.tracked_pools > 0).sum()) if len(listing) else None,
                                        **_observed(group)))
        sources.append(dict(**_observed(observed), pool_groups=pool_groups, id=sid,
                            label=C.VENUE_LABELS.get(sid, sid.title()),
                            type=st.get("type"), ok=st.get("ok"), markets=st.get("markets"),
                            errors=st.get("errors"), discovery_errors=st.get("discovery_errors"),
                            pending_pools=len(set(state.get(active_key, [])) - state.get("cursor", {}).keys()) if sid in ("dex", "extra") else None,
                            error=st.get("error") or st.get("last_error"),
                            started=st.get("started"), finished=st.get("finished")))
    check_file = C.DATA / "issuer_pool_check.json"
    extra_check = C.DATA / "extra_pool_check.json"
    protocol = C.DATA / "ondo_protocol.csv"
    protocol_dest = C.SITE_DATA / "ondo_protocol.csv"
    if protocol.exists():
        C.SITE_DATA.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(protocol, protocol_dest)
        days = pd.read_csv(protocol)
        daily["ondo_protocol"] = _clean(days.to_dict("records"))
    else:
        protocol_dest.unlink(missing_ok=True)
    meta = dict(
        protocol_status=state.get("sources", {}).get("ondo-protocol"),
        extra_pool_check=json.loads(extra_check.read_text()) if extra_check.exists() else None,
        pool_volume_check=json.loads(check_file.read_text()) if check_file.exists() else None,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), demo=demo,
        first_date=daily["dates"][0] if daily["dates"] else None, last_date=daily["dates"][-1] if daily["dates"] else None,
        history=dict(calendar_days=len(dates), token_dates=int(tf.market_date.nunique()) if len(tf) else 0,
                     stock_dates=int(sf.market_date.nunique()) if len(sf) else 0),
        sessions=[dict(id=s, label=C.SESSION_LABELS[s], hours=C.SESSION_HOURS[s]) for s in S],
        events=[dict(date=C.US_23X5_START.isoformat(), label="Scheduled extension of US consolidated tape hours")],
        venue_labels=C.VENUE_LABELS, kpis=kpis, universe=umeta, coverage=coverage, sources=sources,
        markets=dict(total=int(len(markets)), by_source=markets.source.value_counts().to_dict() if len(markets) else {}),
        last_run=state.get("last_run"), issuer_catalog_at=state.get("issuer_catalog_at"),
    )

    week = aggregate.week_profile(markets, archive_end_ts, days=len(dates))

    _dump("meta.json", _clean(meta))
    _dump("daily.json", _clean(daily))
    _dump("volume_history.json", comparisons.volume_history(head, sf))
    _dump("tickers.json", _clean(dict(window_days=len(dates), target_window_days=len(dates),
                                      start=kpis["start"], end=kpis["end"], rows=rows,
                                      rankings=comparisons.ranking_windows(head, sf, dates),
                                      comparisons=comparisons.volume_views(head, sf),
                                      lead_lag=comparisons.lead_lag(markets, start, end))))
    _dump("closures.json", _clean(closures))
    _dump("week.json", _clean(week))
    token_csv = C.SITE_DATA / "token_daily.csv"
    if len(tf):
        out = tf.assign(market_date=tf.market_date.dt.strftime("%Y-%m-%d"))
        out.to_csv(token_csv, index=False, float_format="%.2f")
    else:
        token_csv.unlink(missing_ok=True)
    stock_csv = C.SITE_DATA / "stock_daily.csv"
    if len(sf):
        sf.assign(market_date=sf.market_date.dt.strftime("%Y-%m-%d")).to_csv(
            stock_csv, index=False, float_format="%.2f")
    else:
        stock_csv.unlink(missing_ok=True)
    pts.to_csv(C.SITE_DATA / "closures.csv", index=False, float_format="%.6f")
    log.info("site data written: %d days, %d tickers, %d closure points", len(dates), len(rows), len(pts))
    return meta
