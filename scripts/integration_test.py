"""Integration test: end-to-end pipeline on ~15 real archetype names.

Archetypes and what they exercise:
- PTON  post-COVID bust w/ heavy SI     (spike history, survivability, SI)
- CVNA  famous turnaround, re-rated      (inflection + valuation row)
- W     cost-cut inflection candidate    (revenue-context haircut)
- CROX  audit-verified GM history        (long history, GM ladders)
- DECK  steady compounder                (should NOT top the long list)
- ETSY  decelerated growth + buybacks    (short-side decel/buybacks)
- KEY   bank                             (ROE-Ke substitute, masks)
- LNC   insurer                          (substitute set)
- BXP   REIT                             (FFO proxy, coverage tags)
- CLF   commodity cyclical               (commodity discount, cyclical tag)
- DJT   de-SPAC                          (structural break, zero/low revenue)
- RXRX  clinical-stage, minimal revenue  (zero-revenue nulling)
- YPF   FPI (20-F filer)                 (annual-only variant)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener import pipeline, market
from screener.universe import (extract_light_batch, hygiene_filter,
                               load_ticker_table, build_universe)

TICKERS = ["PTON", "CVNA", "W", "CROX", "DECK", "ETSY", "KEY", "LNC",
           "BXP", "CLF", "DJT", "RXRX", "YPF"]


def main():
    tick = hygiene_filter(load_ticker_table(pipeline.TICKERS_JSON))
    tick = tick[tick["ticker"].isin(TICKERS)].reset_index(drop=True)
    print(f"ticker rows: {len(tick)} -> {sorted(tick['ticker'])}")

    light = extract_light_batch(tick["cik"].tolist(), pipeline.FACTS_ZIP,
                                pipeline.SUBS_ZIP, workers=6)
    closes = market.chunked_download(tick["ticker"].tolist(), period="5d")
    uni = build_universe(None, market.last_closes(closes), light, tick)
    print(f"universe after band/hygiene: {len(uni)} -> {sorted(uni['ticker'])}")
    print(uni[["ticker", "sic", "fin_sector", "is_commodity", "annual_only",
               "is_despac_name", "mktcap_build"]].to_string(index=False))

    res = pipeline.run(universe_override=uni, workers=6)
    scored = res["scored"]
    print(f"\nscored {res['scored_n']} names, {res['errors_n']} errors, "
          f"{res['n_tearsheets']} tear sheets")
    cols = ["ticker", "long_composite", "short_composite",
            "bucket_turnaround", "bucket_moat", "inflection_score",
            "peak_score", "cov_long", "cov_short", "revenue_context",
            "zero_low_revenue", "false_moat_collision", "contested"]
    print(scored[[c for c in cols if c in scored.columns]]
          .sort_values("long_composite", ascending=False)
          .to_string(index=False))
    print("\ndiagnostics:", res["diagnostics"])
    print("outputs:", res["paths"])


if __name__ == "__main__":
    main()
