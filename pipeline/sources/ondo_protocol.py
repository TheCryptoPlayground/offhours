"""Ondo issuer-market activity from the public DefiLlama protocol series."""
import math
from datetime import datetime, timezone

import pandas as pd

URL = "https://api.llama.fi/summary/dexs/ondo-global-markets"


def protocol_days(payload, now):
    if payload.get("slug") != "ondo-global-markets":
        raise ValueError("Unexpected protocol identity")
    by_chain = dict(payload["totalDataChartBreakdown"])
    rows = []
    for ts, amount in payload["totalDataChart"]:
        # DefiLlama day labels are UTC midnight. Exclude an unfinished UTC day.
        if ts % 86400 != 0:
            raise ValueError("Unexpected protocol day boundary")
        if ts + 86400 > now:
            continue
        amounts = {chain: sum(products.values()) for chain, products in by_chain[ts].items()}
        if (set(amounts) - {"Ethereum", "BSC", "Solana"} or
                any(not math.isfinite(v) or v < 0 for v in [amount, *amounts.values()]) or
                not math.isclose(amount, sum(amounts.values()), abs_tol=2)):
            raise ValueError("Unreconciled Ondo chain breakdown")
        rows.append(dict(utc_date=datetime.fromtimestamp(ts, timezone.utc).date().isoformat(),
                         total_usd=amount, ethereum_usd=amounts.get("Ethereum"), bsc_usd=amounts.get("BSC"),
                         solana_usd=amounts.get("Solana")))
    result = pd.DataFrame(rows)
    if result.empty or result.utc_date.duplicated().any():
        raise ValueError("Missing or duplicate Ondo protocol dates")
    return result.sort_values("utc_date")
