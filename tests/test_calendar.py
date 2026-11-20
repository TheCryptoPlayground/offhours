"""The session clock: DST switches, holidays, half days, weekends, market-date rollover."""
import pandas as pd
import pytest

from pipeline import calendar as K
from pipeline import config as C


def et(s):
    return int(pd.Timestamp(s, tz="America/New_York").timestamp())


def session(s):
    code, md = K.classify([et(s)])
    return C.SESSIONS[code[0]], str(pd.Timestamp(md[0]).date())


@pytest.mark.parametrize("when,expected,mdate", [
    ("2026-11-16 03:30", "OVERNIGHT", "2026-11-16"),  # Sunday night counts toward Monday
    ("2026-11-16 04:00", "PRE", "2026-11-16"),
    ("2026-11-16 09:00", "PRE", "2026-11-16"),
    ("2026-11-16 09:30", "RTH", "2026-11-16"),
    ("2026-11-16 15:30", "RTH", "2026-11-16"),
    ("2026-11-16 16:00", "POST", "2026-11-16"),
    ("2026-11-16 19:30", "POST", "2026-11-16"),
    ("2026-11-16 20:00", "OVERNIGHT", "2026-11-17"),  # evening rolls to the next trading day
    ("2026-11-20 20:00", "CLOSED", "2026-11-20"),     # Friday night
    ("2026-11-21 12:00", "CLOSED", "2026-11-21"),
    ("2026-11-22 19:30", "CLOSED", "2026-11-22"),
    ("2026-11-22 20:00", "OVERNIGHT", "2026-11-23"),
    ("2026-11-25 20:00", "CLOSED", "2026-11-25"),     # eve of Thanksgiving
    ("2026-11-26 12:00", "CLOSED", "2026-11-26"),     # Thanksgiving
    ("2026-11-26 20:00", "OVERNIGHT", "2026-11-27"),
    ("2026-11-27 12:30", "RTH", "2026-11-27"),
    ("2026-11-27 13:00", "POST", "2026-11-27"),       # half day closes at 13:00
    ("2026-11-27 16:30", "POST", "2026-11-27"),
    ("2026-11-27 17:00", "CLOSED", "2026-11-27"),
    ("2026-03-08 19:30", "CLOSED", "2026-03-08"),     # clocks went forward that morning
    ("2026-03-08 20:00", "OVERNIGHT", "2026-03-09"),
    ("2026-03-09 09:30", "RTH", "2026-03-09"),
    ("2026-11-01 20:00", "OVERNIGHT", "2026-11-02"),  # clocks went back that morning
    ("2026-11-02 09:30", "RTH", "2026-11-02"),
])
def test_classify(when, expected, mdate):
    assert session(when) == (expected, mdate)


@pytest.mark.parametrize("utc,expected", [
    ("2026-07-15 13:30", "RTH"), ("2026-07-15 13:00", "PRE"),   # EDT: open is 13:30 UTC
    ("2026-12-15 14:30", "RTH"), ("2026-12-15 14:00", "PRE"),   # EST: open is 14:30 UTC
])
def test_open_follows_dst_in_utc(utc, expected):
    code, _ = K.classify([int(pd.Timestamp(utc, tz="UTC").timestamp())])
    assert C.SESSIONS[code[0]] == expected


def test_closure_windows_around_thanksgiving():
    w = K.closure_windows()
    sel = w[(w.start >= et("2026-11-15")) & (w.start < et("2026-12-01"))]
    got = [((r.end - r.start) / 3600, str(pd.Timestamp(r.next_date).date()), r.next_open) for r in sel.itertuples()]
    assert got == [(48.0, "2026-11-23", et("2026-11-23 09:30")),
                   (24.0, "2026-11-27", et("2026-11-27 09:30")),
                   (51.0, "2026-11-30", et("2026-11-30 09:30"))]


def test_intervals_tile_without_overlap():
    T = K.tables()
    assert (T.ends > T.starts).all() and (T.starts[1:] >= T.ends[:-1]).all()


def test_week_template():
    t = K.week_template()
    assert len(t) == 7 and all(len(r) == 48 for r in t)
    assert t[0][0] == K.OVN and t[0][18] == K.PRE and t[0][19] == K.RTH and t[0][32] == K.POST
    assert t[4][40] == K.CLOSED and t[5] == [K.CLOSED] * 48 and t[6][39] == K.CLOSED and t[6][40] == K.OVN
