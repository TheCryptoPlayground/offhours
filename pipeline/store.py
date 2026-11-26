"""On-disk layout (all under data/):

    bars/<source>/<YYYY-MM>/<YYYY-MM-DD>.parquet   bars by UTC day; a day is written once,
                                                   then touched again only for late fixes
    markets.csv                                    every market ever tracked, with its mapping
    state.json                                     fetch cursors and per-source status

Daily files keep git history close to the size of the data, which matters when a scheduled
job commits every day.
"""
from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Iterator

import pandas as pd

from . import config as C
from .sources.base import BAR_COLUMNS, Market

MARKET_COLUMNS = [f.name for f in fields(Market)]


def bars_dir() -> Path:
    return C.DATA / "bars"


def day_path(source: str, day: str) -> Path:
    return bars_dir() / source / day[:7] / f"{day}.parquet"


def write_bars(source: str, df: pd.DataFrame) -> int:
    """Upsert bars by (market_id, ts). Returns the number of rows written."""
    if df is None or df.empty:
        return 0
    df = df[BAR_COLUMNS]
    days = pd.to_datetime(df.ts, unit="s", utc=True).dt.strftime("%Y-%m-%d")
    for day, part in df.groupby(days.values):
        path = day_path(source, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            part = pd.concat([pd.read_parquet(path), part], ignore_index=True)
        part = part.drop_duplicates(["market_id", "ts"], keep="last").sort_values(["market_id", "ts"])
        part.to_parquet(path, index=False)
    return len(df)


def _sources(sources, exclude):
    # Older checkouts may still contain parquet files from retired venues.
    return [s for s in C.BAR_SOURCES if (sources is None or s in sources) and s not in exclude]


def bar_files(sources: list[str] | None = None, exclude: tuple[str, ...] = ()) -> Iterator[tuple[str, str, Path]]:
    for source in _sources(sources, exclude):
        for f in sorted((bars_dir() / source).glob("*/*.parquet")):
            yield source, f.stem, f


def read_days(sources: list[str] | None, days: list[str], exclude: tuple[str, ...] = (),
              columns: list[str] | None = None) -> pd.DataFrame:
    frames = []
    for source in _sources(sources, exclude):
        for day in days:
            path = day_path(source, day)
            if path.exists():
                frames.append(pd.read_parquet(path, columns=columns))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns or BAR_COLUMNS)


def load_markets() -> pd.DataFrame:
    p = C.DATA / "markets.csv"
    if not p.exists():
        return pd.DataFrame(columns=MARKET_COLUMNS)
    df = pd.read_csv(p, dtype=str, keep_default_na=False)
    df = df[df.source.isin(C.BAR_SOURCES)].copy()
    for col in ("bar_s",):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(C.BAR_S).astype(int)
    df["vol24"] = pd.to_numeric(df["vol24"], errors="coerce").fillna(0.0)
    return df


def save_markets(new: list[Market]) -> pd.DataFrame:
    """Merge new market records into markets.csv. Old markets stay so history keeps its mapping."""
    old = load_markets()
    add = pd.DataFrame([m.row() for m in new], columns=MARKET_COLUMNS)
    if not add.empty:
        keep_first_seen = old.set_index("market_id")["discovered"] if len(old) else pd.Series(dtype=str)
        add["discovered"] = add.market_id.map(keep_first_seen).fillna(add.discovered)
    merged = pd.concat([old[~old.market_id.isin(add.market_id)], add], ignore_index=True)
    merged = merged.sort_values(["source", "market_id"])
    C.DATA.mkdir(parents=True, exist_ok=True)
    merged.to_csv(C.DATA / "markets.csv", index=False)
    return merged


def load_state() -> dict:
    p = C.DATA / "state.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"cursor": {}, "sources": {}}


def save_state(state: dict) -> None:
    C.DATA.mkdir(parents=True, exist_ok=True)
    (C.DATA / "state.json").write_text(json.dumps(state, indent=1, sort_keys=True))
