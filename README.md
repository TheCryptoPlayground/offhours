# Tokenized Stock Observatory

**Live website:** [tianhaoyin1120.github.io/offhours](https://tianhaoyin1120.github.io/offhours/)

A daily tracker of tokenized US stock trading by US market session. It answers three questions:

1. How much tokenized-stock volume trades outside regular hours, and how much while every US venue is shut?
2. How does that compare with the stocks' own volume, session by session?
3. Do token prices set while US venues are closed anticipate where the stock opens?

Everything is free: public exchange APIs, GeckoTerminal, CoinGecko and Alpaca's free plan for data,
GitHub Actions to run it daily, GitHub Pages to host the site.

The [volume comparison](https://tianhaoyin1120.github.io/offhours/#compare-h) supports 7, 30 and 90 days,
all archived history, and inclusive custom dates. Each ticker requires at least one date with volume on both sides.
Matched-date and session views use only dates with positive volume on both sides. The default for every analysis is the full archive, including stock rankings, the weekly heatmap and the adjacent half-hour price comparison. The daily, product and individual stock charts have independent range controls.
[Data sources and APIs](https://tianhaoyin1120.github.io/offhours/#sources-h) lists upstream feeds and public JSON and CSV downloads.

The [off-hours trend](https://tianhaoyin1120.github.io/offhours/#offhours-h) shows off-hours token dollars as a percentage of consolidated stock dollars for the comparable underlying securities. The chart uses the project's US equity trading calendar, including early closes. Weekends and full holidays are excluded from the chart and its period totals. The denominator defaults to off-hours US stock volume and can include all sessions. The trailing five-session line divides the two volume sums over consecutive trading dates with recorded volume on both sides. Security names and business or fund descriptions are maintained in `config/security_profiles.csv`, using Nasdaq Trader’s symbol directory for listed identities.

## Preview in two minutes

```bash
pip install -r requirements.txt
python -m pipeline demo                # synthetic data, flagged with a banner on the page
python -m http.server -d site 8000     # open http://localhost:8000
```

## Run it for real

**Keys (free).** An [Alpaca](https://alpaca.markets) paper account gives the stocks' own volume and
checks token tickers against real US listings. A CoinGecko Demo key is optional and makes the weekly
token-list refresh about five times faster. Everything else needs no key.

**Locally:**

```bash
export ALPACA_API_KEY_ID=... ALPACA_API_SECRET_KEY=...
export COINGECKO_DEMO_API_KEY=...      # optional
python -m pipeline all                 # update, then build site/data
python -m pipeline status              # health of each source
```

Estimated run time is one to two hours the first time (backfill; GeckoTerminal's free limit of
30 calls a minute is the bottleneck) and around half an hour a day after that.

The first successful run backfills the configured history window (120 days for centralised
exchanges, 30 days for DEX pools, and 120 days for stocks). Later runs resume from per-market
cursors and fetch only new or recently amended bars, so the site keeps accumulating history.
The dashboard opens on all history and offers optional shorter chart views; its source table distinguishes sources
that are working, failing, or have not been run yet.

**On GitHub:**

1. Push this folder to a new repository. Public repositories get unlimited Actions minutes; a daily
   run also fits a private repository's 2,000 free minutes.
2. In Settings, Secrets and variables, Actions, add `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`
   and optionally `COINGECKO_DEMO_API_KEY`.
3. In Settings, Pages, set the source to GitHub Actions.
4. In Actions, open "Update data and site" and choose Run workflow. From then on it runs daily at
   07:20 UTC, commits the new data and redeploys. If GitHub ever pauses the schedule after a quiet
   spell, re-enable it from the Actions tab.

## Before you trust the numbers

The adapters follow each venue's published API and pass offline tests against those response
formats, but they were written without network access and have not yet run against the live
endpoints. Do the first run one venue at a time:

```bash
python -m pipeline update --only kraken --skip-stocks -v
python -m pipeline status
```

Then check `data/markets.csv` (which listings matched which token) and `data/universe/tokens.csv`
(issuer and ticker for each token), and fix stragglers in `config/overrides.csv`. Each exchange
adapter is about 30 lines in `pipeline/sources/cex.py`; a typical first-run fix is a renamed field
or status value.

**Geography.** GitHub's hosted runners are in the US, and some exchanges refuse US addresses (the
status table on the site shows HTTP 403 or 451). Binance already goes through its public
market-data mirror for this reason. If another venue fails that way, run the job from a machine in
a country the venue serves: register it as a self-hosted runner and set `runs-on: self-hosted` in
`.github/workflows/update.yml`, or run `python -m pipeline all` from cron and push `data/` and
`site/data/`.

## What it measures

Sessions are in New York time on the NYSE calendar (holidays, half days and DST come from
`exchange_calendars`):

| Session     | Hours                                                        |
|-------------|--------------------------------------------------------------|
| Regular     | 09:30 to 16:00 on trading days (13:00 on half days)          |
| Pre-market  | 04:00 to the open                                            |
| After-hours | close to four hours later (20:00, or 17:00 on half days)     |
| Overnight   | 20:00 to 04:00 on nights that lead into a trading day        |
| Closed      | everything else: weekends from Friday 20:00, holidays        |

Overnight trading from 20:00 counts toward the next trading day, the convention the consolidated
tape uses with 23/5 trading. Weekends and holidays count under their own dates. Bars are 30 minutes
(15 for DEX pools), so no bar straddles a session boundary.

## Sources

| Source        | Covers                                       | Endpoint                                   | History on first run |
|---------------|----------------------------------------------|--------------------------------------------|----------------------|
| Binance       | bStocks                                      | `data-api.binance.vision` klines           | 120 days |
| Kraken        | xStocks                                      | OHLC with `asset_class=tokenized_asset`    | 15 days (the API keeps only the latest 720 bars) |
| Bybit         | xStocks                                      | v5 `market/kline`                          | 120 days |
| Bitget        | xStocks, Ondo, Bitget's own tokens           | v2 spot `history-candles`                  | 120 days |
| Gate          | xStocks                                      | v4 spot `candlesticks`                     | 120 days |
| OKX           | Ondo                                         | v5 `market/history-candles`                | 120 days |
| GeckoTerminal | Selected Coinbase stock tokens on Base and Ondo stock tokens on Ethereum | pool OHLCV, 15 minutes | up to 30 days |
| Alpaca        | the stocks, consolidated tape                | v2 `stocks/bars`, `feed=sip`               | 120 days (the free plan reaches back to 2016) |
| CoinGecko     | token list, issuer, contract addresses       | categories, `coins/markets`, `coins/list`  | refreshed weekly |

Change the backfill with `OFFHOURS_BACKFILL_DAYS` and `OFFHOURS_DEX_BACKFILL_DAYS`.

**Which markets count.**

- **Tokens:** every coin in CoinGecko's tokenized-stock categories, plus any new category whose
  name matches (a new issuer is picked up without code changes). CoinGecko lists canonical
  contracts, which matters on chains full of copies squatting on tickers.
- **Exchange listings:** the base symbol matches a token, the quote is USD or a dollar
  stablecoin, and the price is within 20% of CoinGecko's.
- **DEX pools:** one side must be a registered token contract. Pools are classed by the other
  side: stablecoins, majors (ETH, SOL, BNB, BTC wrappers), another stock token, or other (mostly
  memecoins). Headline figures leave out "other", which is reported on its own, because a memecoin
  launched against a stock token says little about the stock.
- **Tickers:** these come from issuer naming (TSLAx, TSLAon, TSLAB, Robinhood's plain tickers)
  and are checked against Alpaca's list of US equities.

**Volume** is quote volume as each venue reports it. For Kraken that is bar VWAP times base volume,
for GeckoTerminal the pool's volume in USD, and for the stocks, shares times bar VWAP. Dollar
stablecoins count one for one.

## Volume crossovers and two-way price discovery

Calendar totals include token-only dates. Matched totals use dates with positive volume on both sides, optionally in the same session. Daily crossover counts always use matched observations. A small stock denominator or an incomplete feed can materially affect the ratio.

The lead–lag panel pairs consecutive half hours for each ticker within one regular, pre-market or after-hours session. Returns are log(close/open). Each USD or major-quoted token market contributes its own return weighted by that interval’s dollar volume. Both quarters are required for 15-minute markets. Both directions use the same pairs, with at least 30 observations. These are descriptive Pearson correlations. Shared news, sparse trades and asynchronous last prints can affect them.

The scheduled pool panel targets AAPL, AMZN, GOOGL, MSFT, NVDA, QQQ, SPY and TSLA. Weekly discovery selects up to two qualifying pools per token deployment from GeckoTerminal’s first volume-ranked page. Tokens need $5,000 in trailing daily volume and pools need $2,000. Stablecoin and major-asset pairs qualify. Only realized bar volume enters totals.

## The weekend test

The test covers every closure of at least 20 hours (weekends and full-day holidays), and every stock
with at least $10,000 of token trading during that closure.

- **x** is the token's log return from the closure start (normally Friday 20:00 ET) to two hours
  before it ends (Sunday 18:00, when CME equity futures reopen). Each venue contributes its own
  return, weighted by its volume in the window. That way, tokens that accrue dividends, such as
  Ondo's total-return design, do not distort the level.
- **y** is the stock's log return from its last trade before the closure to the next regular open.

The test reports:

- the slope of y on x, with standard errors clustered by closure;
- R²;
- the gap already priced, 1 - mean((y - x)²) / mean(y²);
- the share of gaps over 0.5% where the signs agree.

Observations beyond ±50% are dropped as bad prints. The test is descriptive: it shows whether token
prices carry information, not that tokens move the open.

## Known gaps

- **Perpetual futures** on stocks are left out, and they trade far more than spot tokens. Hyperliquid's
  equity perps are the natural next adapter.
- **Off-book trading** is missing: trading inside apps that never reaches a public book or chain,
  and issuer mint/redeem flows.
- **Wash trading** can inflate exchange-reported volume. Session shares within a venue are less
  exposed to it than volume levels compared across venues.
- **Overnight stock volume** is incomplete before 6 December 2026, because overnight ATS trades were
  not comprehensively on the consolidated tape. Occasional eligible or late prints can still appear.
- **Kraken history** starts 15 days before your first run.

## What the timing makes possible

US exchanges are scheduled to start night sessions on 6 December 2026. That allows a clean
difference-in-differences: compare the tokens' weekday-overnight share before and after, with the
weekend share as the control.

To compare Monday opens for stocks with and without liquid tokens, add control tickers with
`OFFHOURS_EXTRA_STOCKS=KO,PEP,...`. A repository variable of the same name works in Actions.

## Layout

```
pipeline/
  calendar.py        session clock and closure windows
  universe.py        token list, issuer, ticker, contract addresses
  sources/cex.py     Binance, Kraken, Bybit, Bitget, Gate, OKX
  sources/dex.py     GeckoTerminal pools and candles
  sources/stocks.py  Alpaca consolidated-tape bars
  update.py          one fetch run, with per-source status
  aggregate.py       bars to daily session facts and the week profile
  pricing.py         the weekend test
  site.py            writes site/data
  demo.py            synthetic preview data
site/                the static page, with Chart.js and the font vendored
data/                pipeline output: bars by source and UTC day, markets.csv, state.json
config/overrides.csv manual fixes to the token list
tests/               offline tests
```

Bars are stored as one Parquet file per source per UTC day, so git history grows roughly with the
data itself. Expect a few hundred MB a year at full coverage. If that gets too big for the
repository, move `data/` to its own branch.

## Tests

`python -m pytest` runs offline. It covers:

- the session clock (DST, holidays, half days);
- every adapter's parsing and paging against the documented response shapes;
- market matching and DEX pool classification;
- aggregation;
- the weekend test;
- the site build.

## Licences

The code is MIT. Chart.js (MIT) and Schibsted Grotesk (OFL) are vendored in `site/vendor` with their
licences.
