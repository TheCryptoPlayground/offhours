"""python -m pipeline <command>

    update   pull new bars from every source (add --only binance,dex to limit)
    build    write site/data from what is on disk
    all      update, then build
    demo     synthetic data for previewing the site (not market data)
    status   print per-source status from the last run
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from . import config as C


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["update", "build", "all", "demo", "status"])
    ap.add_argument("--only", default="", help="comma-separated sources, e.g. binance,kraken,dex,stocks")
    ap.add_argument("--refresh-universe", action="store_true", help="re-pull the token list from CoinGecko")
    ap.add_argument("--rediscover", action="store_true", help="re-scan DEX pools now")
    ap.add_argument("--skip-dex", action="store_true")
    ap.add_argument("--skip-stocks", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    only = [s for s in a.only.split(",") if s] or None

    if a.command == "demo":
        from . import demo, site
        demo.generate()
        site.build(demo=True)
        print("Demo data written to site/data (synthetic, flagged on the page). Preview: python -m http.server -d site")
        return 0
    if a.command in ("update", "all"):
        from . import update
        update.run(only=only, refresh_universe=a.refresh_universe, rediscover=a.rediscover,
                   skip_dex=a.skip_dex, skip_stocks=a.skip_stocks)
    if a.command in ("build", "all"):
        from . import site
        site.build()
    if a.command == "status":
        from . import store
        st = store.load_state()
        for name, s in sorted(st.get("sources", {}).items()):
            flag = "ok " if s.get("ok") else "ERR"
            print(f"{flag} {name:14} markets={s.get('markets', '-'):>5} rows={s.get('rows', '-'):>8} "
                  f"errors={s.get('errors', 0):>4} {s.get('error') or s.get('last_error') or ''}")
        print(json.dumps(st.get("last_run", {})))
    return 0


if __name__ == "__main__":
    sys.exit(main())
