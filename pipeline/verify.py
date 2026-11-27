"""Reconcile the published charts to the downloadable facts before deployment."""
import csv
import json
from collections import defaultdict
from decimal import Decimal

from . import config as C


def verify():
    root = C.SITE_DATA
    daily = json.loads((root / "daily.json").read_text())
    comparison = daily["venue_comparison"]
    sources = set(C.BAR_SOURCES) - {"stocks"}
    if set(comparison["all_sources"]) != sources or set(comparison["core_sources"]) != set(C.CORE_SOURCES):
        raise ValueError("Published cohort sources differ from the configured sources")

    totals, tickers = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    history = defaultdict(lambda: [0, 0])
    ranking = json.loads((root / "tickers.json").read_text())["rankings"]
    dates = daily["dates"]
    starts = {k: dates[-min(n, len(dates))] for k, n in (("all", len(dates)), ("30", 30), ("7", 7), ("1", 1))}
    token_windows = {k: defaultdict(lambda: [0, 0, 0]) for k in starts}
    stock_windows = {k: defaultdict(int) for k in starts}
    for side, filename in enumerate(("token_daily.csv", "stock_daily.csv")):
        with (root / filename).open(newline="") as file:
            for row in csv.DictReader(file):
                if side == 0 and row["counterpart"] not in C.HEADLINE_COUNTERPARTS:
                    continue
                day, ticker, session = row["market_date"], row["underlying"], row["session"]
                cents = int(Decimal(row["vol_usd"]) * 100)
                if ticker:
                    history[day, ticker, session][side] += cents
                    for period, first in starts.items():
                        if day < first:
                            continue
                        if side == 1:
                            stock_windows[period][ticker] += cents
                        else:
                            amounts = token_windows[period][ticker, row["source"], row["family"]]
                            amounts[0] += cents
                            if session == "RTH":
                                amounts[1] += cents
                            if session == "CLOSED":
                                amounts[2] += cents
                if side == 0:
                    source, family = row["source"], row["family"]
                    for pair in (totals[day, source, family], tickers[day, source, family, ticker]):
                        pair[0] += cents
                        if session != "RTH":
                            pair[1] += cents

    all_rows = [list(key) + amounts for key, amounts in totals.items()]
    if sorted(all_rows) != sorted(comparison["all"]):
        raise ValueError("Product and venue totals disagree with the token CSV")
    active = defaultdict(set)
    for (day, source, _, ticker), (cents, _) in tickers.items():
        if cents > 0 and ticker:
            active[day, ticker].add(source)
    for sample, required in (("matched", set(C.CORE_SOURCES)), ("expanded", sources)):
        rows = [[day, source, family, cents, outside, ticker]
                for (day, source, family, ticker), (cents, outside) in tickers.items()
                if cents > 0 and source in required and required <= active[day, ticker]]
        if sorted(rows) != sorted(comparison[sample]):
            raise ValueError(f"Incorrect ticker-date membership or dollars for the {sample} sample")

    published = json.loads((root / "volume_history.json").read_text())["days"]
    history_rows = sorted((day, ticker, session, *values) for (day, ticker, session), values in history.items() if any(values))
    chart_rows = sorted((day, *record) for day, records in published.items() for record in records)
    if history_rows != chart_rows:
        raise ValueError("Token versus stock history disagrees with downloadable session volumes")


    for period, first in starts.items():
        view = ranking[period]
        rows = [list(key) + amounts for key, amounts in token_windows[period].items() if amounts[0] > 0]
        if view["start"] != first or sorted(view["tokens"]) != sorted(rows) or view["stock"] != stock_windows[period]:
            raise ValueError(f"Stock ranking differs from downloadable facts for period {period}")
