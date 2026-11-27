# Tokenized Stock Observatory

**Live website:** [tianhaoyin1120.github.io/offhours](https://tianhaoyin1120.github.io/offhours/)

A daily tracker of tokenized US stock trading on Binance, Kraken, Gate and selected pools on Base, Ethereum, Solana and BNB Chain by US market session. Venue coverage starts on different dates, so product totals measure the trades observed by this dashboard. It answers three questions:

1. How much tokenized-stock volume trades outside regular hours, and how much while every US venue is shut?
2. How does that compare with the stocks' own volume, session by session?
3. How does trading volume compare across sources for the same securities on the same dates?

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

Run time depends on the number of indexed pools, available history and the public providers’ rate limits.

The first successful run backfills the configured history window (120 days for centralised
exchanges, 35 days for DEX pools, and 120 days for stocks). Later runs resume from per-market
cursors and fetch only new or recently amended bars, so the site keeps accumulating history.
The dashboard opens on all history and offers optional shorter chart views; its source table distinguishes sources
that are working, failing, or have not been run yet.

**On GitHub:**

1. Push this folder to a new repository. This public project uses a standard GitHub-hosted runner.
2. In Settings, Secrets and variables, Actions, add `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`
   and optionally `COINGECKO_DEMO_API_KEY`.
3. In Settings, Pages, set the source to GitHub Actions.
4. In Actions, open "Update data and site" and choose Run workflow. From then on it runs daily at
   07:20 UTC, commits the new data and redeploys. If GitHub ever pauses the schedule after a quiet
   spell, re-enable it from the Actions tab.

## Before you trust the numbers

The public dashboard uses archived responses from live sources. For a new installation, you can check an individual collector:

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
| Gate | Tagged xStocks and Ondo spot markets with a Gate underlying ticker | quote turnover, 30 minutes | up to the study backfill window |
| DefiLlama | Ondo Global Markets issuer-protocol buys and redemptions across Ethereum, BSC and Solana | USD per UTC day, reported separately | available protocol history |
| GeckoTerminal | Selected Coinbase, Ondo, bStocks and xStocks pools across four chains | pool OHLCV, 15 minutes | up to 35 days per new pool |
| Alpaca        | the stocks, consolidated tape                | v2 `stocks/bars`, `feed=sip`               | 120 days (the free plan reaches back to 2016) |
| CoinGecko     | token list, issuer, contract addresses       | categories, `coins/markets`, `coins/list`  | refreshed weekly |

Change the backfill with `OFFHOURS_BACKFILL_DAYS` and `OFFHOURS_DEX_BACKFILL_DAYS`.

**Which markets count.**

- **Tokens:** every coin in CoinGecko's tokenized-stock categories, plus any new category whose
  name matches (a new issuer is picked up without code changes). CoinGecko lists canonical
  contracts, which matters on chains full of copies squatting on tickers.
- **Exchange listings:** Binance’s product directory supplies the bStocks tag, and the spot market list supplies trading status and quote currency. Kraken’s tokenized-asset endpoint supplies canonical xStock pairs. Kraken also publishes SPV price units for the same trades. For the November 2026 coverage repair, earlier Kraken trades filled missing half-hours. Sandisk’s pre-split trade prices were divided by eight to match Kraken’s historical candles, preserving the dollars executed. At each timestamp the archive prefers the canonical xStock bar and retains an older SPV bar only when it is the sole observation.
- **DEX pools:** one side must be a registered token contract. Pools are classed by the other
  side: stablecoins, majors (ETH, SOL, BNB, BTC wrappers), another stock token, or other (mostly
  memecoins). Headline figures leave out "other", which is reported on its own, because a memecoin
  launched against a stock token says little about the stock.
- **Tickers:** these come from issuer naming (TSLAx, TSLAon, TSLAB, Robinhood's plain tickers)
  and are checked against Alpaca's list of US equities.

**Volume** is quote volume as each venue reports it. For Kraken that is bar VWAP times base volume,
for GeckoTerminal the pool's volume in USD, and for the stocks, shares times bar VWAP. Dollar
stablecoins count one for one.

## Comparing venues

The product and venue panel opens on all recorded trading. The [four-venue comparison](https://tianhaoyin1120.github.io/offhours/?sample=shared#venue-h) opens directly on shared tickers. The [seven-source comparison](https://tianhaoyin1120.github.io/offhours/?sample=expanded#venue-h) also requires Gate, Solana and BNB Chain on the same ticker-date. The original shared sample counts an underlying once per New York market date when all four token venues report positive dollar volume for that security. All four venue totals use those exact ticker-date pairs, even when trades occur in different sessions. The selector shows each security’s matched-date count. Off-hours share is off-hours dollars divided by total dollars for the selected observations. The other dashboard sections keep their own stated samples.

## Volume crossovers and two-way price discovery

Calendar totals include token-only dates. Matched totals use dates with positive volume on both sides, optionally in the same session. Daily crossover counts always use matched observations. The overnight view shows dated observations because the stock feed has incomplete coverage. Aggregate token-to-stock ratios use only underlying securities present in both feeds. The stock dollar totals can also include control securities.

The lead–lag panel pairs consecutive half hours for each ticker within one regular, pre-market or after-hours session. Returns are log(close/open). Each USD or major-quoted token market contributes its own return weighted by that interval’s dollar volume. Both quarters are required for 15-minute markets. Both directions use the same pairs, with at least 30 observations. These are descriptive Pearson correlations. Shared news, sparse trades and asynchronous last prints can affect them.

Weekly discovery checks the full Coinbase stock-token directory on Base and the Ondo stock and ETF directory on Ethereum. It queries DEX Screener for pairs of each contract and GeckoTerminal for top pools, then verifies the pool ID and both assets with GeckoTerminal. Pools against the configured stablecoin and major-asset addresses qualify regardless of daily volume. A pool counts once. The [issuer coverage CSV](https://tianhaoyin1120.github.io/offhours/data/issuer_coverage.csv) includes every listing, the pool count, observed dates and measured volume. Empty volume means no observation. The coverage table also compares tracked pools with all DEX Screener pools returning 24-hour volume for those issuer contracts. The numerator and denominator use the same index, network and weekly scan. Issuer creations, redemptions, other networks and unindexed venues are outside these totals.

On Solana, xStocks mints come from the paginated xStocks issuer API. On BNB Chain, Ondo addresses come from its chain-56 token list and bStocks addresses come from Binance’s BSC deposit-network directory. These searches cover securities already present in the four original feeds. For each product/network group the weekly scan selects up to 50 eligible indexed pools by one-day turnover. The candle provider verifies the token mint, side and quote. Past selections stay in the archive. Retrospective pool selection favors pools still active today. A full-day match allows different sessions at different venues; it does not establish uninterrupted market coverage.

The [Ondo protocol series](https://tianhaoyin1120.github.io/offhours/#ondo-h) reports executed issuer buy/redemption activity from DefiLlama’s open adapter. It has a different market scope and UTC clock from session-level secondary trading. Totals reconcile to the reported chains. Missing chain observations remain blank. Wallet transfers, circulating value, other exchanges and unindexed pools cannot be inferred from the session archive.

## Archived weekend-test data

The pipeline also exports closure observations for offline analysis. This test covers every closure of at least 20 hours (weekends and full-day holidays), and every stock
with at least $10,000 of token trading during that closure.

- **x** is the token's log return from the closure start (normally Friday 20:00 ET) to two hours
  before it ends (Sunday 18:00, when CME equity futures reopen). Each token market contributes its own
  return, weighted by its volume in the window. The venues column counts distinct exchange or pool-network sources. That way, tokens that accrue dividends, such as
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

Extended consolidated tape hours are scheduled for 6 December 2026. Comparing periods around that date requires accounting for changes in feed and venue coverage.

To compare Monday opens for stocks with and without liquid tokens, add control tickers with
`OFFHOURS_EXTRA_STOCKS=KO,PEP,...`. A repository variable of the same name works in Actions.

## Layout

```
pipeline/
  calendar.py        session clock and closure windows
  universe.py        token list, issuer, ticker, contract addresses
  sources/cex.py     Binance, Kraken and Gate
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
