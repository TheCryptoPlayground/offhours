"""US equity session clock.

Every bar start (UTC epoch seconds) maps to one session and one market date:

    OVERNIGHT  20:00 ET on the previous calendar day to 04:00 ET, ahead of a trading day
    PRE        04:00 to the open
    RTH        open to close (09:30 to 16:00, or 13:00 on half days)
    POST       close to close + 4h
    CLOSED     anything else (weekends from Friday 20:00 ET, holidays, half-day evenings)

Sessions that belong to a trading day carry that day's date, so overnight trading from
20:00 counts toward the next trading day (the same convention the SIP uses from 6 Dec 2026).
CLOSED bars carry their own calendar date in New York.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import exchange_calendars as xcals
import numpy as np
import pandas as pd

from . import config as C

CODE = {s: i for i, s in enumerate(C.SESSIONS)}
RTH, PRE, POST, OVN, CLOSED = (CODE[s] for s in ("RTH", "PRE", "POST", "OVERNIGHT", "CLOSED"))


@dataclass(frozen=True)
class Tables:
    starts: np.ndarray   # int64 epoch seconds, sorted
    ends: np.ndarray
    codes: np.ndarray    # int8 session codes
    mdates: np.ndarray   # datetime64[D] market date of each interval
    days: np.ndarray     # datetime64[D] trading days
    opens: np.ndarray    # int64, per trading day
    closes: np.ndarray


def _epoch(idx: pd.DatetimeIndex) -> np.ndarray:
    return (idx.tz_convert("UTC") - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)


def _wall(days: pd.DatetimeIndex, hour: int, minute: int = 0) -> np.ndarray:
    """Wall-clock time in New York on each day, as UTC epoch seconds (DST-aware)."""
    naive = days + pd.Timedelta(hours=hour, minutes=minute)
    return np.asarray(_epoch(naive.tz_localize(C.MARKET_TZ)), dtype=np.int64)


@lru_cache(maxsize=1)
def tables() -> Tables:
    end = (pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    sched = xcals.get_calendar("XNYS", start="2023-01-03", end=end).schedule
    days = pd.DatetimeIndex(sched.index).tz_localize(None).normalize()
    opens = np.asarray(_epoch(pd.DatetimeIndex(sched["open"])), dtype=np.int64)
    closes = np.asarray(_epoch(pd.DatetimeIndex(sched["close"])), dtype=np.int64)
    pre = _wall(days, C.PRE_START.hour, C.PRE_START.minute)
    ovn = _wall(days - pd.Timedelta(days=1), C.OVERNIGHT_START.hour, C.OVERNIGHT_START.minute)
    post_end = closes + C.POST_HOURS * 3600

    starts = np.column_stack([ovn, pre, opens, closes]).ravel()
    ends = np.column_stack([pre, opens, closes, post_end]).ravel()
    codes = np.tile(np.array([OVN, PRE, RTH, POST], dtype=np.int8), len(days))
    d64 = days.values.astype("datetime64[D]")
    if np.any(starts[1:] < ends[:-1]):
        raise RuntimeError("session intervals overlap; check the calendar settings")
    return Tables(starts, ends, codes, np.repeat(d64, 4), d64, opens, closes)


def et_dates(ts: np.ndarray) -> np.ndarray:
    t = pd.to_datetime(np.asarray(ts, dtype=np.int64), unit="s", utc=True).tz_convert(C.MARKET_TZ)
    return t.tz_localize(None).normalize().values.astype("datetime64[D]")


def classify(ts) -> tuple[np.ndarray, np.ndarray]:
    """Session code and market date for each bar start."""
    ts = np.asarray(ts, dtype=np.int64)
    T = tables()
    i = np.searchsorted(T.starts, ts, side="right") - 1
    ic = np.clip(i, 0, None)
    inside = (i >= 0) & (ts < T.ends[ic])
    code = np.where(inside, T.codes[ic], CLOSED).astype(np.int8)
    mdate = np.where(inside, T.mdates[ic], et_dates(ts))
    return code, mdate


def closure_windows() -> pd.DataFrame:
    """Stretches of at least CLOSURE_MIN_HOURS when no US venue is in session."""
    T = tables()
    gap_start, gap_end = T.ends[:-1], T.starts[1:]
    k = np.nonzero(gap_end - gap_start >= C.CLOSURE_MIN_HOURS * 3600)[0]
    prev_day, next_day = k // 4, (k + 1) // 4
    return pd.DataFrame({
        "start": gap_start[k],
        "end": gap_end[k],
        "prev_close": T.closes[prev_day],
        "next_open": T.opens[next_day],
        "prev_date": T.days[prev_day],
        "next_date": T.days[next_day],
    })


def week_slots(ts) -> tuple[np.ndarray, np.ndarray]:
    """New York weekday (Mon=0) and half-hour slot (0..47) of each timestamp."""
    t = pd.to_datetime(np.asarray(ts, dtype=np.int64), unit="s", utc=True).tz_convert(C.MARKET_TZ)
    return np.asarray(t.dayofweek), np.asarray(t.hour * 2 + t.minute // 30)


def week_template() -> list[list[int]]:
    """Session of each half hour in an ordinary week with no holidays (rows Mon..Sun)."""
    def slot(h, m=0):
        return h * 2 + m // 30
    weekday = [OVN] * slot(4) + [PRE] * (slot(9, 30) - slot(4)) + [RTH] * (slot(16) - slot(9, 30)) \
        + [POST] * (slot(20) - slot(16)) + [OVN] * (48 - slot(20))
    friday = weekday[:slot(20)] + [CLOSED] * (48 - slot(20))
    saturday = [CLOSED] * 48
    sunday = [CLOSED] * slot(20) + [OVN] * (48 - slot(20))
    return [weekday[:], weekday[:], weekday[:], weekday[:], friday, saturday, sunday]
