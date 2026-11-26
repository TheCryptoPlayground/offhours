"""Settings. Paths are read at call time so a run can point DATA elsewhere (the demo does)."""
from __future__ import annotations

import os
from datetime import date, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("OFFHOURS_DATA", ROOT / "data"))
SITE_DATA = ROOT / "site" / "data"
OVERRIDES = ROOT / "config" / "overrides.csv"

# --- Bars -------------------------------------------------------------------------------
# Every US session boundary (04:00, 09:30, 13:00, 16:00, 17:00, 20:00 ET) sits on a half hour,
# so 30-minute bars never straddle two sessions. GeckoTerminal has no 30m candle, so DEX
# pools use 15m, which aligns too.
BAR_S = 1800
DEX_BAR_S = 900

# --- US session clock (America/New_York) ---------------------------------------------
MARKET_TZ = "America/New_York"
PRE_START = time(4, 0)
POST_HOURS = 4                 # after-hours ends 4h after the close: 20:00, or 17:00 on half days
OVERNIGHT_START = time(20, 0)  # weeknight window that leads into the next trading day
US_23X5_START = date(2026, 12, 6)  # scheduled start of exchange night sessions on the SIP

SESSIONS = ["RTH", "PRE", "POST", "OVERNIGHT", "CLOSED"]
SESSION_LABELS = {
    "RTH": "Regular hours",
    "PRE": "Pre-market",
    "POST": "After-hours",
    "OVERNIGHT": "Overnight",
    "CLOSED": "Closed",
}
SESSION_HOURS = {
    "RTH": "09:30 to 16:00 ET on trading days (13:00 on half days)",
    "PRE": "04:00 to 09:30 ET",
    "POST": "16:00 to 20:00 ET (13:00 to 17:00 on half days)",
    "OVERNIGHT": "20:00 to 04:00 ET on nights that lead into a trading day",
    "CLOSED": "Everything else: weekends from Friday 20:00 ET, and holidays",
}

# --- History ----------------------------------------------------------------------------
BACKFILL_DAYS = int(os.environ.get("OFFHOURS_BACKFILL_DAYS", 120))
DEX_BACKFILL_DAYS = int(os.environ.get("OFFHOURS_DEX_BACKFILL_DAYS", 35))
REFETCH_BARS = 2               # re-read the last bars each run in case a venue amends them
UNIVERSE_MAX_AGE_DAYS = 7

# --- Universe ---------------------------------------------------------------------------
# CoinGecko categories to pull. Any category whose name matches CATEGORY_AUTO is added too,
# so new issuer categories are picked up without editing this list.
COINGECKO_CATEGORIES = [
    "tokenized-stock",
    "xstocks-ecosystem",
    "ondo-tokenized-assets",
    "bstocks-ecosystem",
]
CATEGORY_AUTO = r"tokeni[sz]ed (stock|equit|etf)|\bxstocks?\b|\bbstocks?\b|ondo tokenized|stock token"
CATEGORY_FAMILY = [  # (regex on category id or name, family)
    (r"xstock", "xstocks"),
    (r"ondo", "ondo"),
    (r"bstock", "bstocks"),
    (r"robinhood", "robinhood"),
    (r"dinari", "dinari"),
    (r"backed", "xstocks"),
]
# (family, regex on coin name, symbol suffix that the issuer appends to the ticker)
NAME_FAMILY = [
    ("xstocks", r"\bxstocks?\b", "X"),
    ("ondo", r"\bondo tokeni[sz]ed\b", "ON"),
    ("bstocks", r"\bbstocks?\b", "B"),
    ("robinhood", r"\brobinhood\b", ""),
    ("coinbase", r"\bcoinbase\b", "C"),
    ("dinari", r"\bdinari\b", ""),
    ("backpack", r"\bbackpack\b", ""),
]
FAMILY_SUFFIX = {f: s for f, _, s in NAME_FAMILY}

# --- Venues -----------------------------------------------------------------------------
CEX_SOURCES = ["binance", "kraken"]
DEX_NETWORKS = ["eth", "base"]  # GeckoTerminal ids
BAR_SOURCES = (*CEX_SOURCES, *("dex-" + net for net in DEX_NETWORKS), "stocks")
VENUE_LABELS = {
    "binance": "Binance", "kraken": "Kraken",
    "dex-eth": "Trading pools on Ethereum", "dex-base": "Trading pools on Base",
    "dex": "On-chain trading pools (GeckoTerminal)", "stocks": "US stocks (Alpaca, consolidated tape)",
}

USD_LIKE = {
    "USD", "USDT", "USDC", "USDG", "FDUSD", "PYUSD", "USD1", "USDE", "RLUSD", "DAI", "USDS",
    "TUSD", "USDP", "USDB", "USDTB", "USDX", "USDT0", "USDC.E", "USDBC", "AUSD",
}
MAJORS = {"ETH", "WETH", "SOL", "WSOL", "BNB", "WBNB", "BTC", "WBTC", "CBBTC", "CBETH", "STETH", "WSTETH"}
HEADLINE_COUNTERPARTS = ("usd", "major", "stock")   # "other" (mostly memecoins) is reported apart

PRICE_TOLERANCE = 0.20         # exchange price must be within 20% of CoinGecko's to accept a match
DEX_MIN_TOKEN_VOL = 5_000      # skip pool discovery for tokens below this 24h volume
DEX_MIN_POOL_VOL = 2_000       # keep stable/major/stock pools above this 24h volume
DEX_MIN_OTHER_POOL_VOL = 50_000  # memecoin-paired pools need more volume to be tracked
DEX_MAX_POOLS = int(os.environ.get("OFFHOURS_DEX_MAX_POOLS", 400))

# --- Closure-window price test -------------------------------------------------------
CLOSURE_MIN_HOURS = 20         # weekends and full-day holidays
CLOSURE_END_BUFFER_H = 2       # stop 2h early: CME equity futures reopen Sunday 18:00 ET
STALE_START_H = 2              # token needs a trade within 2h before the closure starts
STALE_END_H = 3                # and within 3h before the measurement ends
UNDERLYING_STALE_H = 4
CLOSURE_MIN_TOKEN_VOL = 10_000  # token volume during the closure, per stock
CLOSURE_MAX_ABS_LOGRET = 0.5   # drop observations beyond +/-50% (bad prints, not information)
CLOSURE_MIN_WINDOWS_PER_TICKER = 6

# Extra US tickers to pull from Alpaca, e.g. stocks without tokens as a control group
EXTRA_STOCKS = [t.strip().upper() for t in os.environ.get("OFFHOURS_EXTRA_STOCKS", "").split(",") if t.strip()]


# --- Keys (all free) ----------------------------------------------------------------------
ALPACA_KEY = os.environ.get("ALPACA_API_KEY_ID", "")
ALPACA_SECRET = os.environ.get("ALPACA_API_SECRET_KEY", "")
ALPACA_TRADING_BASE = os.environ.get("ALPACA_TRADING_BASE", "https://paper-api.alpaca.markets")
COINGECKO_DEMO_KEY = os.environ.get("COINGECKO_DEMO_API_KEY", "")
