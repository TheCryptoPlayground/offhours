"""Which tokens are stock tokens, who issued them, which US ticker they track, and where they live.

Source of truth is CoinGecko's curated categories (free; a free Demo key raises the rate limit).
CoinGecko lists canonical contracts, which matters on chains full of ticker-squatting copies.
The ticker comes from the issuer's naming convention (TSLAx, TSLAon, TSLAB, ...) and is
checked against Alpaca's list of US equities when keys are set. config/overrides.csv wins
over both.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

import pandas as pd

from . import config as C
from .http import HTTP
from .sources.dex import GeckoTerminal, norm_addr
from .sources.stocks import Alpaca

log = logging.getLogger("offhours")
CG = "https://api.coingecko.com/api/v3"


class CoinGecko:
    def __init__(self, http: HTTP):
        self.http = http
        self.headers = {"x-cg-demo-api-key": C.COINGECKO_DEMO_KEY} if C.COINGECKO_DEMO_KEY else {}
        self.pace = 2.2 if C.COINGECKO_DEMO_KEY else 12.0

    def get(self, path: str, **params):
        return self.http.get(CG + path, params=params, headers=self.headers, pace=self.pace)

    def categories(self) -> dict[str, str]:
        return {c["category_id"]: c["name"] for c in self.get("/coins/categories/list")}

    def markets(self, category: str) -> list[dict]:
        out = []
        for page in range(1, 40):
            rows = self.get("/coins/markets", vs_currency="usd", category=category, per_page=250,
                            page=page, order="market_cap_desc")
            out += rows
            if len(rows) < 250:
                break
        return out

    def platforms(self) -> dict[str, dict]:
        return {c["id"]: (c.get("platforms") or {}) for c in self.get("/coins/list", include_platform="true")}


def _dir():
    d = C.DATA / "universe"
    d.mkdir(parents=True, exist_ok=True)
    return d


def category_family(cat_id: str, name: str) -> str:
    for pattern, fam in C.CATEGORY_FAMILY:
        if re.search(pattern, f"{cat_id} {name}", re.I):
            return fam
    return ""


def name_family(name: str) -> str:
    for fam, pattern, _ in C.NAME_FAMILY:
        if re.search(pattern, name or "", re.I):
            return fam
    return ""


def derive_ticker(symbol: str, family: str, valid: set[str] | None) -> str:
    s = (symbol or "").upper().replace("-", ".")
    suffix = C.FAMILY_SUFFIX.get(family, "")
    cands = []
    if suffix and s.endswith(suffix) and len(s) > len(suffix):
        cands.append(s[: -len(suffix)])
    cands.append(s)
    if family == "other":  # unknown issuer: try each known suffix
        cands += [s[: -len(x)] for x in C.FAMILY_SUFFIX.values() if x and s.endswith(x) and len(s) > len(x)]
    if valid is None:
        return cands[0]
    return next((c for c in cands if c in valid), "")


def load_overrides() -> dict[str, dict]:
    if not C.OVERRIDES.exists():
        return {}
    df = pd.read_csv(C.OVERRIDES, comment="#", dtype=str).fillna("")
    return {r["cg_id"]: r for r in df.to_dict("records") if r.get("cg_id")}


def map_token(coin: dict, valid: set[str] | None, overrides: dict[str, dict]) -> tuple[str, str, bool]:
    ov = overrides.get(coin["cg_id"], {})
    fams = sorted(coin["families"])
    family = ov.get("family") or (fams[0] if len(fams) == 1 else "") or name_family(coin["name"]) or "other"
    if ov.get("underlying"):
        return family, ov["underlying"].upper(), ov.get("exclude", "") in ("1", "true", "yes")
    return family, derive_ticker(coin["symbol"], family, valid), ov.get("exclude", "") in ("1", "true", "yes")


def build(http: HTTP) -> dict:
    cg = CoinGecko(http)
    known = cg.categories()
    missing = [c for c in C.COINGECKO_CATEGORIES if c not in known]
    if missing:
        log.warning("CoinGecko has no categories %s; skipping them", missing)
    cats = [c for c in C.COINGECKO_CATEGORIES if c in known]
    cats += sorted(cid for cid, name in known.items() if re.search(C.CATEGORY_AUTO, name, re.I) and cid not in cats)
    log.info("universe: categories %s", cats)

    coins: dict[str, dict] = {}
    for cat in cats:
        fam = category_family(cat, known[cat])
        for r in cg.markets(cat):
            c = coins.setdefault(r["id"], dict(cg_id=r["id"], symbol=r.get("symbol", ""), name=r.get("name", ""),
                                               price=r.get("current_price"), vol24=r.get("total_volume") or 0.0,
                                               mcap=r.get("market_cap") or 0.0, families=set(), categories=set()))
            c["categories"].add(cat)
            if fam:
                c["families"].add(fam)
    log.info("universe: %d tokens", len(coins))

    platforms = cg.platforms()
    netmap = GeckoTerminal(http).networks()
    alpaca = Alpaca(http)
    valid = None
    if alpaca.enabled:
        try:
            valid = alpaca.assets()
        except Exception as e:
            log.warning("Alpaca asset list failed (%s); tickers are unchecked", e)
    overrides = load_overrides()

    rows, addrs = [], []
    for cid, c in coins.items():
        family, under, excluded = map_token(c, valid, overrides)
        if excluded:
            continue
        rows.append(dict(cg_id=cid, symbol=c["symbol"].upper(), name=c["name"], family=family, underlying=under,
                         price=c["price"], vol24=c["vol24"], mcap=c["mcap"], categories=";".join(sorted(c["categories"]))))
        for plat, addr in (platforms.get(cid) or {}).items():
            if addr and plat in netmap:
                addrs.append(dict(cg_id=cid, platform=plat, network=netmap[plat], address=norm_addr(addr)))
    tokens = pd.DataFrame(rows)
    addresses = pd.DataFrame(addrs, columns=["cg_id", "platform", "network", "address"])
    meta = dict(fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), categories=cats,
                missing_categories=missing, tickers_checked=valid is not None,
                tokens=len(tokens), mapped=int((tokens.underlying != "").sum()) if len(tokens) else 0,
                coingecko_vol24=float(tokens.vol24.sum()) if len(tokens) else 0.0)
    save(tokens, addresses, meta)
    return dict(tokens=tokens, addresses=addresses, meta=meta)


def save(tokens: pd.DataFrame, addresses: pd.DataFrame, meta: dict) -> None:
    d = _dir()
    tokens.to_csv(d / "tokens.csv", index=False)
    addresses.to_csv(d / "addresses.csv", index=False)
    (d / "meta.json").write_text(json.dumps(meta, indent=1))


def load() -> dict | None:
    d = _dir()
    if not (d / "tokens.csv").exists():
        return None
    tokens = pd.read_csv(d / "tokens.csv", dtype={"underlying": str, "symbol": str}, keep_default_na=False)
    tokens["price"] = pd.to_numeric(tokens["price"], errors="coerce")
    tokens["vol24"] = pd.to_numeric(tokens["vol24"], errors="coerce").fillna(0.0)
    addresses = pd.read_csv(d / "addresses.csv", dtype=str, keep_default_na=False)
    meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
    return dict(tokens=tokens, addresses=addresses, meta=meta)


def load_or_build(http: HTTP, force: bool = False) -> dict:
    current = load()
    if current and not force:
        fetched = pd.Timestamp(current["meta"].get("fetched_at", "1970-01-01T00:00:00+00:00"))
        if pd.Timestamp.now(tz="UTC") - fetched < pd.Timedelta(days=C.UNIVERSE_MAX_AGE_DAYS):
            return current
    try:
        return build(http)
    except Exception as e:
        if current:
            log.warning("universe refresh failed (%s); using the saved one", e)
            return current
        raise
